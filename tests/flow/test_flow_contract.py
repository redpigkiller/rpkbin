"""Independent public-API acceptance cases for the explicitly connected Flow."""

from dataclasses import dataclass

import pytest

from rpkbin.flow import (
    ActionOutcome, AnalysisReport, Analyzer, AnalyzerContext, Automation, AutomationContext,
    Binding, Flow, FlowSession, Parser, ParserContext,
)


def test_graph_validates_identity_category_duplicates_and_signatures_before_running():
    p = Parser("p", lambda line, ctx: {})
    other_p = Parser("p", lambda line, ctx: {})
    def handler(value, ctx):
        return None
    with pytest.raises(ValueError, match="not in this Flow"):
        Flow("foreign", parsers=[p], analyzers=[Analyzer("a", records=[Binding(other_p, handler)])])
    with pytest.raises(ValueError, match="duplicate"):
        Analyzer("a", records=[Binding(p, handler), Binding(p, handler)])
    with pytest.raises(TypeError, match="Analyzer sources"):
        Automation("bad", reports=[Binding(p, handler)])
    with pytest.raises(TypeError, match="2 positional"):
        Binding(p, lambda value: None)
    with pytest.raises(TypeError, match="0 positional"):
        Parser("bad-state", handler, state_factory=lambda missing: {})

    async def async_handler(value, ctx):
        pass

    with pytest.raises(TypeError, match="synchronous"):
        Parser("async", async_handler)


def test_context_capabilities_and_nested_record_snapshot_are_real_not_annotations():
    seen, contexts = [], []
    data = {"kind": "user-kind", "name": "user-name", "nested": {"values": [1, 2]}}

    def parse(line, ctx):
        assert isinstance(ctx, ParserContext)
        for attr in ("job", "actions", "summary", "report", "record", "latest"):
            assert not hasattr(ctx, attr)
        contexts.append(ctx)
        return data

    def inspect(record, ctx):
        assert isinstance(ctx, AnalyzerContext)
        assert not hasattr(ctx, "job")
        assert not hasattr(ctx, "actions")
        with pytest.raises(TypeError):
            record["nested"]["values"][0] = 3
        with pytest.raises(TypeError):
            ctx.source["evidence"]["line_no"] = 999
        with pytest.raises(AttributeError):
            ctx.state = {}
        seen.append(record["kind"])
        return AnalysisReport("observed", result="ANY TEXT", values=record)

    p = Parser("p", parse)
    a = Analyzer("a", records=[Binding(p, inspect)])
    out = []
    session = FlowSession(Flow("typed", parsers=[p], analyzers=[a]), publish=out.append)
    session.feed("raw")
    data["nested"]["values"].append(3)
    assert seen == ["user-kind"]
    assert out[0]["data"] == {"kind": "user-kind", "name": "user-name", "nested": {"values": [1, 2]}}
    assert session.finish()["result"] == "N/A"
    with pytest.raises(RuntimeError, match="during its callback"):
        _ = contexts[0].state


def test_typed_factory_is_per_session_and_shared_only_by_one_analyzers_handlers():
    @dataclass
    class Counters:
        samples: int = 0
        edges: int = 0

    seen, states = [], []

    def sample(record, ctx: AnalyzerContext[Counters]):
        ctx.state.samples += 1
        states.append(ctx.state)

    def edge(record, ctx: AnalyzerContext[Counters]):
        ctx.state.edges += 1
        seen.append((ctx.state.samples, ctx.state.edges))

    p = Parser("samples", lambda line, ctx: {})
    q = Parser("edges", lambda line, ctx: {})
    a = Analyzer("a", records=[Binding(p, sample), Binding(q, edge)], state_factory=Counters)
    flow = Flow("state", parsers=[p, q], analyzers=[a])
    first, second = FlowSession(flow), FlowSession(flow)
    first.feed("one")
    first.feed("two")
    second.feed("one")
    assert seen == [(1, 1), (2, 2), (1, 1)]
    assert states[0] is states[1] and states[1] is not states[2]


def test_topological_finish_delivers_tail_reports_before_downstream_finish():
    seen = []

    def source_finish(end, ctx):
        assert end.complete and end.execution["returncode"] == 0
        return {"tail": 1}

    def analyze(record, ctx):
        return AnalysisReport("data", values=record)

    def finish(end, ctx):
        return AnalysisReport("done")

    def consume(report, ctx):
        assert isinstance(report, AnalysisReport)
        seen.append((report.name, report.evidence))

    def downstream_finish(end, ctx):
        seen.append(("finished", None))

    p = Parser("p", lambda line, ctx: None, on_finish=source_finish)
    a = Analyzer("a", records=[Binding(p, analyze)], on_finish=finish)
    b = Analyzer("b", reports=[Binding(a, consume)], on_finish=downstream_finish)
    flow = Flow("end", parsers=[p], analyzers=[b, a])
    session = FlowSession(flow)
    session.finish(execution={"returncode": 0})
    assert seen == [("data", None), ("done", None), ("finished", None)]


def test_failed_generator_publishes_neither_report_prefix_nor_staged_summary():
    actions, out = [], []

    def analyze(record, ctx):
        ctx.summary.issue("pending", level="warning")
        ctx.summary.result("FAIL", "would have failed")
        yield AnalysisReport("bad", result="FAIL")
        raise ValueError("invalid remainder")

    p = Parser("p", lambda line, ctx: {})
    a = Analyzer("a", records=[Binding(p, analyze)])
    auto = Automation("auto", reports=[Binding(a, lambda report, ctx: actions.append(report))])
    session = FlowSession(Flow("buffer", parsers=[p], analyzers=[a], automations=[auto]), clock_enabled=True, automation_enabled=True, publish=out.append)
    session.feed("raw")
    snapshot = session.finish()
    assert session.wait_automation()
    assert not actions
    assert [row["kind"] for row in out] == ["record", "diagnostic"]
    assert snapshot["analyzers"][0]["issues"] == []
    assert snapshot["analyzers"][0]["result"] == "ERROR"


def test_batch_size_limit_does_not_publish_a_valid_prefix(monkeypatch):
    import rpkbin.flow._session as module
    monkeypatch.setattr(module, "MAX_OUTPUT_BYTES", 180)
    out = []
    p = Parser("p", lambda line, ctx: [{"x": 1}, {"x": "x" * 200}])
    session = FlowSession(Flow("size", parsers=[p]), publish=out.append)
    session.feed("raw")
    assert not any(row["kind"] == "record" for row in out)
    assert "bytes" in session.snapshot()["parsers"][0]["error"]


def test_returned_control_receipt_is_not_executed_twice_and_controls_expire():
    calls, controls = [], []

    def act(report, ctx):
        assert isinstance(ctx, AutomationContext)
        controls.append(ctx.actions)
        return ctx.actions.stop(reason=report.reason)

    p = Parser("p", lambda line, ctx: {})
    a = Analyzer("a", records=[Binding(p, lambda record, ctx: AnalysisReport("limit", reason="high"))])
    auto = Automation("stop", reports=[Binding(a, act)])
    out = []
    session = FlowSession(Flow("receipt", parsers=[p], analyzers=[a], automations=[auto]), clock_enabled=True, automation_enabled=True,
                          control={"stop": lambda **kw: calls.append(kw) or ActionOutcome("accepted", "Control accepted")}, publish=out.append)
    session.feed("raw")
    session.finish()
    assert session.wait_automation()
    assert calls == [{"reason": "high"}]
    assert session.snapshot()["automation_error_count"] == 0
    assert [row["data"]["status"] for row in out if row["kind"] == "action"] == ["requested", "accepted"]
    with pytest.raises(RuntimeError):
        controls[0].stop()


def test_handled_oversize_summary_does_not_poison_a_small_fallback(monkeypatch):
    import rpkbin.flow._session as module
    monkeypatch.setattr(module, "MAX_OUTPUT_BYTES", 220)

    def analyze(record, ctx):
        with pytest.raises(ValueError, match="bytes"):
            ctx.summary.event("large", message="x" * 300)
        ctx.summary.event("fallback", message="small")

    p = Parser("p", lambda line, ctx: {})
    a = Analyzer("a", records=[Binding(p, analyze)])
    out = []
    session = FlowSession(Flow("fallback", parsers=[p], analyzers=[a]), publish=out.append)
    session.feed("raw")
    assert session.finish()["error_count"] == 0
    assert [row["data"]["name"] for row in out if row["kind"] == "event"] == ["fallback"]


def test_oversize_automation_request_is_rejected_before_audit_and_control(monkeypatch):
    import rpkbin.flow._session as module
    calls, out = [], []

    def act(event, ctx):
        monkeypatch.setattr(module, "MAX_OUTPUT_BYTES", 120)
        return ctx.actions.send("x" * 500)

    session = FlowSession(Flow("bounded-action", automations=[Automation("send", on_event=act)]), clock_enabled=True, automation_enabled=True,
                          publish=out.append, control={"send": lambda text: calls.append(text) or ActionOutcome("accepted", "Control accepted")})
    session.start()
    assert session.wait_automation()
    assert not calls and not any(row["kind"] == "action" for row in out)
    assert "bytes" in session.snapshot()["automations"][0]["error"]
    session.finish()


def test_state_factory_zero_arg_builtins_and_async_callable_object_rejected():
    from collections import defaultdict, deque

    for factory in (deque, defaultdict, int):
        p1 = Parser("p", lambda line, ctx: {"v": type(ctx.state).__name__}, state_factory=factory)
        s1 = FlowSession(Flow("f", parsers=[p1]))
        out = []
        s2 = FlowSession(Flow("f2", parsers=[p1]), publish=out.append)
        s2.feed("x")
        assert out[0]["data"]["v"] == factory.__name__
        assert s1.snapshot()["parsers"][0]["count"] == 0

    class AsyncCallable:
        async def __call__(self, line, ctx):
            return {}

    class AsyncGenCallable:
        async def __call__(self, line, ctx):
            yield {}

    for bad_cb in (AsyncCallable(), AsyncGenCallable()):
        with pytest.raises(TypeError, match="synchronous"):
            Parser("async-obj", bad_cb)

    for bad_factory in (len, range):
        with pytest.raises(TypeError, match="0 positional|signature"):
            Parser("bad-factory", lambda line, ctx: {}, state_factory=bad_factory)
