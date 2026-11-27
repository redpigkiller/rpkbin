"""Lower typed source into deterministic virtual-register control flow."""

import ast
from types import MappingProxyType

from .frontend import _Type
from .ir import Block, Function, Imm, Instr, VReg


_OPS = MappingProxyType({ast.Add: "add", ast.Sub: "sub", ast.BitAnd: "and", ast.BitOr: "or",
                         ast.BitXor: "xor", ast.LShift: "shl", ast.RShift: "shr"})
_CONDITIONS = MappingProxyType({ast.Eq: "eq", ast.NotEq: "ne", ast.Lt: "ltu", ast.LtE: "leu", ast.Gt: "gtu", ast.GtE: "geu"})


class _Lower:
    def __init__(self, typed, source):
        self.typed, self.source = typed, source
        self.function = Function(source.node.name, [], source.ret.width if source.ret else None, [], source.abi,
                                 _filename=typed.filename, _external=source.external)
        self.locals = {}
        for name, ty in source.params:
            value = self._new(ty.width, name)
            self.locals[name] = value
            self.function.params.append(value)
        self.current = self._block()
        self.current.instrs.append(Instr("entry", None, tuple(self.function.params)))
        self.return_value = None
        self.return_jumps = []
        self.loops = []

    def _new(self, width, name=None):
        value = VReg(len(self.function._vregs), width, name)
        self.function._vregs.append(value)
        return value

    def _block(self):
        block = Block(len(self.function.blocks), [])
        self.function.blocks.append(block)
        return block

    def _emit(self, op, args, width, node, dst=None, **meta):
        dst = dst or self._new(width)
        self.current.instrs.append(Instr(op, dst, tuple(args), {"line": getattr(node, "lineno", 1), **meta}))
        return dst

    def _copy(self, dst, src, node):
        self._emit("const" if isinstance(src, Imm) else "mov", (src,), dst.width, node, dst=dst)

    def _jump(self, block):
        if self.current.term is None:
            self.current.term = Instr("jmp", None, (), {"target": block.id})

    def _type(self, node, expected=None):
        return self.source.expressions.get(node) or expected or _Type(8)

    def _const(self, node):
        from .frontend import _BINARY
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return self.typed.constants[node.id]
        if isinstance(node, ast.UnaryOp):
            value = self._const(node.operand)
            return ~value if isinstance(node.op, ast.Invert) else -value
        return _BINARY[type(node.op)](self._const(node.left), self._const(node.right))

    def _address(self, node):
        ty = self.source.expressions.get(node.slice)
        value = Imm(self._const(node.slice), self.typed._addr_bits[node.value.attr]) if ty is None else self._expr(node.slice)
        return node.value.attr, value

    def _expr(self, node, expected=None):
        ty = self._type(node, expected)
        if isinstance(node, ast.Constant) or isinstance(node, ast.Name) and node.id in self.typed.constants:
            return Imm(self._const(node), ty.width)
        if isinstance(node, ast.Name):
            return self.locals[node.id]
        if isinstance(node, ast.BinOp):
            left = self._expr(node.left, ty)
            right = self._expr(node.right, ty)
            return self._emit(_OPS[type(node.op)], (left, right), ty.width, node)
        if isinstance(node, ast.UnaryOp):
            return self._emit("not" if isinstance(node.op, ast.Invert) else "neg", (self._expr(node.operand, ty),), ty.width, node)
        if isinstance(node, ast.Subscript):
            space, address = self._address(node)
            return self._emit("load", (address,), 8, node, space=space)
        if isinstance(node, ast.IfExp):
            yes, no, merged = self._block(), self._block(), self._block()
            self._condition(node.test, yes, no)
            self.current = yes
            left = self._expr(node.body, ty)
            dst = self._new(ty.width)
            self._copy(dst, left, node)
            self._jump(merged)
            self.current = no
            self._copy(dst, self._expr(node.orelse, ty), node)
            self._jump(merged)
            self.current = merged
            return dst
        if isinstance(node, ast.Call):
            name = node.func.id
            if name in ("u8", "u16") or name in self.typed.aliases:
                if node in self.source.conversion_constants:
                    value = self.source.conversion_constants[node]
                    return Imm(value & ((1 << ty.width) - 1), ty.width)
                arg = node.args[0]
                if name == "u8" and isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.RShift) and isinstance(arg.right, ast.Constant) and arg.right.value == 8 and self._type(arg.left).width == 16:
                    return self._emit("hi", (self._expr(arg.left),), 8, node)
                value = self._expr(arg, ty)
                if isinstance(value, Imm):
                    return Imm(value.value & ((1 << ty.width) - 1), ty.width)
                if value.width == ty.width:
                    return value
                return self._emit("trunc" if ty.width < value.width else "zext", (value,), ty.width, node)
            callee = self.typed.functions[name]
            args = tuple(self._expr(arg, desired) for arg, (_, desired) in zip(node.args, callee.params))
            if callee.ret is None:
                self.current.instrs.append(Instr("call", None, args, {"callee": name, "line": node.lineno}))
                return None
            return self._emit("call", args, ty.width, node, callee=name)
        raise AssertionError(type(node).__name__)

    def _condition(self, node, yes, no):
        if isinstance(node, ast.BoolOp):
            for value in node.values[:-1]:
                next_block = self._block()
                self._condition(value, next_block if isinstance(node.op, ast.And) else yes,
                                no if isinstance(node.op, ast.And) else next_block)
                self.current = next_block
            self._condition(node.values[-1], yes, no)
            return
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            self._condition(node.operand, no, yes)
            return
        if isinstance(node, ast.Compare):
            left = self._expr(node.left, self.source.expressions.get(node.comparators[0]) or _Type(8))
            right = self._expr(node.comparators[0], _Type(left.width))
            cond = _CONDITIONS[type(node.ops[0])]
        else:
            left = self._expr(node, _Type(8))
            right, cond = Imm(0, left.width), "ne"
        self.current.term = Instr("br", None, (left, right), {"cond": cond, "true": yes.id, "false": no.id, "line": node.lineno})

    def _assign(self, target, value, node):
        if isinstance(target, ast.Subscript):
            # Python evaluates the RHS before the assignment address.
            space, address = self._address(target)
            self.current.instrs.append(Instr("store", None, (address, value), {"space": space, "line": node.lineno}))
            return
        if target.id not in self.locals:
            self.locals[target.id] = self._new(self.source.variables[target.id].width, target.id)
        self._copy(self.locals[target.id], value, node)

    def _statements(self, nodes):
        for node in nodes:
            if self.current.term is not None:
                self.current = self._block()
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                target = node.targets[0] if isinstance(node, ast.Assign) else node.target
                value = self._expr(node.value, self.source.variables[target.id] if isinstance(target, ast.Name) else _Type(8))
                self._assign(target, value, node)
            elif isinstance(node, ast.AugAssign):
                address = self._address(node.target) if isinstance(node.target, ast.Subscript) else None
                left = self._emit("load", (address[1],), 8, node.target, space=address[0]) if address else self._expr(node.target)
                right = self._expr(node.value, self._type(node.target))
                value = self._emit(_OPS[type(node.op)], (left, right), left.width, node)
                if address:
                    self.current.instrs.append(Instr("store", None, (address[1], value), {"space": address[0], "line": node.lineno}))
                else:
                    self._assign(node.target, value, node)
            elif isinstance(node, ast.Return):
                if node.value is not None:
                    value = self._expr(node.value, self.source.ret)
                    if self.return_value is None:
                        self.return_value = self._new(self.source.ret.width)
                    self._copy(self.return_value, value, node)
                self.current.term = Instr("jmp", None, (), {"target": None})
                self.return_jumps.append(self.current.term)
            elif isinstance(node, ast.If):
                yes, no, merged = self._block(), self._block(), self._block()
                self._condition(node.test, yes, no)
                self.current = yes
                self._statements(node.body)
                self._jump(merged)
                self.current = no
                self._statements(node.orelse)
                self._jump(merged)
                self.current = merged
            elif isinstance(node, (ast.While, ast.For)):
                index, stop = None, None
                if isinstance(node, ast.For):
                    ty = self.source.variables[node.target.id]
                    low = self._expr(node.iter.args[0], ty) if len(node.iter.args) == 2 else Imm(0, ty.width)
                    stop = self._expr(node.iter.args[-1], ty)
                    if isinstance(stop, VReg):
                        frozen = self._new(stop.width)
                        self._copy(frozen, stop, node)
                        stop = frozen
                    if node.target.id not in self.locals:
                        self.locals[node.target.id] = self._new(ty.width, node.target.id)
                    index = self.locals[node.target.id]
                    self._copy(index, low, node)
                header, body, end = self._block(), self._block(), self._block()
                increment = self._block() if index is not None else header
                self._jump(header)
                self.current = header
                if index is None:
                    self._condition(node.test, body, end)
                else:
                    self.current.term = Instr("br", None, (index, stop),
                                              {"cond": "ltu", "true": body.id, "false": end.id,
                                               "line": node.lineno})
                self.loops.append((increment, end))
                self.current = body
                self._statements(node.body)
                self._jump(increment)
                self.loops.pop()
                if index is not None:
                    self.current = increment
                    self._emit("add", (index, Imm(1, index.width)), index.width, node, dst=index)
                    self._jump(header)
                self.current = end
            elif isinstance(node, ast.Break):
                self._jump(self.loops[-1][1])
            elif isinstance(node, ast.Continue):
                self._jump(self.loops[-1][0])
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                if node.value.func.id == "asm":
                    self._assembly(node.value)
                else:
                    self._expr(node.value)

    def _assembly(self, node):
        metadata = {k.arg: k.value for k in node.keywords}
        def locations(key):
            return tuple((self.locals[(v.elts[0] if isinstance(v, ast.Tuple) else v).id], ast.literal_eval(v.elts[1]) if isinstance(v, ast.Tuple) else None)
                         for v in metadata.get(key, ast.Tuple(elts=[])).elts)
        inputs, outputs = locations("ins"), locations("outs")
        self.current.instrs.append(Instr("asm", None, tuple(v for v, _ in inputs), {
            "text": node.args[0].value, "outs": tuple(v for v, _ in outputs), "inputs": inputs, "outputs": outputs,
            "clobbers": ast.literal_eval(metadata["clobbers"]) if "clobbers" in metadata else (),
            "nbytes": self._const(metadata["nbytes"]), "ref": metadata.get("ref"),
            "flags_ok": ast.literal_eval(metadata["flags_ok"]) if "flags_ok" in metadata else False, "line": node.lineno}))

    def _run(self):
        self._statements(self.source.node.body)
        final_source = self.current
        end = self._block()
        if final_source.term is None:
            final_source.term = Instr("jmp", None, (), {"target": end.id})
        end.term = Instr("ret", None, (self.return_value,) if self.return_value else ())
        for jump in self.return_jumps:
            jump.meta["target"] = end.id
        return self.function


def _lower(typed):
    return {name: _Lower(typed, fn)._run() for name, fn in typed.functions.items()}
