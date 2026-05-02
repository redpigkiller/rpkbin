"""Tests for rpkbin.cfg.program."""

import pytest

from rpkbin.cfg import CFG, Program


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
