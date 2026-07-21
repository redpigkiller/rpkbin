"""HIR → LIR lowering pass.

This module translates a ``HFunction`` (structured HIR with types) into a
``lir.Function`` (flat basic-blocks, expression-tree LIR, no types).

The main entry point is :func:`lower_function`.

Supported:

* All scalar expression nodes: HConst, HVar, HBinOp, HCmp, HCast,
  HExtract, HConcat.
* HAssign → lir.Assign.
* HIf (with elif chains and else) → BrIf + multiple blocks + merge block.
* HFor (fixed-count, body may read the loop variable) →  counter block + body block + step block + BrCmp("ne", counter, Const(0)).
* HReturn (single value) → lir.Return.
* HReturn (multiple values) → lir.MultiReturn.
* HInlineAsm → emitted as a raw-text Assign to a sentinel var.
* HCall (value expression) → lir.Assign(ret_var, lir.Call(name, args_lir)).  ``lir.Call`` is
  defined in ``lir.py``.
* HExprStmt(HCall(...)) → lir.CallStmt. Any return value is discarded; no
  dummy temp or return register is introduced.
  HCallAssign → lir.CallAssign.

* HWhile → while_test block with BrIf(cond) + while_body + while_exit blocks.
  HBreak inside HWhile jumps to while_exit.  HContinue jumps to while_test.
* HPoll → poll_body block + poll_check block with BrIf(cond) → poll_exit / poll_body.
  HContinue inside HPoll jumps to poll_check (re-evaluates condition before
  next iteration).
* HBreak → Jump(exit_label); requires enclosing loop.
* HContinue → Jump(test_label); requires enclosing loop.
* HLoad → lir.MemLoad node (volatile=True).  When HLoad appears inside a
  larger expression (e.g. ``HLoad(ptr) + 1``), lowering emits the MemLoad
  into a temporary ``Assign`` *before* the expression that uses it, ensuring
  correct evaluation order.
* HStore → lir.MemStore statement (volatile=True).  Store order is preserved
  relative to surrounding statements; the rewrite pass never removes or
  reorders MemStore.
* HBitSet(value=1) → lir.BitOp("set") statement.
* HBitSet(value=0) → lir.BitOp("clr") statement.
* HBitTest in condition → lir.BitOp("test") expression.
* HLogical("and"/"or") → short-circuit branch-chain via ``_lower_cond_branch``.
* HNot(cond) → label-swap delegation via ``_lower_cond_branch``.

Not supported:

* UInt(32), SInt(32) operands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from . import lir
from .hir import (
    HAssign,
    HBinOp,
    HBitSet,
    HBitTest,
    HBreak,
    HCall,
    HCallAssign,
    HCast,
    HCmp,
    HConcat,
    HConst,
    HContinue,
    HExit,
    HExprStmt,
    HExtract,
    HFor,
    HFragment,
    HFragmentBinding,
    HFunction,
    HIf,
    HInlineAsm,
    HInsert,
    HLoad,
    HLogical,
    HModule,
    HType,
    HNot,
    HPoll,
    HReturn,
    HStore,
    HSymbolAddr,
    HVar,
    HWhile,
    SInt,
    UInt,
    Void,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lower_function(hfunc: HFunction) -> lir.Function:
    """Translate a ``HFunction`` to a ``lir.Function``.

    Raises ``NotImplementedError`` for HIR constructs not yet supported.
    Raises ``ValueError`` if the produced LIR fails structural validation.
    """
    lowerer = _Lowerer(hfunc)
    return lowerer.lower()



# ---------------------------------------------------------------------------
# Internal block builder
# ---------------------------------------------------------------------------

@dataclass
class _Block:
    """Mutable block under construction."""
    label: str
    stmts: List[lir.Assign] = field(default_factory=list)
    terminator: lir.Terminator | None = None

    def seal(self, term: lir.Terminator) -> "_Block":
        self.terminator = term
        return self

    def to_lir(self) -> lir.Block:
        assert self.terminator is not None, f"block {self.label!r} has no terminator"
        return lir.Block(
            label=self.label,
            statements=tuple(self.stmts),
            terminator=self.terminator,
        )


# ---------------------------------------------------------------------------
# Lowerer implementation
# ---------------------------------------------------------------------------

class _Lowerer:
    def __init__(self, hfunc: HFunction | None):
        self._hfunc = hfunc
        self._blocks: List[_Block] = []
        self._current: _Block | None = None
        self._counter: dict[str, int] = {}
        # Stacks for break/continue targets in enclosing loops.
        # Each entry is (exit_label, continue_label).
        self._loop_stack: List[Tuple[str, str]] = []
        # Monotonic counter for generating unique temp-variable names.
        self._fresh_counter: int = 0

    def _fresh_id(self) -> int:
        """Return a unique integer for naming temporary variables."""
        n = self._fresh_counter
        self._fresh_counter += 1
        return n

    def _materialize(self, expr: lir.Expr) -> lir.Expr:
        if isinstance(expr, (lir.Const, lir.Var, lir.VReg, lir.SymbolAddr)):
            return expr
        temp = lir.Var(f"__tmp_{self._fresh_id()}", getattr(expr, "width", 8))
        self._emit(lir.Assign(temp, expr))
        return temp

    def _snapshot(self, expr: lir.Expr) -> lir.Expr:
        if isinstance(expr, (lir.Const, lir.SymbolAddr)):
            return expr
        temp = lir.Var(f"__tmp_{self._fresh_id()}", _expr_width_lir(expr))
        self._emit(lir.Assign(temp, expr))
        return temp

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    def lower(self) -> lir.Function:
        params = tuple(
            lir.Var(p.name, _width(p.ty)) for p in self._hfunc.params
        )

        # Start entry block
        self._push_block("entry")

        # Lower body statements
        self._lower_stmts(self._hfunc.body)

        # If current block still has no terminator (fall-through), add a default.
        if self._current is not None and self._current.terminator is None:
            self._current.seal(lir.Return(lir.Const(0)))

        # Remove unreachable blocks (e.g. merge/default-return blocks after
        # all-terminating branches) using the same traversal as fragments.
        reachable = self._compute_reachable(self._blocks)
        blocks = tuple(b.to_lir() for b in reachable)

        func = lir.Function(
            name=self._hfunc.name,
            params=params,
            blocks=blocks,
        )
        lir.validate_function(func)
        return func

    # ------------------------------------------------------------------
    # Block management
    # ------------------------------------------------------------------

    def _push_block(self, base_label: str) -> _Block:
        """Finalize current block (if any) is caller's responsibility first."""
        label = self._unique_label(base_label)
        block = _Block(label=label)
        self._blocks.append(block)
        self._current = block
        return block

    def _unique_label(self, base: str) -> str:
        count = self._counter.get(base, 0)
        self._counter[base] = count + 1
        if count == 0 and base == "entry":
            return "entry"  # keep first entry label clean
        return f"{base}_{count}" if count > 0 else base

    def _current_block(self) -> _Block:
        assert self._current is not None
        return self._current

    def _emit(self, stmt: lir.Stmt) -> None:
        self._current_block().stmts.append(stmt)

    def _emit_stmt(self, stmt) -> None:
        """Emit a non-Assign statement (MemStore, BitOp(set/clr))."""
        self._current_block().stmts.append(stmt)

    def _seal_current(self, term: lir.Terminator) -> None:
        self._current_block().seal(term)

    # ------------------------------------------------------------------
    # Reachability
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_reachable(blocks: list[_Block]) -> list[_Block]:
        if not blocks:
            return []
        label_map: dict[str, _Block] = {b.label: b for b in blocks}
        reachable: set[str] = set()
        worklist = [blocks[0].label]
        while worklist:
            label = worklist.pop()
            if label in reachable:
                continue
            reachable.add(label)
            block = label_map.get(label)
            if block is None or block.terminator is None:
                continue
            term = block.terminator
            if isinstance(term, lir.Jump):
                worklist.append(term.label)
            elif isinstance(term, (lir.BrIf, lir.BrCmp)):
                worklist.append(term.true_label)
                worklist.append(term.false_label)
            # Return / MultiReturn / FragmentExit are leaf terminators.
        return [b for b in blocks if b.label in reachable]

    # ------------------------------------------------------------------
    # Statement lowering
    # ------------------------------------------------------------------

    def _lower_stmts(self, stmts: tuple) -> None:
        for stmt in stmts:
            if self._current is not None and self._current.terminator is not None:
                # Dead code after terminator — skip
                break
            self._lower_stmt(stmt)

    def _lower_stmt(self, stmt) -> None:
        if isinstance(stmt, HAssign):
            target_lir = lir.Var(stmt.target.name, _width(stmt.target.ty))
            value_lir = self._lower_expr(stmt.value)
            self._emit(lir.Assign(target=target_lir, value=value_lir))

        elif isinstance(stmt, HIf):
            self._lower_if(stmt)

        elif isinstance(stmt, HFor):
            self._lower_for(stmt)

        elif isinstance(stmt, HReturn):
            self._lower_return(stmt)

        elif isinstance(stmt, HInlineAsm):
            # Passthrough: emit the raw assembly text as a lir.InlineAsmExpr
            # assigned to a sentinel Var. The instruction selector (toy_target
            # and real targets) must recognise InlineAsmExpr and emit the text
            # verbatim without any modification or prefix.
            sentinel = lir.Var("__asm__", 0)
            self._emit(lir.Assign(target=sentinel, value=lir.InlineAsmExpr(text=stmt.text)))

        elif isinstance(stmt, HExprStmt):
            # Function call as statement — lower the call, discard return.
            if isinstance(stmt.expr, HCall):
                self._lower_call_stmt(stmt.expr)
            else:
                raise TypeError(f"HExprStmt.expr must be HCall, got {type(stmt.expr).__name__}")

        elif isinstance(stmt, HWhile):
            self._lower_while(stmt)
        elif isinstance(stmt, HPoll):
            self._lower_poll(stmt)
        elif isinstance(stmt, HBreak):
            self._lower_break(stmt)
        elif isinstance(stmt, HContinue):
            self._lower_continue(stmt)
        elif isinstance(stmt, HBitSet):
            self._lower_bitset(stmt)
        elif isinstance(stmt, HStore):
            addr_lir = self._lower_expr(stmt.ptr_expr)
            val_lir = self._lower_expr(stmt.value_expr)
            self._emit_stmt(lir.MemStore(addr=addr_lir, value=val_lir))

        elif isinstance(stmt, HCallAssign):
            self._lower_call_assign(stmt)

        else:
            raise TypeError(f"unsupported HIR statement: {type(stmt).__name__}")

    # ------------------------------------------------------------------
    # HIf lowering
    # ------------------------------------------------------------------

    def _lower_if(self, stmt: HIf, depth: int = 0) -> None:
        """Lower HIf (with elif / else) into a block chain ending at a merge block.

        Block layout for ``if cond { then } elif cond2 { elif_body } else { else_body }``:

            current_block:
                ...existing stmts...
                BrIf(cond, then_label, elif_or_else_label)

            then_block:
                ...then stmts...
                Jump(merge_label)

            elif_test_block (if present):
                BrIf(cond2, elif_body_label, else_label)

            elif_body_block:
                ...elif stmts...
                Jump(merge_label)

            else_block (optional):
                ...else stmts...
                Jump(merge_label)

            merge_block:
                (continues here)
        """
        suffix = f"_{depth}" if depth > 0 else ""

        then_label = self._unique_label(f"if_then{suffix}")
        merge_label = self._unique_label(f"if_merge{suffix}")

        # Determine what comes after the then-block
        has_elif = bool(stmt.elif_branches)
        has_else = bool(stmt.else_body)

        if has_elif:
            next_label = self._unique_label(f"if_elif{suffix}")
        elif has_else:
            next_label = self._unique_label(f"if_else{suffix}")
        else:
            next_label = merge_label

        # Seal current block using cond-branch (supports short-circuit HLogical)
        self._lower_cond_branch(stmt.cond, then_label, next_label)

        # --- then block ---
        then_block = _Block(label=then_label)
        self._blocks.append(then_block)
        self._current = then_block
        self._lower_stmts(stmt.then_body)
        if self._current.terminator is None:
            self._seal_current(lir.Jump(merge_label))

        # --- elif branches ---
        for i, (elif_cond, elif_body) in enumerate(stmt.elif_branches):
            is_last_elif = (i == len(stmt.elif_branches) - 1)

            # Test block for this elif
            elif_test_block = _Block(label=next_label)
            self._blocks.append(elif_test_block)
            self._current = elif_test_block

            elif_body_label = self._unique_label(f"if_elif_body{suffix}_{i}")

            if is_last_elif and has_else:
                else_label = self._unique_label(f"if_else{suffix}")
                after_elif_label = else_label
            elif is_last_elif:
                after_elif_label = merge_label
            else:
                after_elif_label = self._unique_label(f"if_elif{suffix}_{i+1}")

            next_label = after_elif_label
            self._lower_cond_branch(elif_cond, elif_body_label, after_elif_label)

            # Body block for this elif
            elif_body_block = _Block(label=elif_body_label)
            self._blocks.append(elif_body_block)
            self._current = elif_body_block
            self._lower_stmts(elif_body)
            if self._current.terminator is None:
                self._seal_current(lir.Jump(merge_label))

        # --- else block ---
        if has_else:
            else_block = _Block(label=next_label)
            self._blocks.append(else_block)
            self._current = else_block
            self._lower_stmts(stmt.else_body)
            if self._current.terminator is None:
                self._seal_current(lir.Jump(merge_label))

        # --- merge block ---
        merge_block = _Block(label=merge_label)
        self._blocks.append(merge_block)
        self._current = merge_block

    # ------------------------------------------------------------------
    # HWhile lowering
    # ------------------------------------------------------------------

    def _lower_while(self, stmt: HWhile) -> None:
        """Lower HWhile into a test/body/exit block structure.

        Layout::

            (current block) → Jump(while_test)

            while_test:
                BrIf(cond) → while_body / while_exit

            while_body:
                [body stmts]
                Jump(while_test)

            while_exit:
                (continues here)
        """
        test_label = self._unique_label("while_test")
        body_label = self._unique_label("while_body")
        exit_label = self._unique_label("while_exit")

        # Seal current block jumping to the test
        self._seal_current(lir.Jump(test_label))

        # --- test block ---
        test_block = _Block(label=test_label)
        self._blocks.append(test_block)
        self._current = test_block
        self._lower_cond_branch(stmt.cond, body_label, exit_label)

        # --- body block ---
        body_block = _Block(label=body_label)
        self._blocks.append(body_block)
        self._current = body_block

        # Push loop context so break/continue inside know where to go
        self._loop_stack.append((exit_label, test_label))
        self._lower_stmts(stmt.body)
        self._loop_stack.pop()

        if self._current.terminator is None:
            self._seal_current(lir.Jump(test_label))

        # --- exit block ---
        exit_block = _Block(label=exit_label)
        self._blocks.append(exit_block)
        self._current = exit_block

    # ------------------------------------------------------------------
    # HPoll lowering
    # ------------------------------------------------------------------

    def _lower_poll(self, stmt: HPoll) -> None:
        """Lower HPoll into a body/check/exit block structure.

        The body executes at least once.  HContinue jumps to the
        condition-check point (poll_check) rather than directly back to
        poll_body, so that the condition is re-evaluated before the next
        iteration.

        Layout::

            (current block) → Jump(poll_body)

            poll_body:
                [body stmts]
                Jump(poll_check)

            poll_check:
                BrIf(cond) → poll_exit / poll_body

            poll_exit:
                (continues here)
        """
        body_label = self._unique_label("poll_body")
        check_label = self._unique_label("poll_check")
        exit_label = self._unique_label("poll_exit")

        # Seal current block jumping to poll_body (body runs first)
        self._seal_current(lir.Jump(body_label))

        # --- body block ---
        body_block = _Block(label=body_label)
        self._blocks.append(body_block)
        self._current = body_block

        # Push loop context (continue → poll_check, break → exit)
        self._loop_stack.append((exit_label, check_label))
        self._lower_stmts(stmt.body)
        self._loop_stack.pop()

        if self._current.terminator is None:
            self._seal_current(lir.Jump(check_label))

        # --- check block ---
        check_block = _Block(label=check_label)
        self._blocks.append(check_block)
        self._current = check_block
        self._lower_cond_branch(stmt.cond, exit_label, body_label)

        # --- exit block ---
        exit_block = _Block(label=exit_label)
        self._blocks.append(exit_block)
        self._current = exit_block

    # ------------------------------------------------------------------
    # HBreak / HContinue lowering
    # ------------------------------------------------------------------

    def _lower_break(self, stmt: HBreak) -> None:
        """Jump to the exit label of the nearest enclosing loop."""
        if not self._loop_stack:
            raise NotImplementedError(
                "HBreak outside of a loop is invalid (caught at lowering time)."
            )
        exit_label, _test_label = self._loop_stack[-1]
        self._seal_current(lir.Jump(exit_label))

    def _lower_continue(self, stmt: HContinue) -> None:
        """Jump to the continue target of the nearest enclosing loop."""
        if not self._loop_stack:
            raise NotImplementedError(
                "HContinue outside of a loop is invalid (caught at lowering time)."
            )
        _exit_label, continue_label = self._loop_stack[-1]
        self._seal_current(lir.Jump(continue_label))

    # ------------------------------------------------------------------
    # HBitSet lowering
    # ------------------------------------------------------------------

    def _lower_bitset(self, stmt: HBitSet) -> None:
        """Emit a BitOp("set") or BitOp("clr") statement."""
        var_lir = self._lower_expr(stmt.var)
        if stmt.value == 1:
            kind = "set"
        elif stmt.value == 0:
            kind = "clr"
        else:
            raise ValueError(f"HBitSet value must be 0 or 1, got {stmt.value!r}")
        self._emit_stmt(lir.BitOp(kind=kind, var=var_lir, bit_idx=stmt.bit_idx))

    # ------------------------------------------------------------------
    # HFor lowering
    # ------------------------------------------------------------------

    def _lower_for(self, stmt: HFor) -> None:
        """Lower a counted HFor into test/body/step/exit blocks."""
        var_name = stmt.var.name
        width = _width(stmt.var.ty)

        if not isinstance(stmt.init, HConst) or not isinstance(stmt.bound, HConst):
            raise NotImplementedError("HFor init and bound must be HConst")
        if stmt.init.value > stmt.bound.value:
            raise NotImplementedError(
                f"HFor bound must be >= init for loop variable '{var_name}'"
            )
        if _body_writes_var(stmt.body, stmt.var):
            raise NotImplementedError(
                f"HFor body writes loop variable '{var_name}'. "
                "This is not supported."
            )

        loop_var = lir.Var(var_name, width)
        counter_name = f"__counter_{var_name}"
        counter_var = lir.Var(counter_name, width)
        n_iters = stmt.bound.value - stmt.init.value
        if n_iters < 0:
            raise NotImplementedError(
                f"HFor bound must be >= init for loop variable '{var_name}'"
            )

        self._emit(lir.Assign(target=loop_var, value=lir.Const(stmt.init.value, width)))
        self._emit(lir.Assign(target=counter_var, value=lir.Const(n_iters, width)))

        loop_test_label = self._unique_label(f"for_test_{var_name}")
        loop_body_label = self._unique_label(f"for_body_{var_name}")
        loop_step_label = self._unique_label(f"for_step_{var_name}")
        loop_exit_label = self._unique_label(f"for_exit_{var_name}")

        self._seal_current(lir.Jump(loop_test_label))

        test_block = _Block(label=loop_test_label)
        self._blocks.append(test_block)
        self._current = test_block
        self._seal_current(lir.BrCmp(
            op="ne",
            left=counter_var,
            right=lir.Const(0, width),
            true_label=loop_body_label,
            false_label=loop_exit_label,
        ))

        body_block = _Block(label=loop_body_label)
        self._blocks.append(body_block)
        self._current = body_block

        self._loop_stack.append((loop_exit_label, loop_step_label))
        self._lower_stmts(stmt.body)
        self._loop_stack.pop()

        if self._current.terminator is None:
            self._seal_current(lir.Jump(loop_step_label))

        step_block = _Block(label=loop_step_label)
        self._blocks.append(step_block)
        self._current = step_block
        self._emit(lir.Assign(
            target=loop_var,
            value=lir.BinOp("add", loop_var, lir.Const(1, width), width),
        ))
        self._emit(lir.Assign(
            target=counter_var,
            value=lir.BinOp("sub", counter_var, lir.Const(1, width), width),
        ))
        self._seal_current(lir.Jump(loop_test_label))

        exit_block = _Block(label=loop_exit_label)
        self._blocks.append(exit_block)
        self._current = exit_block

    # ------------------------------------------------------------------
    # HReturn lowering
    # ------------------------------------------------------------------

    def _lower_return(self, stmt: HReturn) -> None:
        values_lir = tuple(self._lower_expr(v) for v in stmt.values)
        if len(values_lir) == 1:
            self._seal_current(lir.Return(values_lir[0]))
        else:
            self._seal_current(
                lir.MultiReturn(values=tuple(self._materialize(v) for v in values_lir))
            )

    # ------------------------------------------------------------------
    # HCall lowering (as expression producing a value)
    # ------------------------------------------------------------------

    def _lower_call_expr(self, call: HCall) -> lir.Expr:
        args_lir = tuple(self._materialize(self._lower_expr(a)) for a in call.args)
        call_expr = lir.Call(
            call.name, args_lir, call.arg_regs, call.return_regs, call.clobbers
        )
        ret_width = _width(call.return_ty) if not isinstance(call.return_ty, tuple) else 8
        ret_var = lir.Var(f"__ret_{call.name}_{self._fresh_id()}", ret_width)
        self._emit(lir.Assign(target=ret_var, value=call_expr))
        return ret_var

    def _lower_call_stmt(self, call: HCall) -> None:
        """Lower a call used as a statement (return value discarded).

        Emits a ``lir.CallStmt`` so the call is not wrapped in a dummy
        ``Assign`` to a scratch temp. Any return value is discarded; the
        call metadata is preserved on ``lir.Call`` unchanged.
        """
        args_lir = tuple(self._materialize(self._lower_expr(a)) for a in call.args)
        call_expr = lir.Call(
            name=call.name,
            args=args_lir,
            arg_regs=call.arg_regs,
            return_regs=call.return_regs,
            clobbers=call.clobbers,
        )
        self._emit_stmt(lir.CallStmt(call=call_expr))

    def _lower_call_assign(self, stmt: HCallAssign) -> None:
        """Lower HCallAssign — a single call with multiple return targets."""
        args_lir = tuple(
            self._materialize(self._lower_expr(a)) for a in stmt.call.args
        )
        call_expr = lir.Call(
            stmt.call.name,
            args_lir,
            stmt.call.arg_regs,
            stmt.call.return_regs,
            stmt.call.clobbers,
        )

        targets_lir: list[lir.Var | lir.VReg | None] = []
        for t in stmt.targets:
            if t is None:
                targets_lir.append(None)
            else:
                w = _width(t.ty)
                if t.reg_hint:
                    targets_lir.append(lir.VReg(name=t.name, width=w, hint=t.reg_hint))
                else:
                    targets_lir.append(lir.Var(name=t.name, width=w))

        self._emit_stmt(
            lir.CallAssign(
                targets=tuple(targets_lir),
                call=call_expr,
                abi_return_regs=(
                    tuple(call_expr.return_regs)
                    if call_expr.return_regs and all(call_expr.return_regs)
                    else ()
                ),
            )
        )

    # ------------------------------------------------------------------
    # Condition lowering
    # ------------------------------------------------------------------

    def _lower_cond(self, cond) -> lir.Expr:
        """Lower a leaf HCondExpr to a LIR expression usable in BrIf.

        Only handles ``HCmp`` and ``HBitTest``.  Compound conditions
        (``HLogical``, ``HNot``) must go through ``_lower_cond_branch``.
        """
        if isinstance(cond, HCmp):
            left = self._lower_expr(cond.left)
            if _hir_expr_contains_call(cond.right):
                left = self._snapshot(left)
            return lir.Cmp(
                op=cond.op,
                left=self._materialize(left),
                right=self._materialize(self._lower_expr(cond.right)),
                signed=cond.signed,
            )
        elif isinstance(cond, HBitTest):
            var_lir = self._lower_expr(cond.var)
            return lir.BitOp(kind="test", var=var_lir, bit_idx=cond.bit_idx)
        else:
            raise TypeError(
                f"_lower_cond expects a leaf condition (HCmp|HBitTest), "
                f"got {type(cond).__name__}"
            )

    def _lower_cond_branch(
        self, cond, true_label: str, false_label: str
    ) -> None:
        """Lower a condition expression and emit branch terminators.

        Implements short-circuit semantics for ``HLogical`` and ``HNot``.
        After this call the current block is sealed (and possibly intermediate
        blocks have been created).
        """
        if isinstance(cond, (HCmp, HBitTest)):
            cond_lir = self._lower_cond(cond)
            self._seal_current(
                lir.BrIf(cond=cond_lir, true_label=true_label, false_label=false_label)
            )
        elif isinstance(cond, HNot):
            self._lower_cond_branch(cond.expr, false_label, true_label)
        elif isinstance(cond, HLogical):
            if cond.op == "and":
                mid_label = self._unique_label("logical_mid")
                # left: true → mid (check right), false → false_label
                self._lower_cond_branch(cond.left, mid_label, false_label)
                # Create mid block
                mid_block = _Block(label=mid_label)
                self._blocks.append(mid_block)
                self._current = mid_block
                # right: true → true_label, false → false_label
                self._lower_cond_branch(cond.right, true_label, false_label)
            elif cond.op == "or":
                mid_label = self._unique_label("logical_mid")
                # left: true → true_label, false → mid (check right)
                self._lower_cond_branch(cond.left, true_label, mid_label)
                # Create mid block
                mid_block = _Block(label=mid_label)
                self._blocks.append(mid_block)
                self._current = mid_block
                # right: true → true_label, false → false_label
                self._lower_cond_branch(cond.right, true_label, false_label)
            else:
                raise ValueError(f"unknown HLogical op: {cond.op!r}")
        else:
            raise TypeError(f"unsupported condition node: {type(cond).__name__}")

    # ------------------------------------------------------------------
    # Expression lowering
    # ------------------------------------------------------------------

    def _lower_expr(self, expr) -> lir.Expr:
        if isinstance(expr, HConst):
            w = _width(expr.ty)
            if w == 32:
                raise NotImplementedError(
                    "UInt(32)/SInt(32) expressions are not supported."
                )
            return lir.Const(expr.value, w)

        elif isinstance(expr, HVar):
            w = _width(expr.ty)
            if w == 32:
                raise NotImplementedError(
                    "UInt(32)/SInt(32) variables are not supported."
                )
            if expr.reg_hint:
                return lir.VReg(name=expr.name, width=w, hint=expr.reg_hint)
            return lir.Var(expr.name, w)

        elif isinstance(expr, HBinOp):
            w = _width(expr.ty)
            if w == 32:
                raise NotImplementedError("32-bit HBinOp is not supported.")
            left = self._lower_expr(expr.left)
            right = self._lower_expr(expr.right)
            return lir.BinOp(expr.op, left, right, w)

        elif isinstance(expr, HCmp):
            return lir.Cmp(
                op=expr.op,
                left=self._lower_expr(expr.left),
                right=self._lower_expr(expr.right),
                signed=expr.signed,
            )

        elif isinstance(expr, HCast):
            return self._lower_cast(expr)

        elif isinstance(expr, HExtract):
            return self._lower_extract(expr)

        elif isinstance(expr, HConcat):
            return self._lower_concat(expr)

        elif isinstance(expr, HCall):
            return self._lower_call_expr(expr)

        elif isinstance(expr, HInsert):
            # Lower HInsert(dst, value, msb, lsb) using compile-time masks.
            #
            # field_width = msb - lsb + 1
            # field_mask  = ((1 << field_width) - 1) << lsb
            # keep_mask   = ((1 << dst_width) - 1) & ~field_mask
            #
            # Step 1: cleared = dst & keep_mask   (clear target bits in dst)
            # Step 2: shifted = value << lsb       (align value into position)
            # Step 3: result  = cleared | shifted
            dst_lir = self._lower_expr(expr.dst)
            val_lir = self._lower_expr(expr.value)
            dst_width = _expr_width_lir(dst_lir)

            field_width = expr.msb - expr.lsb + 1
            field_mask = ((1 << field_width) - 1) << expr.lsb
            all_ones = (1 << dst_width) - 1
            keep_mask = all_ones & (~field_mask & 0xFFFFFFFF)

            value_mask = (1 << field_width) - 1
            masked_value = self._materialize(
                lir.BinOp(
                    "and", val_lir, lir.Const(value_mask, dst_width), dst_width
                )
            )
            cleared = self._materialize(
                lir.BinOp(
                    "and", dst_lir, lir.Const(keep_mask, dst_width), dst_width
                )
            )
            shifted = self._materialize(
                lir.BinOp(
                    "shl",
                    masked_value,
                    lir.Const(expr.lsb, dst_width),
                    dst_width,
                )
            )
            return lir.BinOp("or", cleared, shifted, dst_width)
        elif isinstance(expr, HLoad):
            # Lower HLoad to MemLoad.
            addr_lir = self._lower_expr(expr.ptr_expr)
            load_node = lir.MemLoad(addr=addr_lir, width=_width(expr.ty))
            ret_var = lir.Var(f"__load_{self._fresh_id()}", _width(expr.ty))
            self._emit(lir.Assign(target=ret_var, value=load_node))  # type: ignore[arg-type]
            return ret_var
        elif isinstance(expr, HSymbolAddr):
            w = _width(expr.ty)
            return lir.SymbolAddr(name=expr.name, width=w)

        elif isinstance(expr, (HLogical, HNot, HBitTest)):
            raise TypeError(
                f"{type(expr).__name__} is a condition expression and cannot appear "
                "in a value position. Use it only as the condition of HIf/HWhile/HFor."
            )
        else:
            raise TypeError(f"unsupported HIR expression: {type(expr).__name__}")

    def _lower_cast(self, cast: HCast) -> lir.Expr:
        inner = self._lower_expr(cast.expr)
        kind = cast.kind
        to_w = _width(cast.to_ty)

        if kind == "low_byte":
            inner = self._materialize(inner)
            return lir.BinOp("and", inner, lir.Const(0xFF, to_w), to_w)
        elif kind == "high_byte":
            inner = self._materialize(inner)
            return lir.BinOp("shr", inner, lir.Const(8, to_w), to_w)
        elif kind == "u16_from":
            return lir.Extend("zext", inner, to_w)
        elif kind == "s16_from":
            return lir.Extend("sext", inner, to_w)
        elif kind in ("as_signed", "as_unsigned"):
            # Passthrough — bits unchanged, only type annotation changes.
            return inner
        else:
            raise ValueError(f"unknown HCast kind: {kind!r}")

    def _lower_extract(self, ext: HExtract) -> lir.Expr:
        inner = self._lower_expr(ext.expr)
        to_w = _width(ext.ty)
        inner_w = _expr_width_lir(inner)
        if inner_w < to_w:
            inner = lir.BinOp(
                "and",
                inner,
                lir.Const((1 << inner_w) - 1, to_w),
                to_w,
            )
        mask = (1 << (ext.msb - ext.lsb + 1)) - 1
        shifted = lir.BinOp("shr", inner, lir.Const(ext.lsb, to_w), to_w)
        return lir.BinOp("and", shifted, lir.Const(mask, to_w), to_w)

    def _lower_concat(self, cat: HConcat) -> lir.Expr:
        to_w = _width(cat.ty)
        hi_lir = self._lower_expr(cat.hi)
        lo_lir = self._lower_expr(cat.lo)
        # Determine lo width to compute shift amount
        lo_w = _expr_width_lir(lo_lir)
        shifted_hi = lir.BinOp("shl", hi_lir, lir.Const(lo_w, to_w), to_w)
        return lir.BinOp("or", shifted_hi, lo_lir, to_w)



# ---------------------------------------------------------------------------
# Fragment lowering
# ---------------------------------------------------------------------------

class _FragmentLowerer(_Lowerer):
    """Lowerer specialized for ``HFragment`` → ``lir.Fragment``.

    Reuses expression and statement lowering from ``_Lowerer``, but:
    * ``HExit`` seals the current block with ``lir.FragmentExit()``.
    * Never emits ``Return``, ``MultiReturn``, default return, prologue, or epilogue.
    * Binding ``HVar`` always lowers to ``VReg(name, width, hint=binding.reg)``.
    * Non-binding locals remain ``Var``.
    * Post-processes blocks to remove unreachable merge blocks from all-terminating
      ``HIf``.
    """

    def __init__(self, fragment: HFragment) -> None:
        super().__init__(hfunc=None)
        self._fragment = fragment
        self._bindings: dict[str, HFragmentBinding] = {
            b.name: b for b in fragment.bindings
        }

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    def lower(self) -> lir.Fragment:
        lir_bindings = tuple(
            lir.FragmentBinding(
                name=b.name,
                width=_width(b.ty),
                reg=b.reg,
                mode=b.mode,
            )
            for b in self._fragment.bindings
        )

        self._push_block("entry")
        self._lower_stmts(self._fragment.body)

        # Do NOT add a default Return — fragment must end with HExit on all paths

        # Remove unreachable blocks (produced by all-terminating if branches)
        reachable = self._compute_reachable(self._blocks)
        blocks = tuple(b.to_lir() for b in reachable)

        frag = lir.Fragment(
            name=self._fragment.name,
            bindings=lir_bindings,
            scratch_regs=self._fragment.scratch_regs,
            blocks=blocks,
        )
        lir.validate_fragment(frag)
        return frag

    # ------------------------------------------------------------------
    # Statement lowering overrides
    # ------------------------------------------------------------------

    def _lower_stmt(self, stmt) -> None:
        if isinstance(stmt, HExit):
            self._seal_current(lir.FragmentExit())
            return
        if isinstance(stmt, HReturn):
            raise NotImplementedError(
                "HReturn is not allowed inside HFragment"
            )
        if isinstance(stmt, (HWhile, HPoll, HFor, HBreak, HContinue)):
            raise NotImplementedError(
                f"{type(stmt).__name__} is not allowed inside HFragment"
            )
        if isinstance(stmt, HAssign):
            self._lower_assign_fragment(stmt)
            return
        if isinstance(stmt, HCallAssign):
            self._lower_call_assign_fragment(stmt)
            return
        # Everything else delegates to parent
        super()._lower_stmt(stmt)

    def _lower_assign_fragment(self, stmt: HAssign) -> None:
        target = stmt.target
        w = _width(target.ty)
        if target.name in self._bindings:
            b = self._bindings[target.name]
            target_lir = lir.VReg(name=target.name, width=w, hint=b.reg)
        elif target.reg_hint:
            target_lir = lir.VReg(name=target.name, width=w, hint=target.reg_hint)
        else:
            target_lir = lir.Var(name=target.name, width=w)
        value_lir = self._lower_expr(stmt.value)
        self._emit(lir.Assign(target=target_lir, value=value_lir))

    def _lower_call_assign_fragment(self, stmt: HCallAssign) -> None:
        args_lir = tuple(
            self._materialize(self._lower_expr(a)) for a in stmt.call.args
        )
        call_expr = lir.Call(
            stmt.call.name,
            args_lir,
            stmt.call.arg_regs,
            stmt.call.return_regs,
            stmt.call.clobbers,
        )
        targets_lir: list[lir.Var | lir.VReg | None] = []
        for t in stmt.targets:
            if t is None:
                targets_lir.append(None)
            else:
                w = _width(t.ty)
                if t.name in self._bindings:
                    b = self._bindings[t.name]
                    targets_lir.append(
                        lir.VReg(name=t.name, width=w, hint=b.reg)
                    )
                elif t.reg_hint:
                    targets_lir.append(
                        lir.VReg(name=t.name, width=w, hint=t.reg_hint)
                    )
                else:
                    targets_lir.append(lir.Var(name=t.name, width=w))
        self._emit_stmt(
            lir.CallAssign(
                targets=tuple(targets_lir),
                call=call_expr,
                abi_return_regs=(
                    tuple(call_expr.return_regs)
                    if call_expr.return_regs and all(call_expr.return_regs)
                    else ()
                ),
            )
        )

    # ------------------------------------------------------------------
    # Expression lowering override — binding HVars → pinned VReg
    # ------------------------------------------------------------------

    def _lower_expr(self, expr) -> lir.Expr:
        if isinstance(expr, HVar) and expr.name in self._bindings:
            w = _width(expr.ty)
            b = self._bindings[expr.name]
            return lir.VReg(name=expr.name, width=w, hint=b.reg)
        return super()._lower_expr(expr)

    # ------------------------------------------------------------------
    # Reachability — remove unreachable blocks
    # ------------------------------------------------------------------



def lower_fragment(hfragment: HFragment) -> lir.Fragment:
    """Translate an ``HFragment`` to a ``lir.Fragment``.

    Raises ``NotImplementedError`` for HIR constructs that are not supported
    inside fragments (loops, return).  Raises ``ValueError`` if the produced
    LIR fails structural validation.
    """
    lowerer = _FragmentLowerer(hfragment)
    return lowerer.lower()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _width(ty) -> int:
    """Return the bit-width of an HType."""
    if isinstance(ty, (UInt, SInt)):
        return ty.width
    if isinstance(ty, Void):
        return 0
    if isinstance(ty, tuple):
        # multi-return: not directly applicable
        return 0
    raise TypeError(f"unsupported HType: {ty!r}")


def _expr_width_lir(expr: lir.Expr) -> int:
    """Return the bit-width of a LIR expression (best-effort)."""
    if isinstance(expr, lir.Const):
        return expr.width
    if isinstance(expr, lir.Var):
        return expr.width
    if isinstance(expr, lir.VReg):
        return expr.width
    if isinstance(expr, lir.BinOp):
        return expr.width
    if isinstance(expr, lir.Extend):
        return expr.width
    if isinstance(expr, lir.Cmp):
        return 1
    if isinstance(expr, lir.MemLoad):
        return expr.width
    if isinstance(expr, lir.SymbolAddr):
        return expr.width
    return 8  # fallback


def _hir_expr_contains_call(expr) -> bool:
    if isinstance(expr, HCall):
        return True
    if isinstance(expr, (HBinOp, HCmp)):
        return _hir_expr_contains_call(expr.left) or _hir_expr_contains_call(expr.right)
    if isinstance(expr, (HCast, HExtract)):
        return _hir_expr_contains_call(expr.expr)
    if isinstance(expr, HConcat):
        return _hir_expr_contains_call(expr.hi) or _hir_expr_contains_call(expr.lo)
    if isinstance(expr, HInsert):
        return _hir_expr_contains_call(expr.dst) or _hir_expr_contains_call(expr.value)
    if isinstance(expr, HLoad):
        return _hir_expr_contains_call(expr.ptr_expr)
    return False


# ---------------------------------------------------------------------------
# Module-level lowering
# ---------------------------------------------------------------------------

def lower_module(module: HModule) -> lir.Module:
    """Lower an ``HModule`` to a ``lir.Module``.

    Each ``HFunction`` is lowered independently via ``lower_function``.
    Each ``HFragment`` is lowered independently via ``lower_fragment``.
    Extern function and external symbol metadata is converted to LIR-native
    declaration dataclasses.  No linking, relocation, or whole-program
    optimisation is performed.
    """

    def _return_widths(ty: HType | tuple) -> tuple[int, ...]:
        if isinstance(ty, tuple):
            return tuple(_width(t) for t in ty)
        elif isinstance(ty, Void):
            return ()
        else:
            return (_width(ty),)

    extern_return_regs = {
        efn.name: tuple(efn.return_regs) for efn in module.extern_functions if efn.return_regs
    }

    lir_funcs = tuple(
        _annotate_call_assign_abi_return_regs(lower_function(fn), extern_return_regs)
        for fn in module.functions
    )
    lir_fragments = tuple(
        _annotate_fragment_call_assign_abi_return_regs(lower_fragment(frag), extern_return_regs)
        for frag in module.fragments
    )
    ext_fn_decls = tuple(
        lir.ExternFunctionDecl(
            name=efn.name,
            param_widths=tuple(_width(p.ty) for p in efn.params),
            return_widths=_return_widths(efn.return_ty),
            clobbers=efn.clobbers,
        )
        for efn in module.extern_functions
    )
    sym_decls = tuple(
        lir.ExternalSymbolDecl(
            name=sym.name,
            address_width=_width(sym.address_ty),
            value_width=_width(sym.value_ty) if sym.value_ty is not None else None,
            volatile=sym.volatile,
        )
        for sym in module.external_symbols
    )
    return lir.Module(
        functions=lir_funcs,
        fragments=lir_fragments,
        extern_functions=ext_fn_decls,
        external_symbols=sym_decls,
    )


def _annotate_call_assign_abi_return_regs(
    func: lir.Function,
    extern_return_regs: dict[str, tuple[str, ...]],
) -> lir.Function:
    """Attach extern ABI return-register metadata to multi-return calls."""

    if not extern_return_regs:
        return func

    new_blocks: list[lir.Block] = []
    changed = False
    for block in func.blocks:
        new_statements: list[lir.Stmt] = []
        for stmt in block.statements:
            if isinstance(stmt, lir.CallAssign):
                abi_regs = extern_return_regs.get(stmt.call.name, ())
                if abi_regs and len(abi_regs) == len(stmt.targets) and stmt.abi_return_regs != abi_regs:
                    stmt = lir.CallAssign(
                        targets=stmt.targets,
                        call=stmt.call,
                        abi_return_regs=abi_regs,
                    )
                    changed = True
            new_statements.append(stmt)
        new_blocks.append(
            lir.Block(
                label=block.label,
                statements=tuple(new_statements),
                terminator=block.terminator,
            )
        )
    if not changed:
        return func
    return lir.Function(name=func.name, params=func.params, blocks=tuple(new_blocks))


def _annotate_fragment_call_assign_abi_return_regs(
    fragment: lir.Fragment,
    extern_return_regs: dict[str, tuple[str, ...]],
) -> lir.Fragment:
    """Attach extern ABI return-register metadata to fragment call sites."""

    if not extern_return_regs:
        return fragment

    new_blocks: list[lir.Block] = []
    changed = False
    for block in fragment.blocks:
        new_statements: list[lir.Stmt] = []
        for stmt in block.statements:
            if isinstance(stmt, lir.CallAssign):
                abi_regs = extern_return_regs.get(stmt.call.name, ())
                if abi_regs and len(abi_regs) == len(stmt.targets) and stmt.abi_return_regs != abi_regs:
                    stmt = lir.CallAssign(
                        targets=stmt.targets,
                        call=stmt.call,
                        abi_return_regs=abi_regs,
                    )
                    changed = True
            new_statements.append(stmt)
        new_blocks.append(
            lir.Block(
                label=block.label,
                statements=tuple(new_statements),
                terminator=block.terminator,
            )
        )
    if not changed:
        return fragment
    return lir.Fragment(
        name=fragment.name,
        bindings=fragment.bindings,
        scratch_regs=fragment.scratch_regs,
        blocks=tuple(new_blocks),
    )


def _body_writes_var(body: tuple, var: HVar) -> bool:
    for stmt in body:
        if _stmt_writes_var(stmt, var):
            return True
    return False


def _stmt_writes_var(stmt, var: HVar) -> bool:
    if isinstance(stmt, HAssign):
        return stmt.target.name == var.name
    if isinstance(stmt, HBitSet):
        return stmt.var.name == var.name
    if isinstance(stmt, HIf):
        return (
            _body_writes_var(stmt.then_body, var)
            or any(_body_writes_var(body, var) for _cond, body in stmt.elif_branches)
            or _body_writes_var(stmt.else_body, var)
        )
    if isinstance(stmt, (HWhile, HPoll)):
        return _body_writes_var(stmt.body, var)
    if isinstance(stmt, HFor):
        return stmt.var.name == var.name or _body_writes_var(stmt.body, var)
    return False
