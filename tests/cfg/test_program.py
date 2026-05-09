"""Tests for rpkbin.cfg.program."""

import pytest

from rpkbin.cfg import CFG, CallRef, Program


def _main_cfg():
    cfg = CFG()
    cfg.add_block("entry")
    cfg.set_entry("entry")
    return cfg


def test_program_mapping_helpers():
    main = _main_cfg()
    program = Program({"main": main})
    assert program["main"] is main
    assert list(program) == ["main"]
    assert len(program) == 1


def test_program_empty_rejected():
    with pytest.raises(ValueError):
        Program({})


def test_program_missing_entry_rejected():
    with pytest.raises(KeyError, match="Entry function"):
        Program({"helper": _main_cfg()}, entry_fn="main")


def test_program_format_includes_all_cfgs():
    main = _main_cfg()
    helper = CFG()
    helper.add_block("body")
    helper.set_entry("body")

    program = Program({"main": main, "helper": helper})
    text = program.format()

    assert "Program" in text
    assert "entry_fn='main'" in text
    assert "Function main" in text
    assert "Function helper" in text
    assert "CFG" in text


def test_program_format_can_filter_functions_and_hide_call_graph():
    main = _main_cfg()
    helper = CFG()
    helper.add_block("body")
    helper.set_entry("body")

    program = Program({"main": main, "helper": helper})
    text = program.format(fn_names=["helper"], show_call_graph=False)

    assert "Function helper" in text
    assert "Function main" not in text
    assert "Call graph" not in text


def test_program_format_call_graph_summary():
    main = CFG()
    main.add_block("entry", insns=[CallRef("helper")])
    main.set_entry("entry")
    helper = CFG()
    helper.add_block("body")
    helper.set_entry("body")

    program = Program({"main": main, "helper": helper})
    text = str(program)

    assert "Call graph" in text
    assert "main -> helper" in text
    assert "at entry" in text


def test_program_format_can_hide_call_sites():
    main = CFG()
    main.add_block("entry", insns=[CallRef("helper")])
    main.set_entry("entry")
    helper = CFG()
    helper.add_block("body")
    helper.set_entry("body")

    program = Program({"main": main, "helper": helper})
    text = program.format(show_call_sites=False)

    assert "main -> helper" in text
    assert "at entry" not in text


def test_program_format_can_hide_empty_call_graph():
    program = Program({"main": _main_cfg()})
    text = program.format(show_empty_call_graph=False)

    assert "Call graph" not in text


def test_program_validate_collects_cfg_issues_and_unreachable():
    cfg = CFG()
    cfg.add_block("entry")
    cfg.add_block("dead")
    cfg.set_entry("entry")
    program = Program({"main": cfg})

    issues = program.validate()

    assert any("main" in issue and "unreachable" in issue and "dead" in issue for issue in issues)


def test_program_validate_can_check_call_depth():
    main = CFG()
    main.add_block("entry", insns=[CallRef("a")])
    main.set_entry("entry")
    a = CFG()
    a.add_block("entry", insns=[CallRef("b")])
    a.set_entry("entry")
    b = CFG()
    b.add_block("entry")
    b.set_entry("entry")
    program = Program({"main": main, "a": a, "b": b})

    issues = program.validate(max_call_depth=1)

    assert any("Call depth" in issue for issue in issues)
