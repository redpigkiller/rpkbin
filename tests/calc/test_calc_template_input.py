"""Configured GUI templates resolve to explicit specs, never guessed hardware widths."""
from dataclasses import replace
import pytest
from rpkbin.calc.core import CalcConfig, FormatEngine, FxpSpec


@pytest.fixture
def cfg():
    return CalcConfig(format_preset="custom", custom_signed_tpl="S{iwl}|{fwl}",
                      custom_unsigned_tpl="U{iwl}|{fwl}", custom_int_tpl="{q_prefix}{wl}.0i")


@pytest.mark.parametrize("text,signed,width,fraction", [
    ("S6|10", True, 16, 10), ("U6|10", False, 16, 10),
    ("S-2|12", True, 10, 12), ("S16|-4", True, 12, -4),
    ("Q16.0i", True, 16, 0), ("UQ16.0i", False, 16, 0),
    ("Q4.12", True, 16, 12), ("sfix16_En12", True, 16, 12),
])
def test_input_has_independently_expected_width_and_signedness(cfg,text,signed,width,fraction):
    assert FormatEngine.parse_input(text,cfg)==FxpSpec(signed,width,fraction)


@pytest.mark.parametrize("template,text", [
    ("W{wl}/F{fwl}", "W16/F12"),
    ("{S_or_U}[{wl},{iwl},{fwl}]", "S[16,4,12]"),
    ("{s_or_u}({wl};{wl};{fwl})", "s(16;16;12)"),
    ("W[{{{wl}}}]F{fwl}", "W[{16}]F12"),
])
def test_simple_placeholder_combinations_and_escaped_literals(cfg,template,text):
    cfg.custom_signed_tpl=template
    spec=FormatEngine.parse_input(text,cfg)
    assert spec==FxpSpec(True,16,12)
    assert FormatEngine.parse_input(FormatEngine.editable(spec,cfg),cfg)==spec


@pytest.mark.parametrize("template,text", [
    ("X{wl}", "X16"), ("X{wl}{fwl}", "X1612"),
    ("X{iwl}0{fwl}", "X10012"), ("X{wl}1{fwl}", "X12112"),
    ("X{wl:03d}/{fwl}", "X016/12"), ("X{wl!r}/{fwl}", "X16/12"),
    ("X{wl.real}/{fwl}", "X16/12"), ("X{unknown}/{fwl}", "X16/12"),
    ("X{wl}/{iwl}/{fwl}", "X16/5/12"),
    ("X{wl}/{wl}/{fwl}", "X16/17/12"),
    ("X{wl}/{fwl}", "X0/12"), ("X{wl}/{fwl}", "X257/12"),
])
def test_incomplete_advanced_inconsistent_or_out_of_range_templates_do_not_guess(cfg,template,text):
    cfg.custom_signed_tpl=template
    with pytest.raises(ValueError):
        FormatEngine.parse_input(text,cfg)
    spec=FxpSpec(True,16,12)
    assert FormatEngine.parse_input(FormatEngine.editable(spec,cfg),cfg)==spec


def test_ambiguous_signedness_and_standard_notation_are_rejected(cfg):
    cfg.custom_signed_tpl=cfg.custom_unsigned_tpl="X{wl}/{fwl}"
    with pytest.raises(ValueError,match="Ambiguous"):
        FormatEngine.parse_input("X16/12",cfg)
    cfg.custom_signed_tpl="Q{wl}.{fwl}"
    with pytest.raises(ValueError,match="Ambiguous"):
        FormatEngine.parse_input("Q4.12",cfg)
    spec=FxpSpec(True,16,12)
    assert FormatEngine.parse_input(FormatEngine.editable(spec,cfg),cfg)==spec


def test_custom_names_are_not_global_aliases_and_matching_is_bounded(cfg):
    with pytest.raises(ValueError):
        FormatEngine.parse_input("S6|10",replace(cfg,format_preset="q_format"))
    with pytest.raises(ValueError):
        FormatEngine.parse("S6|10")
    cfg.custom_signed_tpl="X"*513+"{wl}/{fwl}"
    with pytest.raises(ValueError):
        FormatEngine.parse_input("X"*513+"16/12",cfg)


def test_integer_template_needs_a_reversible_signedness_marker(cfg):
    cfg.custom_int_tpl = "u{wl}.0i"
    with pytest.raises(ValueError):
        FormatEngine.parse_input("s4.0i", cfg)
    with pytest.raises(ValueError, match="Ambiguous"):
        FormatEngine.parse_input("u4.0i", cfg)
    cfg.custom_int_tpl = "{s_or_u}{wl}.0i"
    assert FormatEngine.parse_input("s4.0i", cfg) == FxpSpec(True, 4, 0)
    assert FormatEngine.parse_input("u4.0i", cfg) == FxpSpec(False, 4, 0)
    assert FormatEngine.format(FxpSpec(True, 4, 0), cfg) == "s4.0i"
    assert FormatEngine.format(FxpSpec(False, 4, 0), cfg) == "u4.0i"
