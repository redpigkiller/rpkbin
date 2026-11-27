"""Local calculator state and exact expression evaluation."""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass
from decimal import Decimal, localcontext
from fractions import Fraction

from .core.config import CalcConfig
from .core.format_engine import FormatEngine, FxpSpec
from .core.scalar_fxp import (
    ScalarQuantizeResult,
    quantize_scalar,
    raw_uint_to_fraction,
    raw_uint_to_signed_int,
    read_raw_bit_field,
    reinterp_raw_bits,
    update_raw_bit_field,
)

INT32 = FxpSpec(wl=32, fwl=0)
BASES = {"HEX": 16, "DEC": 10, "OCT": 8, "BIN": 2}
SHIFT_MODES = ("arithmetic", "logical", "rotate", "rotate_carry")
_PREFIXES = {"0x": "HEX", "0o": "OCT", "0b": "BIN"}
_REAL_TOKEN = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)[eE][+-][0-9]+")
_TOKEN = re.compile(r"\s+|<<|>>|[()+*/%&|^~\-]|[0-9A-Za-z_.]+")
_ARITHMETIC_OPS = {"+": operator.add, "-": operator.sub, "*": operator.mul,
                   "/": operator.truediv, "%": operator.mod}
_MAX_INPUT = 512
_MAX_DEPTH = 64
_MAX_EXACT_BITS = 3322  # ~1000 decimal digits, safely under Python's 4300-digit str limit
_MAX_EXACT_POWER = 1000
_MAX_DECIMAL_BITS = 50_000


@dataclass(frozen=True)
class VariableSnapshot:
    spec: FxpSpec
    exact_fraction: Fraction
    raw_uint: int
    source_kind: str = "FXP"


def _group_from_right(digits: str, width: int) -> str:
    chunks = [digits[max(0, end - width):end] for end in range(len(digits), 0, -width)]
    chunks.reverse()
    return " ".join(chunks)


def display_fraction(value: Fraction) -> str:
    """Print terminating decimals (2^a * 5^b denominators) exactly, else clean decimal."""
    numerator, denominator = value.as_integer_ratio()
    if numerator == 0:
        return "0"
    p2 = (denominator & -denominator).bit_length() - 1
    d = denominator >> p2
    p5 = 0
    while d % 5 == 0:
        d //= 5
        p5 += 1
    if d == 1:
        power = max(p2, p5)
        if not power and abs(numerator).bit_length() <= _MAX_EXACT_BITS:
            return str(numerator)
        if power <= _MAX_EXACT_POWER and abs(numerator).bit_length() <= _MAX_EXACT_BITS:
            scaled = abs(numerator) * (2 ** (power - p2)) * (5 ** (power - p5))
            if scaled.bit_length() <= _MAX_EXACT_BITS:
                digits = str(scaled).zfill(power + 1)
                sign = "-" if numerator < 0 else ""
                return sign + digits[:-power] + "." + digits[-power:].rstrip("0")

    if max(abs(numerator).bit_length(), denominator.bit_length()) <= _MAX_DECIMAL_BITS:
        with localcontext() as ctx:
            ctx.prec = 16
            return str((Decimal(numerator) / Decimal(denominator)).normalize())

    k_n = max(0, abs(numerator).bit_length() - 128)
    k_d = max(0, denominator.bit_length() - 128)
    with localcontext() as ctx:
        ctx.prec = 32
        ctx.Emax = 999_999_999
        ctx.Emin = -999_999_999
        approx = (Decimal(numerator >> k_n) / Decimal(denominator >> k_d)) * (Decimal(2) ** (k_n - k_d))
        ctx.prec = 16
        return str((+approx).normalize())


class SimpleCalcEngine:
    def __init__(self, config: CalcConfig):
        self.config = config
        self.primary_spec: FxpSpec | None = None
        self.compare_specs: list[FxpSpec] = []
        self.exact = Fraction(0)
        self.raw_uint = 0
        self.status_kind = "exact"
        self.shift_mode = "arithmetic"
        self.carry_bit = 0
        self.variables: dict[str, VariableSnapshot | None] = dict.fromkeys("MABCDEF")
        self.history: list[tuple[str, VariableSnapshot]] = []

    def set_shift_mode(self, mode: str) -> None:
        if mode not in SHIFT_MODES:
            raise ValueError(f"Unknown shift mode: {mode}")
        self.shift_mode = mode

    @property
    def spec(self) -> FxpSpec:
        return self.primary_spec or INT32

    @property
    def real(self) -> Fraction:
        return raw_uint_to_fraction(self.raw_uint, self.spec)

    def parse_format(self, fmt: str | FxpSpec) -> FxpSpec:
        if isinstance(fmt, str) and len(fmt) > _MAX_INPUT:
            raise ValueError("Format is too long")
        spec = FormatEngine.parse_input(fmt, self.config)
        if abs(spec.fwl) > 256:
            raise ValueError("Fractional width must be between -256 and 256")
        return spec

    def _convert(self, value: Fraction, spec: FxpSpec) -> ScalarQuantizeResult:
        return quantize_scalar(value, spec, quant=self.config.default_quant,
                               overflow=self.config.default_overflow)

    def commit(self, value: Fraction) -> ScalarQuantizeResult:
        result = self._convert(value, self.spec)
        self.exact, self.raw_uint, self.status_kind = result.exact_input, result.raw_uint, result.status_kind
        return result

    def set_raw(self, bits: int) -> None:
        self.raw_uint = reinterp_raw_bits(bits, self.spec)
        self.exact, self.status_kind = self.real, "exact"

    def set_primary(self, fmt: str | FxpSpec | None) -> None:
        exact = self.exact
        self.primary_spec = self.parse_format(fmt) if fmt else None
        self.commit(exact)

    def project(self, fmt: str | FxpSpec) -> ScalarQuantizeResult:
        return self._convert(self.exact, self.parse_format(fmt))

    def edit_projection(self, fmt: str | FxpSpec, value: Fraction) -> None:
        self.commit(self._convert(value, self.parse_format(fmt)).quantized_value)

    def display(self, base: str) -> str:
        if base == "DEC":
            return str(raw_uint_to_signed_int(self.raw_uint, self.spec))
        if base == "REAL":
            return display_fraction(self.exact)
        if base == "FXP":
            return display_fraction(self.real)
        if base == "HEX":
            return f"0x{_group_from_right(f'{self.raw_uint:X}', 4)}"
        if base == "BIN":
            raw_bin = f"{self.raw_uint:b}"
            padded = ("0" * ((-len(raw_bin)) % 4)) + raw_bin
            return "0b " + _group_from_right(padded, 4)
        if base == "OCT":
            return f"0o{_group_from_right(f'{self.raw_uint:o}', 3)}"
        raise ValueError(f"Unknown radix: {base}")

    def parse_operand(self, text: str, base: str, *, unscaled: bool = False) -> Fraction:
        if len(text) > _MAX_INPUT:
            raise ValueError("Input is too long")
        text = "".join(text.split()).replace("_", "")
        prefix = _PREFIXES.get(text[:2].lower())
        if prefix:
            base, text = prefix, text[2:]
            if text.startswith(("+", "-")):
                raise ValueError(f"Invalid {base} literal")
        if base in BASES:
            value = int(text, BASES[base])
            if unscaled:
                return Fraction(value)
            if base != "DEC":
                if not 0 <= value < 1 << self.spec.wl:
                    raise ValueError(f"Raw input is outside WL={self.spec.wl}")
                return raw_uint_to_fraction(value, self.spec)
            return value * Fraction(2) ** -self.spec.fwl
        if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,3})?", text):
            raise ValueError("Enter a valid decimal value")
        return Fraction(Decimal(text))

    def paste(self, text: str, base: str = "REAL") -> ScalarQuantizeResult:
        return self.commit(self.parse_operand(text, base))

    def evaluate(self, expression: str, base: str = "REAL",
                 *, operands: dict[str, Fraction] | None = None,
                 shift_mode: str | None = None) -> ScalarQuantizeResult:
        if len(expression) > _MAX_INPUT:
            raise ValueError("Expression is too long")
        active_shift = shift_mode or self.shift_mode
        if active_shift not in SHIFT_MODES:
            raise ValueError(f"Unknown shift mode: {active_shift}")
        values = dict(operands or {})
        tokens: list[tuple[str, Fraction | str]] = []
        offset = 0
        while offset < len(expression):
            match = _REAL_TOKEN.match(expression, offset) if base in ("REAL", "FXP") else None
            match = match or _TOKEN.match(expression, offset)
            if not match:
                raise ValueError("Invalid character in expression")
            offset = match.end()
            token = match.group()
            if token.isspace():
                continue
            upper = token.upper()
            if token in "+-*/%&|^~()" or token in ("<<", ">>"):
                tokens.append(("OP", token))
            elif upper in ("NAND", "NOR") and token not in values:
                tokens.append(("OP", upper))
            elif token in values:
                tokens.append(("VAL", values[token]))
            else:
                tokens.append(("LIT", token))
        if not tokens:
            raise ValueError("Invalid expression")
        saved_carry = self.carry_bit
        try:
            value, next_pos = self._parse_bitor(tokens, 0, active_shift, base)
            if next_pos != len(tokens):
                raise ValueError("Invalid expression")
            result = self.commit(value)
        except ZeroDivisionError as exc:
            self.carry_bit = saved_carry
            raise ValueError("Division or remainder by zero") from exc
        except BaseException:
            self.carry_bit = saved_carry
            raise
        self.history.append((expression, self.snapshot()))
        del self.history[:-100]
        return result

    def _hardware_int(self, value: Fraction) -> int:
        scaled = value * Fraction(2) ** self.spec.fwl
        if scaled.denominator != 1:
            raise ValueError("Bit operations require integral unscaled operands")
        return raw_uint_to_signed_int(int(scaled), self.spec)

    def _apply_shift(self, value: Fraction, count: Fraction, *, left: bool, shift_mode: str) -> Fraction:
        if count < 0 or count > 256 or count.denominator != 1:
            raise ValueError("Shift count must be an integer between 0 and 256")
        signed_val = self._hardware_int(value)
        wl = self.spec.wl
        mask = (1 << wl) - 1
        u = signed_val & mask
        n = int(count)
        if n == 0:
            return raw_uint_to_fraction(u, self.spec)
        if shift_mode == "arithmetic":
            if left:
                self.carry_bit = (u >> (wl - n)) & 1 if n <= wl else 0
                return raw_uint_to_fraction(signed_val << n, self.spec)
            self.carry_bit = ((u >> (n - 1)) & 1) if n <= wl else (1 if signed_val < 0 else 0)
            return raw_uint_to_fraction(signed_val >> n, self.spec)
        if shift_mode == "logical":
            if left:
                self.carry_bit = (u >> (wl - n)) & 1 if n <= wl else 0
                return raw_uint_to_fraction((u << n) & mask, self.spec)
            self.carry_bit = ((u >> (n - 1)) & 1) if n <= wl else 0
            return raw_uint_to_fraction(u >> n, self.spec)
        if shift_mode == "rotate":
            k = n % wl
            if k == 0:
                return raw_uint_to_fraction(u, self.spec)
            if left:
                res = ((u << k) | (u >> (wl - k))) & mask
                self.carry_bit = res & 1
            else:
                res = ((u >> k) | (u << (wl - k))) & mask
                self.carry_bit = (res >> (wl - 1)) & 1
            return raw_uint_to_fraction(res, self.spec)
        # rotate_carry: (WL + 1)-bit ring [carry | u]
        ring_bits = wl + 1
        k = n % ring_bits
        if k == 0:
            return raw_uint_to_fraction(u, self.spec)
        ring = ((self.carry_bit & 1) << wl) | u
        ring_mask = (1 << ring_bits) - 1
        if left:
            new_ring = ((ring << k) | (ring >> (ring_bits - k))) & ring_mask
        else:
            new_ring = ((ring >> k) | (ring << (ring_bits - k))) & ring_mask
        self.carry_bit = (new_ring >> wl) & 1
        return raw_uint_to_fraction(new_ring & mask, self.spec)

    def _parse_bitor(self, tokens, pos: int, shift_mode: str, base: str,
                     *, unscaled: bool = False, depth: int = 0) -> tuple[Fraction, int]:
        left, pos = self._parse_bitxor(tokens, pos, shift_mode, base, unscaled=unscaled, depth=depth)
        while pos < len(tokens) and tokens[pos] in (("OP", "|"), ("OP", "NOR")):
            op = tokens[pos][1]
            right, pos = self._parse_bitxor(tokens, pos + 1, shift_mode, base, unscaled=unscaled, depth=depth)
            bits = self._hardware_int(left) | self._hardware_int(right)
            left = raw_uint_to_fraction(~bits if op == "NOR" else bits, self.spec)
        return left, pos

    def _parse_bitxor(self, tokens, pos: int, shift_mode: str, base: str,
                      *, unscaled: bool = False, depth: int = 0) -> tuple[Fraction, int]:
        left, pos = self._parse_bitand(tokens, pos, shift_mode, base, unscaled=unscaled, depth=depth)
        while pos < len(tokens) and tokens[pos] == ("OP", "^"):
            right, pos = self._parse_bitand(tokens, pos + 1, shift_mode, base, unscaled=unscaled, depth=depth)
            left = raw_uint_to_fraction(self._hardware_int(left) ^ self._hardware_int(right), self.spec)
        return left, pos

    def _parse_bitand(self, tokens, pos: int, shift_mode: str, base: str,
                      *, unscaled: bool = False, depth: int = 0) -> tuple[Fraction, int]:
        left, pos = self._parse_shift(tokens, pos, shift_mode, base, unscaled=unscaled, depth=depth)
        while pos < len(tokens) and tokens[pos] in (("OP", "&"), ("OP", "NAND")):
            op = tokens[pos][1]
            right, pos = self._parse_shift(tokens, pos + 1, shift_mode, base, unscaled=unscaled, depth=depth)
            bits = self._hardware_int(left) & self._hardware_int(right)
            left = raw_uint_to_fraction(~bits if op == "NAND" else bits, self.spec)
        return left, pos

    def _parse_shift(self, tokens, pos: int, shift_mode: str, base: str,
                     *, unscaled: bool = False, depth: int = 0) -> tuple[Fraction, int]:
        left, pos = self._parse_add(tokens, pos, shift_mode, base, unscaled=unscaled, depth=depth)
        while pos < len(tokens) and tokens[pos] in (("OP", "<<"), ("OP", ">>")):
            op = tokens[pos][1]
            right, pos = self._parse_add(tokens, pos + 1, shift_mode, base, unscaled=True, depth=depth)
            left = self._apply_shift(left, right, left=(op == "<<"), shift_mode=shift_mode)
        return left, pos

    def _parse_add(self, tokens, pos: int, shift_mode: str, base: str,
                   *, unscaled: bool = False, depth: int = 0) -> tuple[Fraction, int]:
        left, pos = self._parse_mul(tokens, pos, shift_mode, base, unscaled=unscaled, depth=depth)
        while pos < len(tokens) and tokens[pos] in (("OP", "+"), ("OP", "-")):
            op = tokens[pos][1]
            right, pos = self._parse_mul(tokens, pos + 1, shift_mode, base, unscaled=unscaled, depth=depth)
            left = _ARITHMETIC_OPS[op](left, right)
        return left, pos

    def _parse_mul(self, tokens, pos: int, shift_mode: str, base: str,
                   *, unscaled: bool = False, depth: int = 0) -> tuple[Fraction, int]:
        left, pos = self._parse_unary(tokens, pos, shift_mode, base, unscaled=unscaled, depth=depth)
        while pos < len(tokens) and tokens[pos] in (("OP", "*"), ("OP", "/"), ("OP", "%")):
            op = tokens[pos][1]
            right, pos = self._parse_unary(tokens, pos + 1, shift_mode, base, unscaled=unscaled, depth=depth)
            left = _ARITHMETIC_OPS[op](left, right)
        return left, pos

    def _parse_unary(self, tokens, pos: int, shift_mode: str, base: str,
                     *, unscaled: bool = False, depth: int = 0) -> tuple[Fraction, int]:
        ops: list[str] = []
        while pos < len(tokens) and tokens[pos][0] == "OP" and tokens[pos][1] in ("+", "-", "~"):
            ops.append(tokens[pos][1])  # type: ignore[arg-type]
            pos += 1
        value, next_pos = self._parse_primary(tokens, pos, shift_mode, base, unscaled=unscaled, depth=depth)
        for op in reversed(ops):
            if op == "-":
                value = -value
            elif op == "~":
                value = raw_uint_to_fraction(~self._hardware_int(value), self.spec)
        return value, next_pos

    def _parse_primary(self, tokens, pos: int, shift_mode: str, base: str,
                       *, unscaled: bool = False, depth: int = 0) -> tuple[Fraction, int]:
        if pos >= len(tokens):
            raise ValueError("Invalid expression")
        kind, val = tokens[pos]
        if kind == "VAL":
            return val, pos + 1  # type: ignore[return-value]
        if kind == "LIT":
            return self.parse_operand(val, base, unscaled=unscaled), pos + 1  # type: ignore[arg-type]
        if kind == "OP" and val == "(":
            if depth >= _MAX_DEPTH:
                raise ValueError(f"Expression is too deeply nested (max {_MAX_DEPTH} levels)")
            expr_val, next_pos = self._parse_bitor(tokens, pos + 1, shift_mode, base, unscaled=unscaled, depth=depth + 1)
            if next_pos >= len(tokens) or tokens[next_pos] != ("OP", ")"):
                raise ValueError("Invalid expression")
            return expr_val, next_pos + 1
        raise ValueError("Invalid expression")

    def read_field(self, high: int, low: int) -> int:
        return read_raw_bit_field(self.raw_uint, self.spec.wl, high, low)

    def write_field(self, high: int, low: int, value: int) -> None:
        self.set_raw(update_raw_bit_field(self.raw_uint, self.spec.wl, high, low, value))

    def snapshot(self, source_kind: str = "FXP") -> VariableSnapshot:
        return VariableSnapshot(self.spec, self.exact, self.raw_uint, source_kind=source_kind)

    def store(self, name: str) -> VariableSnapshot:
        snapshot = self.snapshot()
        self.variables[name] = snapshot
        return snapshot

    def recall(self, value: VariableSnapshot, *, raw: bool = False) -> None:
        if raw:
            self.set_raw(value.raw_uint)
        else:
            self.commit(value.exact_fraction)
