"""Flow execution contracts independent of subprocesses, storage and the GUI."""

import pytest

from rpkbin.flow import ActionOutcome, AnalysisReport, Analyzer, Automation, Binding, Flow, FlowSession, Parser


def parsed(line, ctx):
    return {"value": int(line)}


def session(*, parse=parsed, analyze=None, finish=None, trigger=None, live=False, control=None, **kwargs):
    source = Parser("sample", parse)
    guard = None if analyze is None else Analyzer("guard", records=[Binding(source, analyze)], on_finish=finish)
    flow = Flow(
        "example",
        parsers=[source],
        analyzers=[] if guard is None else [guard],
        automations=[] if trigger is None else [Automation("react", reports=[Binding(guard, trigger)])],
        **kwargs,
    )
    output = []
    return FlowSession(flow, publish=output.append, clock_enabled=live, automation_enabled=live, control=control), output


def test_repeated_records_exact_integers_and_detached_latest():
    engine, output = session()
    huge = 9007199254740993
    for value in (huge, huge, 7):
        engine.feed(str(value))
    assert [row["data"]["value"] for row in output] == [huge, huge, 7]
    assert [row["seq"] for row in output] == [1, 2, 3]
    assert [row["evidence"]["line_no"] for row in output] == [1, 2, 3]
    snap = engine.snapshot()
    snap["parsers"][0]["latest"]["value"] = 0
    output[-1]["data"]["value"] = -1
    assert engine.snapshot()["parsers"][0]["latest"] == {"value": 7}


def test_multi_emission_and_multiline_finish_evidence():
    def collect(line, ctx):
        ctx.state.setdefault("lines", []).append(line)
        if len(ctx.state["lines"]) == 2:
            return [{"part": part} for part in ctx.state.pop("lines")]

    def finish(end, ctx):
        assert end.complete
        if ctx.state.get("lines"):
            return {"part": ctx.state.pop("lines")[0]}

    output = []
    engine = FlowSession(Flow("multi", parsers=[Parser("block", collect, on_finish=finish)]), publish=output.append)
    for line in ("a", "b", "c"):
        engine.feed(line)
    engine.finish()
    assert [p["data"] for p in output] == [{"part": "a"}, {"part": "b"}, {"part": "c"}]
    assert output[-1]["evidence"] is None  # EOF is not a fabricated source line.


def test_all_analysis_finishes_before_live_trigger_and_receipt_is_not_exit():
    calls = []

    def first(value, ctx):
        return AnalysisReport("alert", values={"doubled": value["value"] * 2, "original": value["value"]})

    def second(value, ctx):
        assert isinstance(value, AnalysisReport)
        return AnalysisReport("sum", values={"sum": value.values["doubled"] + value.values["original"]})

    def react(publication, ctx):
        calculated = next(p["data"]["values"]["sum"] for p in output if p["producer"] == "sum")
        calls.append((publication.name, calculated))
        assert ctx.actions.stop(reason="alert").accepted
        assert ctx.source["execution"]["status"] == "RUNNING"

    requests, output = [], []
    source = Parser("sample", parsed)
    twice = Analyzer("twice", records=[Binding(source, first)])
    summed = Analyzer("sum", reports=[Binding(twice, second)])
    flow = Flow("order", parsers=[source], analyzers=[twice, summed], automations=[Automation("react", reports=[Binding(twice, react)])])
    engine = FlowSession(flow, clock_enabled=True, automation_enabled=True, execution_provider=lambda: {"status": "RUNNING"}, control={"stop": lambda **kw: requests.append(kw) or ActionOutcome("accepted", "Control accepted")}, publish=output.append)
    engine.feed("3")
    assert engine.wait_automation()
    engine.finish()
    assert calls == [("alert", 9)]
    assert requests == [{"reason": "alert"}]
    assert [p["data"]["phase"] for p in output if p["kind"] == "action"] == ["requested", "returned"]
    assert next(p for p in output if p["kind"] == "action" and p["data"]["phase"] == "returned")["data"]["receipt"] == {"status": "accepted", "reason": "Control accepted"}


def test_live_and_offline_analysis_equal_but_no_offline_callback():
    controls = []

    def analyze(value, ctx):
        ctx.summary.result("FAIL")
        return AnalysisReport("found", values=dict(value))

    def react(publication, ctx):
        controls.append(publication.name)

    offline, offline_rows = session(analyze=analyze, trigger=react)
    live, live_rows = session(analyze=analyze, trigger=react, live=True)
    offline.feed("2")
    live.feed("2")
    assert live.wait_automation()
    assert controls == ["found"]
    assert offline_rows == [p for p in live_rows if p["stage"] != "automation"]
    assert offline.finish()["result"] == live.finish()["result"] == "FAIL"


def test_reusable_flow_state_isolated_per_session_and_per_step():
    def accum(value, ctx):
        ctx.state["n"] = ctx.state.get("n", 0) + 1
        return AnalysisReport("count", values={"count": ctx.state["n"]})

    source = Parser("sample", parsed)
    flow = Flow("isolation", parsers=[source], analyzers=[Analyzer("a", records=[Binding(source, accum)]), Analyzer("b", records=[Binding(source, accum)])])
    first_rows, second_rows = [], []
    first, second = FlowSession(flow, publish=first_rows.append), FlowSession(flow, publish=second_rows.append)
    first.feed("1")
    first.feed("1")
    second.feed("1")
    assert [p["data"]["values"] for p in first_rows if p["kind"] == "report"] == [{"count": 1}, {"count": 1}, {"count": 2}, {"count": 2}]
    assert [p["data"]["values"] for p in second_rows if p["kind"] == "report"] == [{"count": 1}, {"count": 1}]


def test_clear_current_issue_preserves_occurrences_and_confirmed_fail():
    def analyze(value, ctx):
        high = value["value"] > 2
        if high and not ctx.state.get("high", False):
            ctx.summary.event("entered")
        ctx.state["high"] = high
        ctx.summary.issue("high", level="warning", message="too high", active=high)
        ctx.summary.result("FAIL" if high else "PASS")

    engine, output = session(analyze=analyze)
    for value in (3, 3, 1, 4, 1):
        engine.feed(str(value))
    guard = engine.snapshot()["analyzers"][0]
    assert guard["issues"] == []
    assert guard["event_count"] == 2
    assert len([p for p in output if p["kind"] == "issue"]) == 5
    assert engine.finish()["result"] == "FAIL"


def test_warning_is_not_fail_and_no_exit_code_inferred_pass():
    def analyze(value, ctx):
        ctx.summary.issue("heads_up", level="warning")

    engine, _ = session(analyze=analyze)
    engine.feed("2")
    assert engine.snapshot()["result"] is None
    assert engine.finish(execution={"returncode": 0})["result"] == "N/A"


@pytest.mark.parametrize("complete,expected", [(True, "PASS"), (False, "INCOMPLETE")])
def test_pass_requires_completed_trusted_input(complete, expected):
    def analyze(value, ctx):
        ctx.summary.result("PASS")

    engine, _ = session(analyze=analyze)
    engine.feed("1")
    assert engine.snapshot()["result"] is None
    assert engine.finish(complete=complete)["result"] == expected
    assert engine.snapshot()["analyzers"][0]["result"] == expected


def test_finish_can_check_absence_and_is_idempotent():
    def analyze(value, ctx):
        ctx.state["seen"] = value["value"] == 3 or ctx.state.get("seen", False)

    def finish(end, ctx):
        ctx.summary.result("PASS" if ctx.state.get("seen", False) else "FAIL", final=True)

    engine, output = session(analyze=analyze, finish=finish)
    engine.feed("1")
    assert engine.finish()["result"] == "FAIL"
    count = len(output)
    assert engine.finish()["result"] == "FAIL"
    assert len(output) == count
    with pytest.raises(RuntimeError, match="finished"):
        engine.feed("3")


def test_no_match_distinct_from_failure_and_other_branch_survives():
    def bad(line, ctx):
        if line == "skip":
            return None
        return {"x": int(line)}

    def done(values, ctx):
        ctx.summary.result("PASS")

    bad_source, good_source = Parser("bad", bad), Parser("good", lambda line, ctx: {"text": line})
    flow = Flow("errors", parsers=[bad_source, good_source], analyzers=[Analyzer("dependent", records=[Binding(bad_source, done)]), Analyzer("other", records=[Binding(good_source, done)])])
    engine = FlowSession(flow)
    engine.feed("skip")
    assert engine.snapshot()["error_count"] == 0
    engine.feed("bad integer")
    engine.feed("123")
    snap = engine.finish()
    assert snap["result"] == "ERROR"
    assert snap["parsers"][0]["count"] == 0
    assert snap["parsers"][1]["count"] == 3
    assert snap["analyzers"][0]["result"] == "ERROR"
    assert snap["analyzers"][1]["result"] == "PASS"


def test_ticks_run_without_log_lines_at_declared_intervals():
    seen = []

    def timer(value, ctx):
        seen.append(value.elapsed)
        ctx.summary.result("FAIL", reason="no progress")

    engine = FlowSession(Flow("clock", analyzers=[Analyzer("watchdog", on_tick=timer, every=2)]), clock_enabled=True, automation_enabled=True)
    for elapsed in (0, 1, 2, 2.1, 4):
        engine.tick(elapsed)
    assert seen == [2, 4]
    assert engine.snapshot()["result"] == "FAIL"


def test_snapshots_never_execute_custom_aggregation_or_reactions():
    calls = []

    def aggregate(reports):
        calls.append(len(reports))
        with pytest.raises(TypeError):
            reports[0]["name"] = "changed"
        return "PASS"

    engine, _ = session(analyze=lambda value, ctx: None, result=aggregate)
    engine.feed("1")
    assert calls == [1]
    for _ in range(10):
        assert engine.snapshot()["result"] is None
    assert calls == [1]
    assert engine.finish()["result"] == "PASS"
    assert calls == [1, 1]


def test_control_capability_only_live_trigger_and_expires_after_callback():
    contexts = []

    def analyze(value, ctx):
        with pytest.raises(AttributeError):
            _ = ctx.actions
        return AnalysisReport("ready")

    def react(publication, ctx):
        contexts.append(ctx)
        with pytest.raises(TypeError):
            ctx.source["execution"]["status"] = "DONE"

    engine, _ = session(analyze=analyze, trigger=react, live=True)
    engine.feed("1")
    assert engine.wait_automation()
    engine.finish()
    with pytest.raises(RuntimeError, match="during its callback"):
        contexts[0].actions.stop()


def test_input_isolation_and_metadata_detachment():
    metadata = {"value": {"label": "Amount", "unit": "V"}}
    parser = Parser("sample", parsed, outputs=metadata)
    metadata["value"]["label"] = "changed"
    seen = []

    def mutate(values, ctx):
        with pytest.raises(TypeError):
            values["value"] = -1

    def observe(values, ctx):
        seen.append(values["value"])

    flow = Flow("copies", parsers=[parser], analyzers=[Analyzer("mutate", records=[Binding(parser, mutate)]), Analyzer("observe", records=[Binding(parser, observe)])])
    engine = FlowSession(flow)
    engine.feed("5")
    assert seen == [5]
    assert engine.snapshot()["parsers"][0]["outputs"]["value"]["label"] == "Amount"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), {1: "non-string key"}, object()])
def test_invalid_data_disables_producer_without_silently_casting(bad):
    engine, output = session(parse=lambda line, ctx: {"bad": bad})
    engine.feed("1")
    assert engine.snapshot()["parsers"][0]["count"] == 0
    assert engine.snapshot()["result"] == "ERROR"
    assert all(p["kind"] != "record" for p in output)


def test_bounded_tails_keep_complete_publication_stream():
    def analyze(values, ctx):
        ctx.summary.event("observed", data=values)
        ctx.debug("handled")

    engine, output = session(analyze=analyze)
    for _ in range(250):
        engine.feed("1")
    snap = engine.snapshot()
    assert snap["analyzers"][0]["event_count"] == 250
    assert len(snap["analyzers"][0]["events"]) == 100
    assert len(snap["diagnostics"]) == 100
    assert len(output) == 750
    assert not engine._records and not engine._reactions


def test_publication_failure_is_separate_from_analysis_result():
    def fail_store(publication):
        raise OSError("disk full")

    def analyze(values, ctx):
        ctx.summary.result("PASS")

    source = Parser("p", parsed)
    engine = FlowSession(Flow("save", parsers=[source], analyzers=[Analyzer("a", records=[Binding(source, analyze)])]), publish=fail_store)
    engine.feed("1")
    snap = engine.finish()
    assert snap["result"] == "PASS"
    assert snap["error_count"] == 0
    assert "disk full" in snap["publication_error"]


def test_invalid_graph_and_named_result_contract():
    missing = Parser("missing", parsed)
    with pytest.raises(ValueError):
        Flow("unattached", analyzers=[Analyzer("a", records=[Binding(missing, lambda data, ctx: None)])])
    with pytest.raises(ValueError, match="unique"):
        Flow("duplicate", parsers=[Parser("same", parsed), Parser("same", parsed)])
    with pytest.raises(ValueError, match="namespaces"):
        Parser("sample.value", parsed)
    assert Parser("sample", parsed, version="1.0").version == "1.0"
    source = Parser("sample", parsed)
    guard = Analyzer("guard", records=[Binding(source, lambda value, ctx: None)])
    engine = FlowSession(Flow("selected", parsers=[source], analyzers=[guard], result=guard))
    engine.feed("1")
    assert engine.finish()["result"] == "INCOMPLETE"


def test_repeat_policy_is_explicit_python_and_only_reports_are_delivered():
    calls = []

    def analyze(value, ctx):
        ctx.summary.issue("present")
        ctx.summary.event("present")
        return AnalysisReport("present")

    def react(report, ctx):
        if ctx.state.get("seen"):
            return
        ctx.state["seen"] = True
        calls.append(report.name)

    source = Parser("p", parsed)
    analyzed = Analyzer("a", records=[Binding(source, analyze)])
    flow = Flow("once", parsers=[source], analyzers=[analyzed], automations=[Automation("once", reports=[Binding(analyzed, react)])])
    engine = FlowSession(flow, clock_enabled=True, automation_enabled=True)
    engine.feed("1")
    engine.feed("1")
    assert engine.wait_automation()
    engine.finish()
    assert calls == ["present"]
    assert engine.snapshot()["automations"][0]["processed"] == 2


def test_aggregation_error_is_visible_and_does_not_run_on_reads():
    def bad(reports):
        raise ValueError("broken aggregate")

    engine, _ = session(analyze=lambda value, ctx: None, result=bad)
    engine.feed("1")
    snap = engine.snapshot()
    assert snap["result"] == "ERROR"
    assert "broken aggregate" in snap["aggregation_error"]
    assert snap["error_count"] == engine.snapshot()["error_count"] == 1


def test_returning_staged_summary_none_does_not_create_another_report():
    def parse(line, ctx):
        return {"value": int(line)}

    def analyze(value, ctx):
        return ctx.summary.result("FAIL")

    engine, output = session(parse=parse, analyze=analyze)
    engine.feed("3")
    assert [p["kind"] for p in output] == ["record", "result"]
    analyzer = engine.snapshot()["analyzers"][0]
    assert analyzer["count"] == 0  # No calculated observation record was emitted.
    assert analyzer["publication_count"] == 1
    assert analyzer["processed"] == 1


def test_iterable_output_limit_rejects_runaway_generator(monkeypatch):
    import rpkbin.flow._session as flow_module

    monkeypatch.setattr(flow_module, "MAX_OUTPUTS_PER_CALL", 3)

    def parse(line, ctx):
        while True:
            yield {"value": 1}

    engine, output = session(parse=parse)
    engine.feed("3")
    assert len([p for p in output if p["kind"] == "record"]) == 0
    assert engine.snapshot()["result"] == "ERROR"


def test_parser_failure_points_to_rejected_input_not_previous_good_record():
    engine, _ = session(parse=lambda text, ctx: {"value": int(text)}, analyze=lambda value, ctx: None)
    engine.feed("1", evidence={"line_no": 10})
    engine.feed("invalid", evidence={"line_no": 11})
    snapshot = engine.snapshot()
    assert snapshot["parsers"][0]["evidence"]["line_no"] == 11
    assert snapshot["parsers"][0]["latest_evidence"]["line_no"] == 10
    assert snapshot["analyzers"][0]["evidence"]["line_no"] == 11


def test_spec_source_includes_finish_logic():
    def finish(end, ctx):
        ctx.summary.result("PASS", final=True)

    engine = FlowSession(Flow("source", analyzers=[Analyzer("completion", on_finish=finish)]))
    source = engine.snapshot()["analyzers"][0]["code"]
    assert "def finish(end, ctx)" in source
    assert 'ctx.summary.result("PASS", final=True)' in source


def test_multiple_inputs_expose_origin_even_when_data_keys_are_identical():
    seen = []

    def analyze(value, ctx):
        seen.append((ctx.source["producer"], ctx.source["seq"], value["value"]))
        with pytest.raises(TypeError):
            ctx.source["producer"] = "changed"

    left, right = Parser("left", lambda line, ctx: {"value": 1}), Parser("right", lambda line, ctx: {"value": 2})
    flow = Flow("origins", parsers=[left, right], analyzers=[Analyzer("join", records=[Binding(left, analyze), Binding(right, analyze)])])
    FlowSession(flow).feed("sample")
    assert seen == [("left", 1, 1), ("right", 2, 2)]


def test_long_stream_retains_counts_and_bounded_tails_not_record_history():
    total = 0

    def publish(publication):
        nonlocal total
        total += 1

    def analyze(value, ctx):
        ctx.summary.event("sample")

    source = Parser("sample", lambda line, ctx: {"value": 1})
    engine = FlowSession(Flow("long", parsers=[source], analyzers=[Analyzer("counter", records=[Binding(source, analyze)])]), publish=publish)
    for _ in range(10000):
        engine.feed("repeat")
    snapshot = engine.snapshot()
    assert total == 20000
    assert snapshot["parsers"][0]["count"] == 10000
    assert snapshot["analyzers"][0]["event_count"] == 10000
    assert len(snapshot["analyzers"][0]["events"]) == 100
    assert not engine._records and not engine._reactions


def test_complete_positive_finalization_does_not_unfinalize_previous_failure():
    engine, _ = session(analyze=lambda value, ctx: ctx.summary.result("FAIL", "failure"),
                        finish=lambda end, ctx: ctx.summary.result("PASS", final=True))
    engine.feed("1")
    engine.finish()
    analyzer = engine.snapshot()["analyzers"][0]
    assert analyzer["result"] == "FAIL" and analyzer["final"]


def test_final_result_is_not_overwritten_by_lower_or_equal_severity():
    def first(record, ctx):
        if record["value"] == 1:
            ctx.summary.result("INCOMPLETE", "missing tail", final=True)
        elif record["value"] == 2:
            ctx.summary.result("PASS", "ok")
        else:
            ctx.summary.result("FAIL", "bad", final=False)

    def finish(end, ctx):
        ctx.summary.result("PASS", "late pass", final=True)

    engine1, _ = session(analyze=first, finish=finish)
    engine1.feed("1")
    engine1.feed("2")
    snap1 = engine1.finish()
    analyzer1 = snap1["analyzers"][0]
    assert analyzer1["result"] == "INCOMPLETE"
    assert analyzer1["reason"] == "missing tail"
    assert analyzer1["final"] is True

    engine2, _ = session(analyze=first, finish=finish)
    engine2.feed("1")
    engine2.feed("3")
    snap2 = engine2.finish()
    analyzer2 = snap2["analyzers"][0]
    assert analyzer2["result"] == "FAIL"
    assert analyzer2["reason"] == "bad"
    assert analyzer2["final"] is True


def test_tick_handles_decimal_float_intervals_without_skipping():
    seen = []
    flow = Flow("ticks", analyzers=[Analyzer("t", on_tick=lambda tick, ctx: seen.append(tick.elapsed), every=0.1)])
    engine = FlowSession(flow, clock_enabled=True)
    for elapsed in (0.0, 0.1, 0.2, 0.3, 0.4):
        engine.tick(elapsed)
    assert seen == [0.1, 0.2, 0.3, 0.4]


def test_custom_result_updates_without_full_snapshot(monkeypatch):
    observed = []

    def aggregate(analyzers):
        observed.append(analyzers)
        with pytest.raises(TypeError):
            analyzers[0]["name"] = "mutated"
        return analyzers[0]["result"]

    engine, _ = session(analyze=lambda value, ctx: ctx.summary.result("FAIL", "bad"), result=aggregate)

    def forbid_snapshot():
        raise AssertionError("full snapshot() should not run during feed()")

    monkeypatch.setattr(engine, "snapshot", forbid_snapshot)
    engine.feed("1")
    assert len(observed) == 1
    assert observed[0][0]["name"] == "guard"
    assert observed[0][0]["result"] == "FAIL"
    assert observed[0][0]["reason"] == "bad"
    assert "code" not in observed[0][0]
    monkeypatch.undo()
    assert engine.snapshot()["result"] == "FAIL"


def test_flow_session_context_manager_and_repr():
    seen_reprs = []

    def parse(line, ctx):
        seen_reprs.append(repr(ctx))
        return {"x": 1}

    p = Parser("p", parse)
    a = Analyzer("a", records=[Binding(p, lambda row, ctx: seen_reprs.append(repr(ctx)) or ctx.summary.result("PASS"))])
    auto = Automation("auto", on_event=lambda ev, ctx: seen_reprs.append(repr(ctx)))
    flow = Flow("ctx-mgr", parsers=[p], analyzers=[a], automations=[auto])

    with FlowSession(flow, automation_enabled=True) as engine:
        assert "FlowSession(" in repr(engine) and "flow='ctx-mgr'" in repr(engine)
        engine.start()
        engine.feed("1")
        snap = engine.finish()
        assert snap["result"] == "PASS"
    assert engine.snapshot()["result"] == "PASS"
    assert any("ParserContext(" in r and "open=True" in r for r in seen_reprs)
    assert any("AnalyzerContext(" in r and "open=True" in r for r in seen_reprs)
    assert any("AutomationContext(" in r and "open=True" in r for r in seen_reprs)

    with pytest.raises(RuntimeError, match="boom"):
        with FlowSession(flow, automation_enabled=True) as aborted:
            aborted.start()
            aborted.feed("1")
            raise RuntimeError("boom")
    assert aborted.finished is True
    assert aborted.snapshot()["result"] == "INCOMPLETE"
    with pytest.raises(RuntimeError, match="closed"):
        with aborted:
            pass
