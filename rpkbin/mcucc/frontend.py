"""Typed validation of the restricted Python source language."""

import ast
import builtins
from dataclasses import dataclass, field
import operator
import re
from string import Formatter
from types import MappingProxyType

from .errors import SourceError, UnsupportedError
from .target import MemLoc, _width, units


@dataclass(frozen=True)
class _Type:
    width: int
    space: str | None = None


@dataclass
class _Function:
    node: object
    params: tuple
    ret: object
    options: dict
    external: bool
    variables: dict = field(default_factory=dict)
    expressions: dict = field(default_factory=dict)
    conversion_constants: dict = field(default_factory=dict)
    calls: set = field(default_factory=set)
    abi: dict = field(default_factory=dict)


@dataclass
class _Module:
    tree: object
    constants: dict
    aliases: dict
    functions: dict
    filename: str
    _addr_bits: dict = field(default_factory=dict)


_BINARY = MappingProxyType({ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
           ast.BitAnd: operator.and_, ast.BitOr: operator.or_, ast.BitXor: operator.xor,
           ast.LShift: operator.lshift, ast.RShift: operator.rshift})
_VALUE_OPS = (ast.Add, ast.Sub, ast.BitAnd, ast.BitOr, ast.BitXor, ast.LShift, ast.RShift)
_IMPORTS = ("u8", "u16", "Ptr", "mem", "fn", "extern", "asm")


class _Parser:
    def __init__(self, source, target, filename, addr_bits):
        self.target, self.filename = target, filename
        self.spaces = ({s.name: s.addr_bits for s in target.spaces if s.kind == "data"}
                       if target is not None else dict(addr_bits))
        try:
            tree = ast.parse(source, filename=filename, feature_version=(3, 11))
        except SyntaxError as exc:
            raise SourceError(f"{filename}:{exc.lineno}: {exc.msg}") from exc
        self.module = _Module(tree, {}, {}, {}, filename, self.spaces)
        self.function = None

    def _error(self, node, message):
        raise SourceError(f"{self.filename}:{getattr(node, 'lineno', 1)}: {type(node).__name__}: {message}")

    def _const(self, node):
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.Name) and node.id in self.module.constants:
            return self.module.constants[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            try:
                return _BINARY[type(node.op)](self._const(node.left), self._const(node.right))
            except (ZeroDivisionError, ValueError) as exc:
                self._error(node, str(exc))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.Invert, ast.UAdd)):
            value = self._const(node.operand)
            return -value if isinstance(node.op, ast.USub) else ~value if isinstance(node.op, ast.Invert) else value
        self._error(node, "expected integer constant expression")

    def _is_const_expression(self, node):
        if isinstance(node, ast.Constant):
            return type(node.value) is int
        if isinstance(node, ast.Name):
            return node.id in self.module.constants
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            return self._is_const_expression(node.left) and self._is_const_expression(node.right)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.Invert, ast.UAdd)):
            return self._is_const_expression(node.operand)
        return False

    def _type(self, node):
        if isinstance(node, ast.Name):
            if node.id in ("u8", "u16"):
                return _Type(8 if node.id == "u8" else 16)
            if node.id in self.module.aliases:
                return self.module.aliases[node.id]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Ptr" and len(node.args) == 1 and not node.keywords:
            value = node.args[0]
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str) or value.value not in self.spaces:
                self._error(node, "Ptr requires a declared data space")
            width = self.spaces[value.value]
            if width not in (8, 16):
                self._error(node, f"Ptr width {width}: expected 8 or 16")
            return _Type(width, value.value)
        self._error(node, "expected u8, u16 or a Ptr alias")

    def _metadata(self, node):
        try:
            return ast.literal_eval(node)
        except (ValueError, TypeError) as exc:
            self._error(node, f"invalid declaration: {exc}")

    def _headers(self):
        for node in self.module.tree.body:
            if isinstance(node, ast.ImportFrom):
                if node.module != "rpkbin.mcucc.lang" or node.level or any(a.name not in _IMPORTS or a.asname is not None for a in node.names):
                    self._error(node, "only named language imports are allowed")
            elif isinstance(node, ast.Assign):
                if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                    self._error(node, "module assignment requires one constant name")
                name = node.targets[0].id
                if name in _IMPORTS or name in vars(builtins) or name in self.module.constants or name in self.module.aliases:
                    self._error(node, f"reserved or duplicate name {name!r}")
                if isinstance(node.value, ast.Call):
                    self.module.aliases[name] = self._type(node.value)
                else:
                    self.module.constants[name] = self._const(node.value)
            elif isinstance(node, ast.FunctionDef):
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", node.name) is None:
                    self._error(node, f"function {node.name!r}: expected an ASCII assembly identifier")
                if node.name in self.module.functions or node.name in _IMPORTS or node.name in self.module.constants or node.name in self.module.aliases:
                    self._error(node, f"duplicate or reserved function {node.name!r}")
                if self.target is not None and node.name in {r.name for r in (*self.target.regs, *self.target.aliases)}:
                    self._error(node, f"function {node.name!r} conflicts with register")
                arguments = node.args
                if arguments.posonlyargs or arguments.vararg or arguments.kwarg or arguments.kwonlyargs or arguments.defaults or arguments.kw_defaults or len(arguments.args) > 8:
                    self._error(node, "at most eight positional typed parameters without defaults")
                params = tuple((a.arg, self._type(a.annotation)) for a in arguments.args)
                ret = self._type(node.returns) if node.returns is not None else None
                options, external = {}, False
                if len(node.decorator_list) > 1:
                    self._error(node, "only one fn or extern decorator")
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Name) or decorator.func.id not in ("fn", "extern") or decorator.args:
                        self._error(decorator, "expected fn or extern keyword declaration")
                    external = decorator.func.id == "extern"
                    allowed = ("args", "ret", "clobbers") if external else ("args", "ret", "preserves")
                    for keyword in decorator.keywords:
                        if keyword.arg not in allowed or keyword.arg in options:
                            self._error(decorator, f"unknown or duplicate declaration field {keyword.arg!r}")
                        options[keyword.arg] = self._metadata(keyword.value)
                    if external and set(options) != set(allowed):
                        self._error(decorator, "extern requires args, ret and clobbers")
                fn = _Function(node, params, ret, options, external)
                fn.variables.update(params)
                self.module.functions[node.name] = fn
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                pass
            else:
                self._error(node, "unsupported module statement")

    def _abi(self, fn):
        options, target = fn.options, self.target
        if "args" in options and (not isinstance(options["args"], tuple) or len(options["args"]) != len(fn.params)):
            self._error(fn.node, "args must match parameter count")
        for key in ("preserves", "clobbers"):
            if key in options and (not isinstance(options[key], tuple) or any(not isinstance(p, str) for p in options[key])):
                self._error(fn.node, f"{key}: expected register name tuple")
        if fn.ret is None:
            if options.get("ret") is not None:
                self._error(fn.node, "void function cannot declare return location")
        elif "ret" in options and options["ret"] is None:
            self._error(fn.node, "non-void function requires return location")
        if target is None:
            return
        registers = {r.name for r in (*target.regs, *target.aliases)}
        def location(value, ty):
            if isinstance(value, tuple) and len(value) == 2:
                value = MemLoc(*value)
            if isinstance(value, str):
                if value not in registers or (ty is not None and _width(target, value) != ty.width):
                    self._error(fn.node, f"ABI location {value!r}: unknown register or width mismatch")
            elif isinstance(value, MemLoc):
                if value.space not in self.spaces or type(value.addr) is not int or not 0 <= value.addr < 1 << self.spaces[value.space]:
                    self._error(fn.node, f"invalid ABI memory location {value!r}")
                if ty is not None and ty.width != target.cell_bits:
                    raise UnsupportedError(f"{fn.node.name}: multi-cell ABI memory location {value!r} unsupported")
            else:
                self._error(fn.node, f"invalid ABI location {value!r}")
            return value
        args = []
        occupied = set()
        for index, (_, ty) in enumerate(fn.params):
            if "args" in options:
                chosen = location(options["args"][index], ty)
            else:
                candidates = target.abi.arg_locs.get(ty.width, ())
                chosen = next((c for c in candidates if not units(target, c) & occupied), None)
                if chosen is None:
                    # Availability is checked by compilation, not by typing.
                    args.append(None)
                    continue
                chosen = location(chosen, ty)
            if units(target, chosen) & occupied:
                self._error(fn.node, "ABI arguments overlap")
            occupied.update(units(target, chosen))
            args.append(chosen)
        ret = None
        if fn.ret is not None:
            value = options.get("ret", target.abi.ret_locs.get(fn.ret.width))
            ret = location(value, fn.ret) if value is not None else None
        kept = options.get("preserves", target.abi.callee_saved) if not fn.external else ()
        clobbers = options.get("clobbers", ())
        for reg in (*kept, *clobbers):
            if reg not in registers:
                self._error(fn.node, f"unknown ABI register {reg!r}")
        if ret is not None and any(units(target, p) & units(target, ret) for p in kept):
            self._error(fn.node, "preserves overlaps return location")
        fn.abi = {"args": tuple(args), "ret": ret, "preserves": kept, "clobbers": clobbers}

    def _range(self, node, ty):
        value = self._const(node)
        if not 0 <= value < 1 << ty.width:
            self._error(node, f"constant {value} outside unsigned width {ty.width}")

    def _fit(self, node, ty):
        if isinstance(node, ast.BinOp) and isinstance(node.op, _VALUE_OPS):
            if isinstance(node.op, (ast.LShift, ast.RShift)):
                self._fit(node.left, ty)
                amount = self._const(node.right)
                if not 0 <= amount < ty.width:
                    self._error(node, f"shift {amount} outside width {ty.width}")
            else:
                self._pair(node.left, node.right, self.function.expressions.get(node.left),
                           self.function.expressions.get(node.right), ty)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Invert, ast.USub)):
            if isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
                self._error(node, "negative constant is not a value expression")
            self._fit(node.operand, ty)
        elif isinstance(node, ast.IfExp):
            self._fit(node.body, ty)
            self._fit(node.orelse, ty)
        else:
            self._range(node, ty)
        self.function.expressions[node] = ty

    def _pair(self, left, right, lt, rt, expected=None):
        ty = lt or rt or expected
        if ty is None:
            return None
        if ty.width < 1 or (lt is not None and lt != ty) or (rt is not None and rt != ty):
            self._error(left, "operands require equal unsigned types")
        if lt is None:
            self._fit(left, ty)
        if rt is None:
            self._fit(right, ty)
        return ty

    def _expr(self, node, assigned, expected=None, condition=False):
        ty = None
        if isinstance(node, ast.Name):
            if node.id in self.module.constants:
                ty = None
            elif node.id in assigned:
                ty = self.function.variables[node.id]
            else:
                self._error(node, f"read before assignment: {node.id!r}")
        elif isinstance(node, ast.Constant) and type(node.value) is int:
            if node.value < 0:
                self._error(node, "negative integer literal")
        elif isinstance(node, ast.BinOp) and isinstance(node.op, _VALUE_OPS):
            lt = self._expr(node.left, assigned)
            rt = self._expr(node.right, assigned)
            if isinstance(node.op, (ast.LShift, ast.RShift)):
                ty = lt or expected
                if ty is None:
                    self._const(node)
                else:
                    if not isinstance(node.right, (ast.Constant, ast.Name)):
                        self._error(node.right, "shift amount must be an integer constant")
                    amount = self._const(node.right)
                    if not 0 <= amount < ty.width:
                        self._error(node, f"shift {amount} outside width {ty.width}")
                    if lt is None:
                        self._fit(node.left, ty)
            else:
                ty = self._pair(node.left, node.right, lt, rt, expected)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.Invert)):
            if isinstance(node.operand, ast.Constant) and isinstance(node.op, ast.USub):
                self._error(node, "negative constant is not a value expression")
            ty = self._expr(node.operand, assigned, expected)
        elif isinstance(node, ast.Compare):
            if not condition or len(node.ops) != 1 or not isinstance(node.ops[0], (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                self._error(node, "one unsigned comparison in condition only")
            lt, rt = self._expr(node.left, assigned), self._expr(node.comparators[0], assigned)
            self._pair(node.left, node.comparators[0], lt, rt, _Type(8))
            ty = _Type(0)
        elif isinstance(node, ast.BoolOp) or (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)):
            if not condition:
                self._error(node, "boolean expressions are conditions only")
            for value in node.values if isinstance(node, ast.BoolOp) else (node.operand,):
                self._expr(value, assigned, condition=True)
            ty = _Type(0)
        elif isinstance(node, ast.IfExp):
            self._expr(node.test, assigned, condition=True)
            lt = self._expr(node.body, assigned, expected)
            rt = self._expr(node.orelse, assigned, expected)
            ty = self._pair(node.body, node.orelse, lt, rt, expected)
        elif isinstance(node, ast.Subscript):
            self._memory(node, assigned)
            ty = _Type(8)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if node.keywords or any(isinstance(a, ast.Starred) for a in node.args):
                self._error(node, "no keyword arguments or unpacking in value calls")
            if name in ("u8", "u16") or name in self.module.aliases:
                if len(node.args) != 1:
                    self._error(node, "conversion requires one value")
                ty = self._type(node.func)
                argument = node.args[0]
                if self._is_const_expression(argument):
                    value = self._const(argument)
                    if ty.space is None and not 0 <= value < 1 << ty.width:
                        self._error(argument, f"constant {value} outside unsigned width {ty.width}")
                    self.function.conversion_constants[node] = value
                else:
                    source = self._expr(argument, assigned)
                    if source is None or source.width < 1:
                        self._error(node, "conversion requires unsigned value or integer constant expression")
            elif name in self.module.functions:
                callee = self.module.functions[name]
                if len(node.args) != len(callee.params):
                    self._error(node, f"wrong argument count for {name!r}")
                for arg, (_, desired) in zip(node.args, callee.params):
                    actual = self._expr(arg, assigned, desired)
                    if actual is not None and actual != desired:
                        self._error(arg, f"argument type mismatch for {name!r}")
                self.function.calls.add(name)
                ty = callee.ret or _Type(-1)
            else:
                self._error(node, f"unknown function {name!r}")
        else:
            self._error(node, "unsupported expression or operation")
        if ty is None and expected is not None:
            self._fit(node, expected)
            ty = expected
        if condition and ty is None:
            self._error(node, "untyped integer is not a condition")
        if condition and ty.width < 0:
            self._error(node, "void call is not a condition")
        self.function.expressions[node] = ty
        return ty

    def _memory(self, node, assigned):
        value = node.value
        if not isinstance(value, ast.Attribute) or not isinstance(value.value, ast.Name) or value.value.id != "mem" or value.attr not in self.spaces:
            self._error(node, "memory access requires a declared data space")
        ty = self._expr(node.slice, assigned)
        wanted = _Type(self.spaces[value.attr], value.attr)
        if ty is None:
            self._range(node.slice, wanted)
        elif ty != wanted:
            self._error(node.slice, f"address requires Ptr({value.attr!r})")
        return value.attr

    def _store(self, target, value, assigned, annotation, readonly):
        if isinstance(target, ast.Subscript):
            if annotation is not None:
                self._error(target, "memory cells cannot be annotated")
            self._memory(target, assigned)
            ty = self._expr(value, assigned, _Type(8))
            if ty != _Type(8):
                self._error(value, "cell stores require u8")
            return
        if not isinstance(target, ast.Name) or target.id in readonly:
            self._error(target, "expected writable local variable")
        name = target.id
        self._local_name(target, name)
        old = self.function.variables.get(name)
        if annotation is not None and old is not None:
            self._error(target, f"repeated type annotation for {name!r}")
        wanted = self._type(annotation) if annotation is not None else old
        actual = self._expr(value, assigned, wanted)
        if actual is None or actual.width < 1:
            self._error(value, "first integer assignment needs a type annotation")
        if wanted is not None and wanted != actual:
            self._error(value, f"variable {name!r} cannot change type")
        self.function.variables[name] = actual
        assigned.add(name)

    def _assembly(self, call, assigned, readonly=frozenset()):
        if len(call.args) != 1 or not isinstance(call.args[0], ast.Constant) or not isinstance(call.args[0].value, str):
            self._error(call, "asm requires one literal template")
        metadata = {k.arg: k.value for k in call.keywords}
        if set(metadata) - {"ins", "outs", "clobbers", "nbytes", "ref", "flags_ok"} or "nbytes" not in metadata:
            self._error(call, "invalid asm fields or missing nbytes")
        if self._const(metadata["nbytes"]) < 1:
            self._error(call, "asm nbytes must be positive")
        operands=[]
        for key in ("ins", "outs"):
            values = metadata.get(key, ast.Tuple(elts=[]))
            if not isinstance(values, ast.Tuple):
                self._error(call, "asm locations require tuples")
            for value in values.elts:
                hint = None
                if isinstance(value, ast.Tuple) and len(value.elts) == 2:
                    value, hint_node = value.elts
                    hint = self._metadata(hint_node)
                if not isinstance(value, ast.Name) or value.id not in assigned:
                    self._error(call, "asm operands must be assigned locals")
                if key == "outs" and value.id in readonly:
                    self._error(call, f"asm outs cannot write readonly variable {value.id!r}")
                if hint is not None and self.target is not None and hint not in {r.name for r in (*self.target.regs, *self.target.aliases)}:
                    self._error(call, f"unknown asm register {hint!r}")
                if hint is not None and self.target is not None and _width(self.target,hint)!=self.function.variables[value.id].width:
                    self._error(call,f"asm register {hint!r}: width mismatch for {value.id!r}")
                if value.id not in operands:
                    operands.append(value.id)
        try:
            fields=tuple(Formatter().parse(call.args[0].value))
        except ValueError as exc:
            self._error(call,f"invalid asm placeholders: {exc}")
        for _, name, spec, conversion in fields:
            if name is not None and (not name.isdecimal() or int(name) >= len(operands) or spec or conversion):
                self._error(call, f"invalid asm placeholder {name!r}")
        if "flags_ok" in metadata and type(self._metadata(metadata["flags_ok"])) is not bool:
            self._error(call,"asm flags_ok requires bool")
        if "clobbers" in metadata:
            clobbers = self._metadata(metadata["clobbers"])
            if not isinstance(clobbers, tuple) or any(not isinstance(v, str) for v in clobbers):
                self._error(call, "asm clobbers requires register names")
            if self.target is not None and any(v not in {r.name for r in (*self.target.regs, *self.target.aliases)} for v in clobbers):
                self._error(call, "asm clobber refers to unknown register")

    def _statements(self, nodes, assigned, readonly=frozenset(), depth=0):
        assigned, terminated = set(assigned), False
        for node in nodes:
            was_terminated = terminated
            if isinstance(node, ast.Assign):
                if len(node.targets) != 1:
                    self._error(node, "no multiple assignment")
                self._store(node.targets[0], node.value, assigned, None, readonly)
            elif isinstance(node, ast.AnnAssign):
                if node.value is None:
                    self._error(node, "annotation requires first value")
                self._store(node.target, node.value, assigned, node.annotation, readonly)
            elif isinstance(node, ast.AugAssign):
                expr = ast.copy_location(ast.BinOp(left=node.target, op=node.op, right=node.value), node)
                self._store(node.target, expr, assigned, None, readonly)
            elif isinstance(node, ast.If):
                self._expr(node.test, assigned, condition=True)
                left, lt = self._statements(node.body, assigned, readonly, depth)
                right, rt = self._statements(node.orelse, assigned, readonly, depth)
                assigned = right if lt and not rt else left if rt and not lt else left & right
                terminated = lt and rt
            elif isinstance(node, ast.While):
                if node.orelse:
                    self._error(node, "while else is unsupported")
                self._expr(node.test, assigned, condition=True)
                self._statements(node.body, assigned, readonly, depth + 1)
            elif isinstance(node, ast.For):
                if node.orelse or not isinstance(node.target, ast.Name) or node.target.id in readonly or not isinstance(node.iter, ast.Call) or not isinstance(node.iter.func, ast.Name) or node.iter.func.id != "range" or "range" in assigned or "range" in self.module.functions or node.iter.keywords or len(node.iter.args) not in (1, 2):
                    self._error(node, "expected for local in range(stop) or range(start,stop)")
                self._local_name(node.target, node.target.id)
                stop = node.iter.args[-1]
                ty = self._expr(stop, assigned)
                if ty is None:
                    maximum = self._const(stop)
                    ty = _Type(8 if maximum < 1 << 8 else 16)
                    self._fit(stop, ty)
                if len(node.iter.args) == 2:
                    lower = self._expr(node.iter.args[0], assigned, ty)
                    if lower != ty:
                        self._error(node, "range bounds must have equal types")
                if node.target.id in self.function.variables and self.function.variables[node.target.id] != ty:
                    self._error(node, f"loop variable {node.target.id!r} cannot change type")
                self.function.variables[node.target.id] = ty
                self.function.expressions[node.target] = ty
                self._statements(node.body, assigned | {node.target.id}, readonly | {node.target.id}, depth + 1)
                assigned.discard(node.target.id)
            elif isinstance(node, (ast.Break, ast.Continue)):
                if not depth:
                    self._error(node, "loop control outside loop")
                terminated = True
            elif isinstance(node, ast.Return):
                if self.function.ret is None:
                    if node.value is not None:
                        self._error(node, "void function cannot return a value")
                elif node.value is None or self._expr(node.value, assigned, self.function.ret) != self.function.ret:
                    self._error(node, "return type mismatch")
                terminated = True
            elif isinstance(node, ast.Expr):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    continue
                if not isinstance(node.value, ast.Call):
                    self._error(node, "expression statement must be a function call")
                if isinstance(node.value.func, ast.Name) and node.value.func.id == "asm":
                    self._assembly(node.value, assigned, readonly)
                else:
                    self._expr(node.value, assigned)
            elif not isinstance(node, ast.Pass):
                self._error(node, "unsupported statement")
            terminated = terminated or was_terminated
        return assigned, terminated

    def _local_name(self, node, name):
        if name in self.module_names:
            self._error(node, f"local name {name!r} shadows a module name")

    def _run(self):
        self._headers()
        # The reference namespace always contains the standard language bindings.
        self.module_names = set(_IMPORTS) | set(self.module.constants) | set(self.module.aliases) | set(self.module.functions)
        for fn in self.module.functions.values():
            self.function = fn
            for argument in fn.node.args.args:
                self._local_name(argument, argument.arg)
            self._abi(fn)
            _, complete = self._statements(fn.node.body, {name for name, _ in fn.params})
            if fn.ret is not None and not complete:
                self._error(fn.node, "return value is not defined on every path")
        def visit(name, active, done):
            if name in active:
                self._error(self.module.functions[name].node, "recursive call graph")
            if name not in done:
                for callee in sorted(self.module.functions[name].calls):
                    visit(callee, active | {name}, done)
                done.add(name)
        done = set()
        for name in self.module.functions:
            visit(name, set(), done)
        return self.module


def _parse(source, target, filename):
    return _Parser(source, target, filename, None)._run()


def _parse_reference(source, addr_bits, filename):
    return _Parser(source, None, filename, addr_bits)._run()
