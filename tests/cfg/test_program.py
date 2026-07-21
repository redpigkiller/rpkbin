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


def test_program_validate_reports_callref_added_after_construction():
    cfg = _main_cfg()
    program = Program({"main": cfg})
    cfg.get_block("entry").insns.append(CallRef("missing"))

    assert any("unknown function 'missing'" in issue for issue in program.validate())


# ---------------------------------------------------------------------------
# Program.function_order
# ---------------------------------------------------------------------------

def _simple_cfg():
    cfg = CFG()
    cfg.add_block("entry")
    cfg.set_entry("entry")
    return cfg


class TestFunctionOrder:
    def _make_program(self, fn_names, entry="main"):
        """Build a Program with plain CFGs (no calls between them)."""
        cfgs = {fn: _simple_cfg() for fn in fn_names}
        return Program(cfgs, entry_fn=entry)

    def _make_call_program(self):
        """main -> a -> c,  main -> b,  orphan (unreachable from main)."""
        from rpkbin.cfg import CallRef

        main = CFG()
        main.add_block("m", insns=[CallRef("a"), CallRef("b")])
        main.set_entry("m")

        a = CFG()
        a.add_block("a_body", insns=[CallRef("c")])
        a.set_entry("a_body")

        b = CFG()
        b.add_block("b_body")
        b.set_entry("b_body")

        c = CFG()
        c.add_block("c_body")
        c.set_entry("c_body")

        orphan = CFG()
        orphan.add_block("o")
        orphan.set_entry("o")

        return Program({"main": main, "a": a, "b": b, "c": c, "orphan": orphan})

    # -- strategy: entry_first -----------------------------------------------

    def test_entry_first_puts_entry_first(self):
        program = self._make_program(["helper", "main", "sub"], entry="main")
        result = program.function_order()
        assert result[0] == "main"

    def test_entry_first_entry_appears_once(self):
        program = self._make_program(["helper", "main", "sub"], entry="main")
        result = program.function_order()
        assert result.count("main") == 1

    def test_entry_first_others_keep_insertion_order(self):
        program = self._make_program(["helper", "main", "sub"], entry="main")
        result = program.function_order()
        non_entry = [fn for fn in result if fn != "main"]
        assert non_entry == ["helper", "sub"]

    # -- strategy: insertion --------------------------------------------------

    def test_insertion_preserves_dict_order(self):
        program = self._make_program(["helper", "main", "sub"], entry="main")
        result = program.function_order("insertion")
        assert result == ["helper", "main", "sub"]

    # -- strategy: call_dfs ---------------------------------------------------

    def test_call_dfs_caller_before_reachable_callee(self):
        program = self._make_call_program()
        result = program.function_order("call_dfs")
        assert result.index("main") < result.index("a")
        assert result.index("main") < result.index("b")
        assert result.index("a") < result.index("c")

    def test_call_dfs_unreachable_appended_in_insertion_order(self):
        program = self._make_call_program()
        result = program.function_order("call_dfs")
        # "orphan" is not reachable from main via calls
        assert "orphan" in result
        # All 5 functions must be present
        assert set(result) == {"main", "a", "b", "c", "orphan"}

    # -- strategy: custom -----------------------------------------------------

    def test_custom_returns_specified_order_then_remaining(self):
        program = self._make_program(["helper", "main", "sub"], entry="main")
        result = program.function_order("custom", order=["sub", "main"])
        assert result[:2] == ["sub", "main"]
        assert result[2] == "helper"

    def test_custom_strict_requires_all_functions(self):
        program = self._make_program(["helper", "main", "sub"], entry="main")
        with pytest.raises(ValueError, match="strict"):
            program.function_order("custom", order=["sub", "main"], strict=True)

    def test_custom_strict_accepts_complete_order(self):
        program = self._make_program(["helper", "main", "sub"], entry="main")
        result = program.function_order(
            "custom", order=["sub", "main", "helper"], strict=True
        )
        assert result == ["sub", "main", "helper"]

    def test_custom_rejects_unknown_name(self):
        program = self._make_program(["main"], entry="main")
        with pytest.raises(KeyError):
            program.function_order("custom", order=["main", "ghost"])

    def test_custom_rejects_duplicate_names(self):
        program = self._make_program(["main", "sub"], entry="main")
        with pytest.raises(ValueError, match="uplicate"):
            program.function_order("custom", order=["main", "main"])

    # -- unknown strategy -----------------------------------------------------

    def test_unknown_strategy_raises_value_error(self):
        program = self._make_program(["main"])
        with pytest.raises(ValueError, match="Unknown"):
            program.function_order("magic")  # type: ignore[arg-type]

    # -- strategy: bottom_up --------------------------------------------------

    def test_bottom_up_callee_before_caller(self):
        """bottom_up places callees before their callers."""
        program = self._make_call_program()
        result = program.function_order("bottom_up")
        # 'c' is called by 'a', which is called by 'main' -> c before a before main
        assert result.index("c") < result.index("a")
        assert result.index("a") < result.index("main")

    def test_bottom_up_entry_is_last_among_reachable(self):
        """The entry function appears last among reachable functions."""
        program = self._make_call_program()
        result = program.function_order("bottom_up")
        reachable = {"main", "a", "b", "c"}
        reachable_in_result = [fn for fn in result if fn in reachable]
        assert reachable_in_result[-1] == "main"

    def test_bottom_up_unreachable_appended_in_insertion_order(self):
        """Functions not reachable from entry are appended at the end."""
        program = self._make_call_program()
        result = program.function_order("bottom_up")
        assert "orphan" in result
        assert set(result) == {"main", "a", "b", "c", "orphan"}
        # orphan is unreachable so it should come after all reachable functions
        reachable = {"main", "a", "b", "c"}
        last_reachable_idx = max(result.index(fn) for fn in reachable)
        assert result.index("orphan") > last_reachable_idx

    def test_bottom_up_all_functions_present_exactly_once(self):
        program = self._make_call_program()
        result = program.function_order("bottom_up")
        assert sorted(result) == sorted(program.cfgs.keys())

    def test_bottom_up_single_function_program(self):
        program = self._make_program(["main"])
        result = program.function_order("bottom_up")
        assert result == ["main"]

    # -- strategy: alphabetical -----------------------------------------------

    def test_alphabetical_sorts_by_name(self):
        """alphabetical returns all functions sorted lexicographically."""
        program = self._make_program(["zebra", "alpha", "main", "beta"], entry="main")
        result = program.function_order("alphabetical")
        assert result == sorted(["zebra", "alpha", "main", "beta"])

    def test_alphabetical_all_functions_present(self):
        program = self._make_call_program()
        result = program.function_order("alphabetical")
        assert sorted(result) == sorted(program.cfgs.keys())

    def test_alphabetical_deterministic_regardless_of_insertion_order(self):
        """alphabetical is independent of dict insertion order."""
        p1 = self._make_program(["z", "a", "m"], entry="m")
        p2 = self._make_program(["m", "z", "a"], entry="m")
        assert p1.function_order("alphabetical") == p2.function_order("alphabetical")
        assert p1.function_order("alphabetical") == ["a", "m", "z"]

    def test_bottom_up_dag_dependency(self):
        """bottom_up correctly orders a DAG call graph where multiple callers share a callee."""
        from rpkbin.cfg import CallRef

        main = CFG()
        main.add_block("m", insns=[CallRef("a"), CallRef("b")])
        main.set_entry("m")

        a = CFG()
        a.add_block("a_body", insns=[CallRef("c")])
        a.set_entry("a_body")

        b = CFG()
        b.add_block("b_body", insns=[CallRef("c")])
        b.set_entry("b_body")

        c = CFG()
        c.add_block("c_body")
        c.set_entry("c_body")

        program = Program({"main": main, "a": a, "b": b, "c": c})
        result = program.function_order("bottom_up")

        # c must be emitted before both of its callers a and b
        assert result.index("c") < result.index("a")
        assert result.index("c") < result.index("b")
        # callers must be emitted before entry (main)
        assert result.index("a") < result.index("main")
        assert result.index("b") < result.index("main")

