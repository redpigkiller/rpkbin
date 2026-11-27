"""Source compilation and public result records."""

from dataclasses import dataclass, field, replace
from pathlib import Path
import ast
from .assemble import _arguments, _placement
from .config import _validate_config
from .emit import _render
from .errors import AllocationError, ConfigError, SourceError, UnsupportedError
from .expand import _Context
from .frames import _Builder, _concretize
from .ir import Imm
from .frontend import _parse
from .lower import _lower
from .layout import _layout, _relax
from .peephole import _peephole
from .lint_q import _findings
from .machine import _in_pool, _reported_clobbers
from .opt import _optimize
from .target import SlotLoc
from .widen import _widen_comparisons


@dataclass(frozen=True)
class FunctionResult:
    name: str
    bytes: int
    clobbers: tuple
    slots: tuple
    findings: tuple
    verified: object
    mir: tuple
    _ir: object = field(repr=False)
    _locations: dict = field(repr=False)
    _coverage: tuple = field(repr=False)
    _pool_contract: str | None = field(default=None, repr=False)
    _parks: int = field(default=0, repr=False)
    _push_depth: int | None = field(default=None, repr=False)
    _has_vreg_slots: bool = field(default=False, repr=False)


@dataclass(frozen=True)
class CompileResult:
    asm: str
    functions: dict

    def report(self):
        return {"functions": {name: {"bytes": fn.bytes, "clobbers": fn.clobbers,
                "slots": list(fn.slots), "findings": fn.findings, "verified": fn.verified,
                **({"pool_contract": fn._pool_contract} if fn._pool_contract is not None else {}),
                **({"parks": fn._parks, "push_depth": fn._push_depth} if fn._push_depth is not None else {})}
                for name, fn in self.functions.items()}}


def _compile_order(functions):
    pending = [name for name, fn in functions.items() if not fn._external]
    result = []
    while pending:
        ready = next((name for name in pending if all(i.meta["callee"] not in pending
                     for b in functions[name].blocks for i in b.instrs if i.op == "call")), None)
        if ready is None:
            raise SourceError("<source>:1: recursive call graph")
        pending.remove(ready)
        result.append(ready)
    return result


def _reject_pool_constants(function, pool):
    for block in function.blocks:
        for instruction in block.instrs:
            if instruction.op not in ("load", "store") or not isinstance(instruction.args[0], Imm):
                continue
            space, address = instruction.meta["space"], instruction.args[0].value
            if _in_pool(pool, space, address):
                low, high = pool[space]
                raise ConfigError(
                    f"{function.name}:{instruction.meta['line']}: {space}:{address} "
                    f"overlaps spill pool [{low}, {high}]")


def _compile_function(function, typed_function, target, config, interfaces, occupied, *, pool_private,
                      park_budget=0, callee_depths=None):
    from .verify import _stack_flow, _verify
    from .regalloc import _allocate

    name = function.name
    ctx = _Context(target, config.spill_pool, pool_private=pool_private)
    ctx.park_depth = park_budget
    ctx.callee_depths = callee_depths or {}
    locations = _allocate(function, ctx, interfaces)
    has_vreg_slots = any(isinstance(loc, SlotLoc) and loc.kind == "vreg" for loc in locations.values())
    order = [b.id for b in function.blocks]
    blocks = _Builder(function, ctx, locations, interfaces, order)._run()
    blocks, locations, slots = _concretize(function, blocks, locations, ctx, occupied)
    mir = _layout(function, blocks, locations, ctx, interfaces)
    if pool_private:
        mir = _peephole(mir, target, name, pool_private=True)
    else:
        mir = _peephole(mir, target, name)
    mir = _relax(mir, target, name)
    findings = _findings(mir, target, name, pool_private=pool_private)
    clobbers = _reported_clobbers(mir, target, function.abi, interfaces, pool_private=pool_private)
    fn = FunctionResult(name, sum(m.bytes for m in mir), clobbers, slots, findings, False,
                        mir, function, locations, (), "private" if pool_private else None,
                        _has_vreg_slots=has_vreg_slots)
    symbols, addresses, _, _ = _placement({name: fn}, 0)
    for index, instruction in enumerate(mir):
        if instruction.form is not None:
            _arguments(instruction, addresses[name, index], symbols, unresolved=True)
    if config.park_depth:
        depth, _, _ = _stack_flow(fn, ctx.callee_depths)
        if park_budget and depth > park_budget:
            raise UnsupportedError(f"{name}: park variant exceeds push budget {park_budget}")
        fn = replace(fn, _parks=sum(m.op == "push" and m._role == "park" for m in mir),
                     _push_depth=depth)
        coverage = _verify(fn, target, config, interfaces, pool_private=pool_private,
                           callee_depths=ctx.callee_depths)
    elif fn._pool_contract == "private":
        coverage = _verify(fn, target, config, interfaces, pool_private=True)
    else:
        coverage = _verify(fn, target, config, interfaces)
    has_asm = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "asm"
                  for n in ast.walk(typed_function.node))
    verified = "unverified: asm" if has_asm else True if not coverage else "partial: " + "; ".join(coverage)
    return replace(fn, verified=verified, _coverage=coverage)


def _compile_variant(function, typed, target, config, interfaces, occupied, *,
                     park_budget=0, callee_depths=None):
    options = dict(park_budget=park_budget, callee_depths=callee_depths)
    declaration = typed.functions[function.name]
    try:
        return _compile_function(function, declaration, target, config, interfaces,
                                 occupied.copy(), pool_private=False, **options)
    except AllocationError:
        if config.spill_pool:
            # Check accesses eliminated by O as well as addresses folded by O.
            _reject_pool_constants(_lower(typed)[function.name], config.spill_pool)
            _reject_pool_constants(function, config.spill_pool)
        try:
            return _compile_function(function, declaration, target, config, interfaces,
                                     occupied.copy(), pool_private=True, **options)
        except AllocationError as error:
            raise AllocationError(f"{error} [private-pool mode]") from error


def _select_variant(first, second, policy="smallest"):
    if first is None:
        return second
    if policy == "stack_first":
        return second if first._has_vreg_slots and second is not None else first
    if second is not None and second.bytes < first.bytes:
        return second
    return first


def compile_source(source, target, config, filename="<source>"):
    typed = _parse(source, target, filename)
    functions = _lower(typed)
    _validate_config(target, config, functions)
    for function in functions.values():
        if not function._external:
            _optimize(function)
            _widen_comparisons(function, target)
    interfaces = {name: dict(fn.abi) for name, fn in functions.items()}
    compiled, occupied = {}, set()
    for name in _compile_order(functions):
        function = functions[name]
        try_park = config.park_depth > 0 and config.park_policy != "slot_first"
        depths = {callee: result._push_depth for callee, result in compiled.items()} if config.park_depth else None
        first = second = error = None
        try:
            first = _compile_variant(function, typed, target, config, interfaces, occupied,
                                     callee_depths=depths)
        except (AllocationError, ConfigError, UnsupportedError) as exc:
            if not try_park:
                raise
            error = exc
        if try_park and (first is None or first._has_vreg_slots):
            try:
                second = _compile_variant(function, typed, target, config, interfaces, occupied,
                                          park_budget=config.park_depth, callee_depths=depths)
            except (AllocationError, ConfigError, UnsupportedError):
                pass
        fn = _select_variant(first, second, config.park_policy)
        if fn is None:
            raise error
        for space, address, count in fn.slots:
            occupied.update((space, cell) for cell in range(address, address + count))
        compiled[name] = fn
        interfaces[name]["clobbers"] = fn.clobbers
    ordered = {name: compiled[name] for name in functions if name in compiled}
    owners = {name: f"extern {name}" for name, function in functions.items() if function._external}
    for function in ordered.values():
        for instruction in function.mir:
            symbol = instruction._label
            if symbol is None:
                continue
            if symbol in owners:
                raise UnsupportedError(f"{function.name}: symbol {symbol!r} conflicts between {owners[symbol]} and function {function.name}")
            owners[symbol] = f"function {function.name}"
    assembly = []
    for fn in ordered.values():
        lines = []
        if config.comments:
            slots_text = " ".join(f"{s}:{a:#x}+{n}" for s, a, n in fn.slots) or "-"
            lines.append(f"{target.comment} fn {fn.name}: {fn.bytes} bytes; clobbers: {' '.join(fn.clobbers)}; slots: {slots_text}")
        lines.extend(_render(m, target) for m in fn.mir)
        assembly.append("\n".join(lines))
    return CompileResult("\n\n".join(assembly) + ("\n" if assembly else ""), ordered)


def compile_file(path, target, config):
    try:
        source=Path(path).read_text(encoding="utf-8")
    except (OSError,UnicodeError) as exc:
        raise SourceError(f"{path}:1: cannot read source: {exc}") from exc
    return compile_source(source,target,config,str(path))
