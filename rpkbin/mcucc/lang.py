"""Executable unsigned values and isolated Python reference execution."""

import ast
from copy import deepcopy
from pathlib import Path
import sys

from .errors import SourceError


__all__ = [
    "Ptr",
    "asm",
    "extern",
    "fn",
    "mem",
    "run_reference",
    "u16",
    "u8",
]


class _UInt(int):
    _width = None
    _space = None

    def __new__(cls, value):
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"unsigned value requires integer, got {value!r}")
        mask = (1 << cls._width) - 1
        if not isinstance(value, _UInt) and cls._space is None and not 0 <= value <= mask:
            raise ValueError(f"value {value} outside unsigned width {cls._width}")
        return int.__new__(cls, int(value) & mask)

    def _other(self, value):
        if isinstance(value, _UInt):
            if (value._width, value._space) != (self._width, self._space):
                raise TypeError("unsigned operand types differ")
        elif type(value) is not int:
            raise TypeError(f"unsigned operation requires integer, got {value!r}")
        elif not 0 <= value < 1 << self._width:
            raise ValueError(f"constant {value} outside unsigned width {self._width}")
        return int(value)

    def _wrap(self, value):
        return int.__new__(type(self), value & ((1 << self._width) - 1))

    def __add__(self, value):
        return self._wrap(int(self) + self._other(value))

    __radd__ = __add__

    def __sub__(self, value):
        return self._wrap(int(self) - self._other(value))

    def __rsub__(self, value):
        return self._wrap(self._other(value) - int(self))

    def __and__(self, value):
        return self._wrap(int(self) & self._other(value))

    __rand__ = __and__

    def __or__(self, value):
        return self._wrap(int(self) | self._other(value))

    __ror__ = __or__

    def __xor__(self, value):
        return self._wrap(int(self) ^ self._other(value))

    __rxor__ = __xor__

    def __invert__(self):
        return self._wrap(~int(self))

    def __neg__(self):
        return self._wrap(-int(self))

    def __lshift__(self, amount):
        if type(amount) is not int or not 0 <= amount < self._width:
            raise ValueError(f"shift {amount!r} outside unsigned width {self._width}")
        return self._wrap(int(self) << amount)

    def __rshift__(self, amount):
        if type(amount) is not int or not 0 <= amount < self._width:
            raise ValueError(f"shift {amount!r} outside unsigned width {self._width}")
        return self._wrap(int(self) >> amount)

    def __eq__(self, value):
        if isinstance(value, _UInt):
            if (value._width, value._space) != (self._width, self._space):
                return NotImplemented
            return int(self) == int(value)
        if type(value) is int:
            return int(self) == value
        return NotImplemented

    def __ne__(self, value):
        eq = self.__eq__(value)
        return NotImplemented if eq is NotImplemented else not eq

    def __lt__(self, value):
        return int(self) < self._other(value)

    def __le__(self, value):
        return int(self) <= self._other(value)

    def __gt__(self, value):
        return int(self) > self._other(value)

    def __ge__(self, value):
        return int(self) >= self._other(value)

    __hash__ = int.__hash__


class u8(_UInt):
    _width = 8


class u16(_UInt):
    _width = 16


def Ptr(space):
    raise SourceError(f"Ptr({space!r}): address width must be bound by run_reference")


class _CellView:
    def __init__(self, data, width, space):
        self._data, self._width, self._space = data, width, space

    def _address(self, value):
        if isinstance(value, _UInt):
            if value._space != self._space or value._width != self._width:
                raise TypeError(f"address requires Ptr({self._space!r})")
        elif type(value) is not int:
            raise TypeError("address requires integer constant or Ptr")
        check = getattr(self._data, "_check_source_address", None)
        if check is not None:
            check(int(value), direct=type(value) is int)
        if not 0 <= int(value) < min(len(self._data), 1 << self._width):
            raise ValueError(f"address {value!r} outside space {self._space!r}")
        return int(value)

    def __getitem__(self, value):
        return u8(self._data[self._address(value)])

    def __setitem__(self, address, value):
        if not isinstance(value, u8):
            raise TypeError("memory cell writes require u8")
        self._data[self._address(address)] = int(value)


class _Memory:
    __slots__ = ("_data", "_widths")

    def __init__(self, data, widths):
        object.__setattr__(self, "_data", data)
        object.__setattr__(self, "_widths", widths)

    def __setattr__(self, name, value):
        raise AttributeError("memory bindings are immutable")

    def __getattr__(self, space):
        if self._data is None or space not in self._data or space not in self._widths:
            raise SourceError(f"memory space {space!r}: missing data or address width")
        return _CellView(self._data[space], self._widths[space], space)


mem = _Memory(None, None)


def fn(**declaration):
    def decorate(function):
        return function
    return decorate


def extern(**declaration):
    def decorate(function):
        return function
    return decorate


def asm(text, *, ins=(), outs=(), clobbers=(), nbytes, ref=None, flags_ok=False):
    if ref is None:
        raise SourceError("inline assembly has no reference semantics")
    args = tuple(v[0] if isinstance(v, tuple) else v for v in ins)
    result = ref(*args)
    destinations = tuple(v[0] if isinstance(v, tuple) else v for v in outs)
    if not destinations:
        return result
    values = (result,) if len(destinations) == 1 else tuple(result)
    if len(values) != len(destinations):
        raise ValueError("assembly reference output count differs")
    converted = tuple(dst._wrap(int(value)) for dst, value in zip(destinations, values))
    return converted[0] if len(converted) == 1 else converted


class _ReferenceTree(ast.NodeTransformer):
    def __init__(self, typed):
        self._typed = typed
        self._function = None

    def _cast(self, node, ty):
        if ty is None or ty.width < 1:
            return node
        if ty.space is None:
            callee = ast.Name(id="u8" if ty.width == 8 else "u16", ctx=ast.Load())
        else:
            callee = ast.Call(func=ast.Name(id="Ptr", ctx=ast.Load()), args=[ast.Constant(ty.space)], keywords=[])
        return ast.copy_location(ast.Call(func=callee, args=[node], keywords=[]), node)

    def visit_ImportFrom(self, node):
        return None

    def visit_FunctionDef(self, node):
        old = self._function
        self._function = self._typed.functions[node.name]
        self.generic_visit(node)
        self._function = old
        return node

    def visit_AnnAssign(self, node):
        if isinstance(node.target, ast.Name):
            node.value = self._cast(self.visit(node.value), self._function.variables[node.target.id])
        return node

    def visit_Assign(self, node):
        ty = self._function.expressions.get(node.value) if self._function is not None else None
        self.generic_visit(node)
        if self._function is not None and isinstance(node.targets[0], ast.Name):
            node.value = self._cast(node.value, self._function.variables[node.targets[0].id])
        elif self._function is not None and isinstance(node.targets[0], ast.Subscript):
            node.value = self._cast(node.value, ty)
        return node

    def visit_BinOp(self, node):
        ty = self._function.expressions.get(node) if self._function is not None else None
        self.generic_visit(node)
        if ty is not None and ty.width > 0:
            node.left = self._cast(node.left, ty)
            if not isinstance(node.op, (ast.LShift, ast.RShift)):
                node.right = self._cast(node.right, ty)
        return node

    def visit_UnaryOp(self, node):
        ty = self._function.expressions.get(node) if self._function is not None else None
        self.generic_visit(node)
        if ty is not None and ty.width > 0:
            node.operand = self._cast(node.operand, ty)
        return node

    def visit_Call(self, node):
        if self._function is not None and node in self._function.conversion_constants:
            node.args = [ast.copy_location(ast.Constant(self._function.conversion_constants[node]), node.args[0])]
            return node
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id in self._typed.functions:
            callee = self._typed.functions[node.func.id]
            node.args = [self._cast(arg, ty) for arg, (_, ty) in zip(node.args, callee.params)]
        return node

    def visit_Return(self, node):
        if node.value is not None:
            node.value = self._cast(self.visit(node.value), self._function.ret)
        return node

    def visit_For(self, node):
        self.generic_visit(node)
        name = node.target.id
        binding = ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store())],
                             value=self._cast(ast.Name(id=name, ctx=ast.Load()), self._function.variables[name]))
        node.body.insert(0, ast.copy_location(binding, node))
        return node

    def visit_Expr(self, node):
        call = node.value
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "asm":
            kwargs = {k.arg: k.value for k in call.keywords}
            outs = kwargs.get("outs", ast.Tuple(elts=[]))
            if outs.elts:
                targets = [v.elts[0] if isinstance(v, ast.Tuple) else v for v in outs.elts]
                assigned = [ast.Name(id=t.id, ctx=ast.Store()) for t in targets]
                target = assigned[0] if len(assigned) == 1 else ast.Tuple(elts=assigned, ctx=ast.Store())
                return ast.copy_location(ast.Assign(targets=[target], value=call), node)
        return self.generic_visit(node)


def run_reference(module_path_or_source, func_name, args, memory, *, addr_bits):
    from .frontend import _parse_reference
    filename = "<reference>"
    if isinstance(module_path_or_source, Path) or (isinstance(module_path_or_source, str) and "\n" not in module_path_or_source and module_path_or_source.endswith(".py")):
        filename = str(module_path_or_source)
        try:
            source = Path(filename).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise SourceError(f"{filename}:1: cannot read source: {exc}") from exc
    else:
        source = module_path_or_source
    typed = _parse_reference(source, addr_bits, filename)
    if func_name not in typed.functions:
        raise SourceError(f"{filename}:1: function {func_name!r} not found")
    values = deepcopy(memory)
    pointer_types = {}
    def pointer(space):
        if space not in addr_bits or addr_bits[space] not in (8, 16):
            raise SourceError(f"Ptr({space!r}): missing supported address width")
        if space not in pointer_types:
            pointer_types[space] = type("_Pointer", (_UInt,), {"_width": addr_bits[space], "_space": space})
        return pointer_types[space]
    namespace = {"u8": u8, "u16": u16, "Ptr": pointer, "mem": _Memory(values, addr_bits),
                 "fn": fn, "extern": extern, "asm": asm}
    tree = ast.fix_missing_locations(_ReferenceTree(typed).visit(typed.tree))
    function = typed.functions[func_name]
    if len(args) != len(function.params):
        raise SourceError(f"{filename}:1: argument count mismatch for {func_name!r}")
    converted = tuple((pointer(ty.space) if ty.space else u8 if ty.width == 8 else u16)(value)
                      for value, (_, ty) in zip(args, function.params))
    remaining = 100000
    def trace(frame, event, arg):
        nonlocal remaining
        if event == "line" and frame.f_code.co_filename == filename:
            remaining -= 1
            if remaining < 0:
                raise TimeoutError(f"reference {func_name!r}: step budget exhausted")
        return trace
    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        exec(compile(tree, filename, "exec"), namespace)
        result = namespace[func_name](*converted)
    except (SourceError, TimeoutError):
        raise
    except Exception as exc:
        raise SourceError(f"{filename}:1: reference execution failed: {exc}") from exc
    finally:
        sys.settrace(previous)
    return result, values
