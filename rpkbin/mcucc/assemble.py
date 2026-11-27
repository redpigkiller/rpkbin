"""Two-pass placement and target-supplied instruction encoding."""

from dataclasses import dataclass
from .errors import InternalError, TargetError, UnsupportedError, _EncodeLengthError
from .target import M, T


@dataclass(frozen=True)
class Program:
    cells: dict
    symbols: dict
    sizes: dict


def _placement(functions, origin):
    symbols, addresses, sizes = {}, {}, {}
    address = origin
    for name, function in functions.items():
        start = address
        for index, instruction in enumerate(function.mir):
            addresses[name, index] = address
            if instruction._label is not None:
                if instruction._label in symbols:
                    raise InternalError(f"duplicate label {instruction._label!r}")
                symbols[instruction._label] = address
            address += instruction.bytes
        sizes[name] = address - start
        if sizes[name] != function.bytes:
            raise InternalError(f"{name}: placement size differs from final size")
    return symbols, addresses, sizes, address


def _arguments(instruction, address, symbols, unresolved=False):
    values = {}
    for operand in instruction.form.operands:
        value = instruction.values[operand.name]
        if isinstance(operand, M):
            value = value.addr
        elif isinstance(operand, T):
            if value not in symbols:
                if not unresolved:
                    raise TargetError(f"unresolved symbol {value!r}")
                value = 0
            else:
                value = symbols[value]
                if operand.rel_bits is not None:
                    value -= address + (instruction.bytes if operand.base == "end" else 0)
            if operand.rel_bits is not None:
                limit = 1 << (operand.rel_bits - 1)
                if not -limit <= value < limit:
                    raise UnsupportedError(f"{instruction.form.id}: branch offset {value} outside {operand.rel_bits} bits")
            elif not 0 <= value < 1 << operand.abs_bits:
                raise UnsupportedError(f"{instruction.form.id}: absolute address {value} outside {operand.abs_bits} bits")
        values[operand.name] = value
    return values


def _encode(instruction, address, symbols, target, unresolved=False):
    if instruction._label is not None:
        return ()
    if instruction._asm is not None:
        raise UnsupportedError("raw asm 沒有編碼入口")
    else:
        if instruction.form.encode is None:
            raise TargetError(f"form {instruction.form.id!r}: missing encode callback")
        values = _arguments(instruction, address, symbols, unresolved)
        try:
            cells = tuple(instruction.form.encode(values))
        except Exception as exc:
            raise TargetError(f"form {instruction.form.id!r}: encode failed: {exc}") from exc
    if len(cells) != instruction.form.bytes:
        raise _EncodeLengthError(
            f"form {instruction.form.id!r}: encode length/cells disagree with form "
            f"(expected {instruction.form.bytes}, got {len(cells)})")
    if any(type(c) is not int or not 0 <= c < 1 << target.cell_bits for c in cells):
        raise _EncodeLengthError(
            f"form {instruction.form.id!r}: encode length/cells disagree with form "
            f"(invalid cells {cells!r})")
    return cells


def _assemble(result, target, origin, extra_symbols=None):
    if type(origin) is not int or origin < 0:
        raise TargetError(f"origin {origin!r}: expected nonnegative integer")
    for name,function in result.functions.items():
        if any(m._asm is not None for m in function.mir):
            raise UnsupportedError(f"{name}: raw asm 沒有編碼入口")
    symbols, addresses, sizes, end = _placement(result.functions, origin)
    if extra_symbols:
        if set(symbols) & set(extra_symbols):
            raise TargetError("external symbols overlap compiled labels")
        symbols.update(extra_symbols)
    program_space = next(s for s in target.spaces if s.kind == "program")
    if end > 1 << program_space.addr_bits:
        raise UnsupportedError("program does not fit program address space")
    cells = {}
    for name, function in result.functions.items():
        for index, instruction in enumerate(function.mir):
            address = addresses[name, index]
            cells.update((address + offset, cell) for offset, cell in enumerate(_encode(instruction, address, symbols, target)))
    return Program(cells, symbols, sizes)


def assemble(result, target, origin):
    return _assemble(result, target, origin)
