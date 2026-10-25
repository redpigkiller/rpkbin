"""Headless core tests for rpk-calc."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from rpkbin.calc.core import (
    CalcConfig,
    FormatEngine,
    FxpSpec,
    WorkspaceSession,
    make_fxp,
)


def test_core_has_zero_gui_dependencies() -> None:
    """Ensure rpkbin.calc.core modules do not import PySide6."""
    import rpkbin.calc.core.config as m_cfg
    import rpkbin.calc.core.format_engine as m_fmt
    import rpkbin.calc.core.fxp_model as m_fxp
    import rpkbin.calc.core.math_helpers as m_math
    import rpkbin.calc.core.session as m_sess

    for mod in (m_cfg, m_fmt, m_fxp, m_math, m_sess):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "PySide6" not in src, f"{mod.__name__} must not import PySide6"


def test_format_engine_parse_and_format() -> None:
    # Qm.n includes the sign position in m for signed values.
    s1 = FormatEngine.parse("Q-2.12")
    assert (s1.signed, s1.wl, s1.iwl, s1.fwl) == (True, 10, -2, 12)

    u1 = FormatEngine.parse("UQ-2.12")
    assert (u1.signed, u1.wl, u1.iwl, u1.fwl) == (False, 10, -2, 12)

    i1 = FormatEngine.parse("Q10.0")
    assert (i1.signed, i1.wl, i1.iwl, i1.fwl) == (True, 10, 10, 0)

    # Reject formats with no hardware bits.
    with pytest.raises(ValueError):
        FormatEngine.parse("Q-2.2")

    # Q-format (IWL.FWL) and Q15 shorthand
    q1 = FormatEngine.parse("Q-2.12")
    assert q1 == FxpSpec(signed=True, wl=10, fwl=12)

    uq1 = FormatEngine.parse("UQ0.12")
    assert uq1 == FxpSpec(signed=False, wl=12, fwl=12)

    q15 = FormatEngine.parse("Q15")
    assert q15 == FxpSpec(signed=True, wl=16, fwl=15)

    # MATLAB fixdt short
    m1 = FormatEngine.parse("sfix10_En12")
    assert m1 == FxpSpec(signed=True, wl=10, fwl=12)

    # Formatting across presets
    cfg = CalcConfig()
    assert FormatEngine.format(s1, cfg) == "Q-2.12"
    assert FormatEngine.format(i1, cfg) == "Q10.0"
    assert FormatEngine.parse(FormatEngine.format(s1, cfg)) == s1

    cfg.format_preset = "matlab"
    assert FormatEngine.format(s1, cfg) == "sfix10_En12"
    assert FormatEngine.parse(FormatEngine.format(s1, cfg)) == s1

    cfg.format_preset = "custom"
    cfg.custom_signed_tpl = "FXP_{S_or_U}({wl},{iwl},{fwl})"
    assert FormatEngine.format(s1, cfg) == "FXP_S(10,-2,12)"
    assert FormatEngine.parse_input(FormatEngine.editable(s1, cfg), cfg) == s1


def test_native_fixed_workflow_and_explicit_fixed_operand_arithmetic() -> None:
    session = WorkspaceSession(CalcConfig(), seed_demo_vars=False)

    # A narrow fixed-point value cannot represent the Python operand 2.
    # An explicit second format preserves APyTypes' native full-precision sum.
    session.execute('a = APyFixed.from_float(-0.03, bits=10, frac_bits=12)')
    session.execute("b = a + APyFixed.from_float(2, bits=4, frac_bits=0)")

    snap_a = session.get_inspector_snapshot("a")
    assert snap_a is not None
    assert snap_a.format_str == "Q-2.12"
    assert snap_a.hex_str == "0x385"
    assert snap_a.s2_int == -123
    assert snap_a.uint_val == 901
    assert snap_a.c1_int == -122
    assert pytest.approx(snap_a.real_val, rel=1e-9) == -123 / 4096.0
    assert snap_a.status_kind == "untracked"

    # The explicitly represented operand contributes four integer bits.
    snap_b = session.get_inspector_snapshot("b")
    assert snap_b is not None
    assert snap_b.format_str == "Q5.12"
    assert snap_b.hex_str == "0x01F85"
    assert snap_b.s2_int == 8069
    assert pytest.approx(snap_b.real_val, rel=1e-9) == 8069 / 4096.0


def test_pin_mode_real_vs_hex_when_changing_format() -> None:
    session = WorkspaceSession(CalcConfig(), seed_demo_vars=False)
    session.execute('x = APyFixed.from_float(-0.03, bits=10, frac_bits=12)')  # Hex = 0x385, s2 = -123

    # 1. Pin Hex (Raw Bits): changing format to Q10.0 keeps 0x385 and re-interprets Real Value as -123
    session.reformat_variable("x", "Q10.0", pin_mode="hex")
    snap_hex_pinned = session.get_inspector_snapshot("x")
    assert snap_hex_pinned is not None
    assert snap_hex_pinned.format_str == "Q10.0"
    assert snap_hex_pinned.hex_str == "0x385"
    assert snap_hex_pinned.real_val == -123.0

    # 2. Pin Hex to UQ10.0: keeps 0x385 and re-interprets Real Value as +901
    session.reformat_variable("x", "UQ10.0", pin_mode="hex")
    snap_unsigned = session.get_inspector_snapshot("x")
    assert snap_unsigned is not None
    assert snap_unsigned.format_str == "UQ10.0"
    assert snap_unsigned.hex_str == "0x385"
    assert snap_unsigned.real_val == 901.0
    # Unsigned MSB must be labeled '9' (not 'S')
    assert snap_unsigned.bits_msb_to_lsb[0].label == "9"
    assert snap_unsigned.bits_msb_to_lsb[0].role == "int"

    # 3. Pin Real Value: changing format of y = -0.03 (Q-2.12) to Q2.8 keeps Real Value ~ -0.03
    session.execute('y = APyFixed.from_float(-0.03, bits=10, frac_bits=12)')
    session.reformat_variable("y", "Q2.8", pin_mode="real")
    snap_real_pinned = session.get_inspector_snapshot("y")
    assert snap_real_pinned is not None
    assert snap_real_pinned.format_str == "Q2.8"
    # -0.03 * 256 = -7.68 -> rounds to -8 (0x3F8), real = -8/256 = -0.03125
    assert snap_real_pinned.s2_int == -8
    assert snap_real_pinned.hex_str == "0x3F8"


def test_matrix_inspection_and_cell_editing() -> None:
    session = WorkspaceSession(CalcConfig(), seed_demo_vars=False)
    # GUI conversion fixtures retain the source needed for SAT diagnostics.
    session.user_ns["A"] = make_fxp([[0.25, -0.125, 0.05], [-0.03, 0.1, -0.2]], "Q-2.12")

    mat = session.get_matrix_snapshot("A", (1, 0))
    assert mat is not None
    assert mat.shape == (2, 3)
    # 0.25 saturates in Q-2.12 (max = 511/4096 = 0.124755859375 -> 0x1FF)
    assert mat.cells_hex[0][0] == "0x1FF"
    # -0.03 at A[1, 0] -> 0x385
    assert mat.cells_hex[1][0] == "0x385"

    # Edit A[0, 1] via hex field
    session.update_target_field("A", (0, 1), "hex", "0x100")
    snap_cell = session.get_inspector_snapshot("A", (0, 1))
    assert snap_cell is not None
    assert snap_cell.hex_str == "0x100"
    assert snap_cell.real_val == 256 / 4096.0


def test_engineering_math_and_auto_imports() -> None:
    session = WorkspaceSession(CalcConfig(), seed_demo_vars=True)
    res1 = session.execute("c_val = cos(a)")
    assert not res1.error_text
    assert pytest.approx(session.user_ns["c_val"], rel=1e-6) == float(
        np.cos(float(session.user_ns["a"]))
    )

    res2 = session.execute("u, s, vh = svd(A)")
    assert not res2.error_text
    assert len(session.user_ns["s"]) == 2

    res3 = session.execute("q_val = qfunc(0)")
    assert not res3.error_text
    assert pytest.approx(session.user_ns["q_val"], rel=1e-9) == 0.5
