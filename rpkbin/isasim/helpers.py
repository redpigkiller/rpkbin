"""Width-explicit integer operations."""

from dataclasses import dataclass as _dataclass

from .errors import UsageError, _is_int


def _integer(value, name, minimum=None):
    if not _is_int(value) or (minimum is not None and value < minimum):
        raise UsageError(f"{name}={value!r}: expected integer >= {minimum}")
    return value


def _mask(bits):
    _integer(bits, "bits", 1)
    return (1 << bits) - 1


def to_unsigned(value: int, bits: int) -> int:
    return _integer(value, "value") & _mask(bits)


def to_signed(value: int, bits: int) -> int:
    value = to_unsigned(value, bits)
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def sign_extend(value: int, from_bits: int, to_bits: int) -> int:
    _mask(from_bits)
    if not _is_int(to_bits) or to_bits < from_bits:
        raise UsageError(f"to_bits={to_bits!r}, from_bits={from_bits!r}")
    return to_unsigned(to_signed(value, from_bits), to_bits)


def bit(value: int, n: int) -> int:
    _integer(value, "value")
    _integer(n, "n", 0)
    return (value >> n) & 1


def set_bit(value: int, n: int, b: int) -> int:
    _integer(value, "value")
    _integer(n, "n", 0)
    if not _is_int(b) or b not in (0, 1):
        raise UsageError(f"b={b!r}: expected 0 or 1")
    return (value & ~(1 << n)) | (b << n)


def parity(value: int) -> int:
    return _integer(value, "value", 0).bit_count() & 1


def rotate_left(value: int, n: int, bits: int) -> int:
    value = to_unsigned(value, bits)
    n = _integer(n, "n", 0) % bits
    return ((value << n) | (value >> (bits - n))) & _mask(bits)


def rotate_right(value: int, n: int, bits: int) -> int:
    value = to_unsigned(value, bits)
    n = _integer(n, "n", 0) % bits
    return ((value >> n) | (value << (bits - n))) & _mask(bits)


@_dataclass(frozen=True)
class AddResult:
    result: int
    carry: int
    overflow: int


@_dataclass(frozen=True)
class SubResult:
    result: int
    borrow: int
    overflow: int


def add_with_carry(a: int, b: int, carry_in: int, bits: int) -> AddResult:
    a = to_unsigned(a, bits)
    b = to_unsigned(b, bits)
    if not _is_int(carry_in) or carry_in not in (0, 1):
        raise UsageError(f"carry_in={carry_in!r}")
    full = a + b + carry_in
    result = full & _mask(bits)
    return AddResult(
        result, full >> bits, (((a ^ result) & (b ^ result)) >> (bits - 1)) & 1
    )


def sub_with_borrow(a: int, b: int, borrow_in: int, bits: int) -> SubResult:
    a = to_unsigned(a, bits)
    b = to_unsigned(b, bits)
    if not _is_int(borrow_in) or borrow_in not in (0, 1):
        raise UsageError(f"borrow_in={borrow_in!r}")
    result = (a - b - borrow_in) & _mask(bits)
    return SubResult(
        result, int(a < b + borrow_in), (((a ^ b) & (a ^ result)) >> (bits - 1)) & 1
    )


def carry_out_of_bit(a: int, b: int, carry_in: int, bit_index: int) -> int:
    _integer(bit_index, "bit_index", 0)
    _integer(a, "a")
    _integer(b, "b")
    if not _is_int(carry_in) or carry_in not in (0, 1):
        raise UsageError(f"carry_in={carry_in!r}")
    mask = (1 << (bit_index + 1)) - 1
    return ((a & mask) + (b & mask) + carry_in) >> (bit_index + 1)
