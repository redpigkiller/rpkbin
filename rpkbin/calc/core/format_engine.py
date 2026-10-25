"""Fixed-Point (FXP) format parsing and display formatting engine."""

from __future__ import annotations

import re
from string import Formatter
from dataclasses import dataclass

from .config import CalcConfig


@dataclass(frozen=True)
class FxpSpec:
    """Hardware Fixed-Point format specification.

    Attributes:
        signed: True for 2's complement signed ('s'), False for unsigned ('u').
        wl: Total hardware word length in bits (must be >= 1).
        fwl: Fractional word length in bits (can be negative, zero, or > wl).
    """

    signed: bool = True
    wl: int = 16
    fwl: int = 15

    def __post_init__(self) -> None:
        if self.wl < 1:
            raise ValueError(f"Word length (wl) must be >= 1, got {self.wl}")
        if self.wl > 256:
            raise ValueError(f"Word length (wl) must be <= 256, got {self.wl}")

    @property
    def iwl(self) -> int:
        """Integer word length (including sign bit for signed numbers): wl - fwl."""
        return self.wl - self.fwl

    @property
    def apy_bits(self) -> int:
        """Underlying APyFixed bits (unsigned uses wl + 1 so MSB sign bit is 0)."""
        return self.wl if self.signed else self.wl + 1

    @property
    def apy_int_bits(self) -> int:
        """Underlying APyFixed int_bits."""
        return self.apy_bits - self.fwl

    @property
    def lsb(self) -> float:
        """Resolution (step size) per LSB in real units."""
        return float(2.0 ** (-self.fwl))

    @property
    def min_raw_int(self) -> int:
        """Minimum representable unscaled integer."""
        return -(1 << (self.wl - 1)) if self.signed else 0

    @property
    def max_raw_int(self) -> int:
        """Maximum representable unscaled integer."""
        return ((1 << (self.wl - 1)) - 1) if self.signed else ((1 << self.wl) - 1)

    @property
    def min_real(self) -> float:
        """Minimum representable real value."""
        return self.min_raw_int * self.lsb

    @property
    def max_real(self) -> float:
        """Maximum representable real value."""
        return self.max_raw_int * self.lsb


# UI format notation (REPL constructors use native APyTypes parameters).
# Q-format: Q[IWL].FWL or UQ[IWL].FWL or Q15 (e.g. Q-2.12, UQ0.12, Q3.12, Q15)
_RE_Q_FMT = re.compile(
    r"^\s*(u|U|s|S)?[qQ]\s*(?:([+-]?\d+)\s*\.\s*([+-]?\d+)|(\d+))\s*$"
)
# MATLAB fixdt short: sfix10_En12, ufix10_En12, sfix10_E2, ufix8
_RE_MATLAB = re.compile(
    r"^\s*([sSuU])fix(\d+)(?:_[eE](n?)(\d+))?\s*$"
)


class FormatEngine:
    """Bidirectional parser and formatter for FXP specifications."""

    @staticmethod
    def canonical(spec: FxpSpec) -> str:
        """Qm.n with m including the sign position; no implicit sign bit."""
        return f"{'Q' if spec.signed else 'UQ'}{spec.iwl}.{spec.fwl}"

    @staticmethod
    def editable(spec: FxpSpec, config: CalcConfig) -> str:
        """Use configured text only when it uniquely round-trips to this spec."""
        choices = (FormatEngine.format(spec, config), FormatEngine.canonical(spec),
                   FormatEngine.format(spec, CalcConfig(format_preset="matlab")))
        for text in choices:
            try:
                if FormatEngine.parse_input(text, config) == spec:
                    return text
            except ValueError:
                pass
        return FormatEngine.canonical(spec)

    @staticmethod
    def parse_input(text: str | FxpSpec, config: CalcConfig) -> FxpSpec:
        """Resolve GUI input; conflicting templates never silently change meaning."""
        if isinstance(text, FxpSpec):
            return text
        candidates = set()
        try:
            candidates.add(FormatEngine.parse(text))
        except ValueError:
            pass
        if config.format_preset == "custom" and isinstance(text, str) and len(text) <= 512:
            for template, signed, integer in (
                    (config.custom_signed_tpl, True, False), (config.custom_unsigned_tpl, False, False),
                    (config.custom_int_tpl, True, True), (config.custom_int_tpl, False, True)):
                candidates.update(_template_specs(text.strip(), template, signed, integer))
        if len(candidates) == 1:
            return candidates.pop()
        if len(candidates) > 1:
            raise ValueError("Ambiguous format: the custom templates and standard formats disagree. "
                             "Use an unambiguous Q/MATLAB format or adjust the templates.")
        raise ValueError("Enter a Q/MATLAB format or a uniquely reversible current custom template.")

    @staticmethod
    def parse(text: str | FxpSpec) -> FxpSpec:
        """Parse any supported FXP format string into an FxpSpec.

        Qm.n includes sign in m (bits=m+n); Q15 means signed 16/15.
        MATLAB short notation such as sfix16_En12 is also accepted.
        This standard parser ignores templates; parse_input resolves GUI templates.
        """
        if isinstance(text, FxpSpec):
            return text
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Format string cannot be empty")

        s = text.strip()

        # Q-Format: Q{iwl}.{fwl} or UQ{iwl}.{fwl} or Q{fwl}
        m = _RE_Q_FMT.match(s)
        if m:
            prefix, iwl_s, fwl_s, single_q = m.groups()
            signed = (prefix or "s").lower() != "u"
            if single_q is not None:
                fwl = int(single_q)
                wl = fwl + (1 if signed else 0)
                return FxpSpec(signed=signed, wl=wl, fwl=fwl)
            iwl = int(iwl_s)
            fwl = int(fwl_s)
            wl = iwl + fwl
            if wl < 1:
                raise ValueError(
                    f"Invalid Q-format '{s}': total word length (iwl + fwl = {wl}) must be >= 1"
                )
            return FxpSpec(signed=signed, wl=wl, fwl=fwl)

        # MATLAB Short: sfix10_En12, ufix10_En12, sfix10
        m = _RE_MATLAB.match(s)
        if m:
            sign_ch, wl_s, neg_flag, exp_s = m.groups()
            signed = sign_ch.lower() == "s"
            wl = int(wl_s)
            if exp_s is None:
                fwl = 0
            else:
                exp_val = int(exp_s)
                # En12 means slope 2^-12 -> fwl = +12; E2 means slope 2^2 -> fwl = -2
                fwl = exp_val if neg_flag == "n" else -exp_val
            return FxpSpec(signed=signed, wl=wl, fwl=fwl)

        raise ValueError(
            f"Cannot parse FXP format '{s}'. Use Q4.12, UQ4.12, Q15 or sfix16_En12. "
            "In Qm.n, m includes the sign bit; total bits = m + n."
        )

    @staticmethod
    def format(spec: FxpSpec, config: CalcConfig | None = None) -> str:
        """Format an FxpSpec according to the user's configured notation preset."""
        preset = config.format_preset if config is not None else "q_format"
        s_or_u = "s" if spec.signed else "u"
        S_or_U = "S" if spec.signed else "U"

        if preset == "q_format":
            return FormatEngine.canonical(spec)

        if preset == "matlab":
            prefix = "sfix" if spec.signed else "ufix"
            if spec.fwl == 0:
                return f"{prefix}{spec.wl}"
            if spec.fwl > 0:
                return f"{prefix}{spec.wl}_En{spec.fwl}"
            return f"{prefix}{spec.wl}_E{abs(spec.fwl)}"

        if preset == "custom" and config is not None:
            tpl = (
                config.custom_int_tpl
                if spec.fwl == 0
                else (
                    config.custom_signed_tpl
                    if spec.signed
                    else config.custom_unsigned_tpl
                )
            )
            try:
                return tpl.format(
                    wl=spec.wl,
                    iwl=spec.iwl,
                    fwl=spec.fwl,
                    s_or_u=s_or_u,
                    S_or_U=S_or_U,
                    q_prefix="Q" if spec.signed else "UQ",
                )
            except Exception:
                return FormatEngine.canonical(spec)

        # Fallback default
        return FormatEngine.canonical(spec)


def _template_specs(text, template, signed, integer):
    """Reverse simple named placeholders only; no eval, regex input, or guessed widths."""
    if not isinstance(template, str) or len(template) > 512:
        return set()
    fixed = {"q_prefix": "Q" if signed else "UQ", "s_or_u": "s" if signed else "u",
             "S_or_U": "S" if signed else "U"}
    pattern, fields = [], []
    previous_numeric = False
    try:
        for literal, name, specifier, conversion in Formatter().parse(template.strip()):
            pattern.append(re.escape(literal))
            if name is None:
                continue
            if specifier or conversion or len(fields) >= 16:
                return set()
            if name in fixed:
                pattern.append(re.escape(fixed[name]))
                previous_numeric = False
            elif name in ("wl", "iwl", "fwl"):
                if previous_numeric and re.fullmatch(r"[0-9]*", literal):
                    return set()  # Empty/digit-only boundaries cannot uniquely divide numbers.
                pattern.append(r"([+-]?[0-9]{1,6})")
                fields.append(name)
                previous_numeric = True
            else:
                return set()
        match = re.fullmatch("".join(pattern), text)
        if match is None:
            return set()
        values = {}
        for name, value in zip(fields, match.groups()):
            number = int(value)
            if name in values and values[name] != number:
                return set()
            values[name] = number
        if integer:
            if values.get("fwl", 0) != 0:
                return set()
            values["fwl"] = 0
        if len(values) < 2:
            return set()
        if "wl" not in values:
            values["wl"] = values["iwl"] + values["fwl"]
        elif "fwl" not in values:
            values["fwl"] = values["wl"] - values["iwl"]
        if "iwl" in values and values["iwl"] != values["wl"] - values["fwl"]:
            return set()
        return {FxpSpec(signed=signed, wl=values["wl"], fwl=values["fwl"])}
    except (ValueError, KeyError):
        return set()
