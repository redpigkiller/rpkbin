"""Native APyTypes declarations and the independent GUI format boundary."""

from dataclasses import replace

import apytypes as apy
import pytest

from rpkbin.calc.core import CalcConfig, FormatEngine, FxpSpec, WorkspaceSession
from rpkbin.calc.core.math_helpers import build_namespace_symbols


def test_native_classes_are_preloaded_without_constructor_wrappers():
    session = WorkspaceSession(CalcConfig(startup_script=""), seed_demo_vars=False)
    for name in ("APyFixed", "APyFixedArray", "APyFloat", "APyFloatArray"):
        assert session.user_ns[name] is getattr(apy, name)
        assert name not in session.user_variable_names()
    assert session.user_ns["apy"] is apy
    assert "fxp" not in session.user_ns and "fxpi" not in session.user_ns
    assert "NameError" in session.execute("fxp(1)").error_text


def test_raw_constructor_and_real_conversion_keep_native_semantics():
    session = WorkspaceSession(CalcConfig(startup_script=""), seed_demo_vars=False)
    result = session.execute(
        "raw = APyFixed(7, 4, 2)\n"
        "real = apy.APyFixed.from_float(7, int_bits=4, frac_bits=2)\n"
        "A = APyFixedArray.from_float([[.25, -.125]], bits=16, frac_bits=12)\n"
        "f = APyFloat.from_float(.25, exp_bits=8, man_bits=23)\n"
        "F = APyFloatArray.from_float([.25], exp_bits=8, man_bits=23)"
    )
    assert not result.error_text
    assert type(session.user_ns["raw"]) is apy.APyFixed
    assert float(session.user_ns["raw"]) == 1.75
    assert float(session.user_ns["real"]) == 7
    snapshot = session.get_inspector_snapshot("raw")
    assert snapshot.format_str == "Q4.2"
    assert snapshot.uint_val == 7 and snapshot.status_kind == "untracked"
    assert session.get_matrix_snapshot("A").cells_real == [["0.25", "-0.125"]]
    assert float(session.user_ns["f"]) == .25
    assert set(session.user_variable_names()) == {"raw", "real", "A", "f", "F"}


def test_gui_conversion_policy_does_not_override_native_constructors():
    cfg = CalcConfig(startup_script="", default_quant="TRN", default_overflow="SAT")
    session = WorkspaceSession(cfg, seed_demo_vars=False)
    assert not session.execute(
        "rounded = APyFixed.from_float(.1, bits=8, frac_bits=4)\n"
        "wrapped = APyFixed.from_float(9, bits=8, frac_bits=4)\n"
        "converted = .1"
    ).error_text
    assert float(session.user_ns["rounded"]) == .125  # APyTypes nearest, not GUI floor
    assert float(session.user_ns["wrapped"]) == -7  # APyTypes wrap, not GUI saturation
    session.reformat_variable("converted", "Q4.4", pin_mode="real")
    assert float(session.user_ns["converted"]) == .0625
    assert session.get_inspector_snapshot("converted").status_kind == "quant_err"


def test_startup_script_can_define_its_own_constructor_and_survives_reload():
    cfg = CalcConfig(startup_script=(
        "def fxp(value, bits=16, frac_bits=12):\n"
        "    return APyFixed.from_float(value, bits=bits, frac_bits=frac_bits)\n"
    ))
    session = WorkspaceSession(cfg, seed_demo_vars=False)
    assert not session.execute("a = fxp(.25)").error_text
    assert type(session.user_ns["a"]) is apy.APyFixed
    assert float(session.user_ns["a"]) == .25
    assert "fxp" not in session.user_variable_names()
    session.reload_auto_imports()
    assert session.user_ns["APyFixed"] is apy.APyFixed
    assert float(session.user_ns["fxp"](.5)) == .5
    assert float(session.user_ns["a"]) == .25
    session.user_ns["APyFixed"] = "user binding"
    session.reload_auto_imports()
    assert session.user_ns["APyFixed"] == "user binding"


@pytest.mark.parametrize("text", ["s10.12f", "u10.12f", "S10.12", "s8i", "u8", "APyFixed(10, -2)"])
def test_private_and_fake_constructor_format_strings_are_rejected(text):
    with pytest.raises(ValueError):
        FormatEngine.parse(text)


def test_custom_display_input_roundtrips_and_conflicting_display_falls_back():
    cfg = CalcConfig(startup_script="", format_preset="custom", custom_signed_tpl="signed:{wl}/{fwl}")
    spec = FxpSpec(True, 16, 12)
    assert FormatEngine.format(spec, cfg) == "signed:16/12"
    assert FormatEngine.editable(spec, cfg) == "signed:16/12"
    assert FormatEngine.parse_input(FormatEngine.editable(spec, cfg), cfg) == spec
    # Even a template that looks valid must not silently declare a different format.
    cfg.custom_signed_tpl = "Q99.{fwl}"
    assert FormatEngine.editable(spec, cfg) == "Q4.12"
    cfg = replace(cfg, format_preset="matlab")
    assert FormatEngine.editable(spec, cfg) == "sfix16_En12"


def test_default_namespace_does_not_install_user_convenience_helpers():
    symbols = build_namespace_symbols(CalcConfig())
    assert "fxp" not in symbols and "fxpi" not in symbols
    assert FormatEngine.format(FxpSpec()) == "Q1.15"


def test_native_array_gui_provenance_preserves_identity_aliases_and_undo():
    from rpkbin.calc.core.editing import EditJournal
    session = WorkspaceSession(CalcConfig(startup_script=""), seed_demo_vars=False)
    session.execute("A = APyFixedArray.from_float([0], bits=8, frac_bits=4)\nalias = A")
    owner = session.user_ns["A"]
    journal = EditJournal(session)
    journal.apply([("A", (0, 0))], lambda: session.update_target_field("A", (0, 0), "real", "99"))
    assert type(owner) is apy.APyFixedArray
    assert session.user_ns["A"] is session.user_ns["alias"] is owner
    assert session.get_inspector_snapshot("alias", (0, 0)).status_kind == "saturated"
    assert session._array_saturation_status(owner) is True
    journal.replay()
    assert float(owner[0]) == 0
    assert session.get_inspector_snapshot("A", (0, 0)).status_kind == "untracked"
    journal.replay(redo=True)
    assert session._array_saturation_status(owner) is True
    duplicate = session.duplicate_variable("A")
    assert session.user_ns[duplicate] is not owner
    assert session._array_saturation_status(session.user_ns[duplicate]) is True
    session.execute("alias[0] = APyFixed.from_float(0, bits=8, frac_bits=4)")
    assert session.get_inspector_snapshot("A", (0, 0)).status_kind == "untracked"
    assert session._array_saturation_status(owner) is None
    assert session._array_saturation_status(session.user_ns[duplicate]) is True
    session.delete_variable("A")
    session.delete_variable("alias")
    session.list_variables()
    assert id(owner) not in session._native_sources
    session.reset()
    assert not session._native_sources


def test_large_native_array_selected_source_does_not_claim_whole_array_scan():
    session = WorkspaceSession(CalcConfig(startup_script=""), seed_demo_vars=False)
    session.execute("A = APyFixedArray.from_float(np.zeros(4097), bits=8, frac_bits=4)")
    session.update_target_field("A", (0, 0), "real", "99")
    assert session.get_inspector_snapshot("A", (0, 0)).status_kind == "saturated"
    assert session._array_saturation_status(session.user_ns["A"]) is None
