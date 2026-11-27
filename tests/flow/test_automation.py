"""Automation contracts: ordinary Python decisions and audited run-bound IO."""

import threading

import pytest

from rpkbin.flow import ActionOutcome, AnalysisReport, Analyzer, Automation, Binding, Flow, FlowSession, Parser


def definition(callback=None, *, event=None, enabled=True):
    samples = Parser("samples", lambda line, ctx: {"x": int(line)} if line.isdigit() else None)
    limits = Analyzer("limits", records=[Binding(samples, lambda row, ctx: AnalysisReport("upper", result="HIGH" if row["x"] > 10 else "OK", values=dict(row)))])
    return Flow("control", parsers=[samples], analyzers=[limits],
                automations=[Automation("react", reports=[Binding(limits, callback)] if callback else [], on_event=event, enabled=enabled)])


def test_false_condition_does_not_consume_future_action_and_state_is_per_run():
    calls, receipts, output = [], [], []

    def react(report, ctx):
        assert report.name == "upper" and ctx.source["producer"] == "limits"
        with pytest.raises(TypeError):
            report.values["x"] = 0
        if report.result != "HIGH" or ctx.state.get("requested"):
            return
        ctx.state["requested"] = True
        receipts.append(ctx.actions.stop(reason=report.reason))

    flow = definition(react)
    for _ in range(2):
        engine = FlowSession(flow, clock_enabled=True, automation_enabled=True, publish=output.append,
                             execution_provider=lambda: {"status": "RUNNING"}, control={"stop": lambda **kw: calls.append(kw) or ActionOutcome("accepted", "Control accepted")})
        for value in (1, 12, 12):
            engine.feed(str(value))
        engine.finish()
        assert engine.wait_automation()
        item = engine.snapshot()["automations"][0]
        assert item["processed"] == 3 and item["action_count"] == 1
    assert len(calls) == 2 and all(r.accepted for r in receipts)
    actions = [p for p in output if p["kind"] == "action"]
    assert len(actions) == 4
    assert actions[0]["seq"] == actions[1]["data"]["request_id"]
    assert actions[1]["data"]["source"]["name"] == "upper"
    assert bool(receipts[0])


def test_rejected_and_failed_receipts_can_be_handled_without_breaking_callback():
    receipts, out = [], []

    def broken():
        raise OSError("not supported")

    def react(report, ctx):
        receipts.append(ctx.actions.send("command"))
        if not receipts[-1].accepted:
            receipts.append(ctx.actions.interrupt())

    engine = FlowSession(definition(react), clock_enabled=True, automation_enabled=True, publish=out.append,
                         control={"send": lambda text: ActionOutcome("rejected", "No process available"), "send_ctrl_c": broken})
    engine.feed("12")
    engine.finish()
    assert engine.wait_automation()
    assert [r.status for r in receipts] == ["rejected", "failed"]
    assert "not supported" in receipts[1].reason
    assert engine.snapshot()["automation_error_count"] == 0
    assert [p["data"]["status"] for p in out if p["kind"] == "action"] == ["requested", "rejected", "requested", "failed"]


def test_callback_error_is_local_and_offline_readers_never_execute_it():
    calls = []

    def bad(report, ctx):
        calls.append(report.name)
        raise ValueError("callback fault")

    flow = definition(bad)
    live = FlowSession(flow, clock_enabled=True, automation_enabled=True)
    live.feed("12")
    assert live.wait_automation()
    live.feed("13")
    live.finish()
    assert live.wait_automation()
    snap = live.snapshot()
    assert snap["result"] == "N/A" and snap["error_count"] == 0
    assert snap["automation_error_count"] == 1
    assert "callback fault" in snap["automations"][0]["error"]
    replay = FlowSession(flow)
    replay.feed("12")
    replay.finish()
    for _ in range(10):
        replay.snapshot()
    assert calls == ["upper"]


def test_slow_callback_does_not_block_feed_and_backlog_is_bounded(monkeypatch):
    import rpkbin.flow._automation as worker
    monkeypatch.setattr(worker, "MAX_PENDING", 3)
    entered, release = threading.Event(), threading.Event()

    def slow(report, ctx):
        entered.set()
        assert release.wait(3)

    engine = FlowSession(definition(slow), clock_enabled=True, automation_enabled=True)
    try:
        engine.feed("12")
        assert entered.wait(1)
        for _ in range(20):
            engine.feed("12")
        assert engine.snapshot()["parsers"][0]["count"] == 21
        assert len(engine._automation._pending) <= 3
        assert engine.snapshot()["automation_error_count"] == 1
    finally:
        release.set()
        engine.finish()
        assert engine.wait_automation()


def test_job_events_have_native_data_and_no_fake_log_source():
    events, contexts = [], []

    def event(value, ctx):
        events.append((value.name, dict(value.data), dict(ctx.source), ctx.source["evidence"]))
        contexts.append(ctx)

    engine = FlowSession(definition(event=event), clock_enabled=True, automation_enabled=True, execution_provider=lambda: {"attempt_id": "original"})
    engine.start()
    engine.finish(execution={"returncode": 0})
    assert engine.wait_automation()
    assert [e[0] for e in events] == ["started", "finished"]
    assert events[0][1] == {"attempt_id": "original"}
    assert events[1][1]["returncode"] == 0
    assert all(e[3] is None and "seq" not in e[2] for e in events)
    with pytest.raises(RuntimeError, match="during its callback"):
        contexts[0].actions.stop()


def test_completion_deadline_rejects_late_controls():
    entered, release = threading.Event(), threading.Event()
    calls, failures = [], []

    def slow(report, ctx):
        entered.set()
        release.wait(3)
        try:
            ctx.actions.stop()
        except RuntimeError as exc:
            failures.append(str(exc))

    engine = FlowSession(definition(slow), clock_enabled=True, automation_enabled=True, control={"stop": lambda: calls.append(True) or ActionOutcome("accepted", "Control accepted")})
    engine.feed("12")
    assert entered.wait(1)
    engine.finish()
    assert not engine.wait_automation(0)
    engine.close_automation()
    release.set()
    assert engine.wait_automation()
    assert failures and not calls


def test_invalid_automation_contracts_are_rejected():
    with pytest.raises(ValueError, match="requires"):
        Automation("empty")
    parser = Parser("samples", lambda text, ctx: {})
    with pytest.raises((TypeError, ValueError)):
        Automation("events", reports=[Binding(parser, lambda report, ctx: None)])
    unattached = Analyzer("missing", on_finish=lambda end, ctx: None)
    with pytest.raises(ValueError):
        Flow("bad", automations=[Automation("a", reports=[Binding(unattached, lambda report, ctx: None)])])


def test_standalone_object_control_respects_signature_for_source_injection():
    calls = []

    class PlainControl:
        def stop(self, reason=""):
            calls.append(("plain", reason))
            return ActionOutcome("accepted", reason or "stopped")

    class SourceControl:
        def stop(self, reason="", *, source="user"):
            calls.append(("source", reason, source))
            return ActionOutcome("accepted", "stopped")

    class PosOnlyControl:
        def stop(self, reason="", /):
            calls.append(("posonly", reason))
            return ActionOutcome("accepted", "stopped")

    def react(report, ctx):
        return ctx.actions.stop(reason="ok")

    def react_pos(report, ctx):
        return ctx.actions.stop("ok")

    for ctrl, fn in ((PlainControl(), react), (SourceControl(), react), (PosOnlyControl(), react_pos)):
        engine = FlowSession(definition(fn), clock_enabled=True, automation_enabled=True, control=ctrl)
        engine.feed("12")
        engine.finish()
        assert engine.wait_automation()
        assert engine.snapshot()["automation_error_count"] == 0
    assert calls == [
        ("plain", "ok"),
        ("source", "ok", "automation:react"),
        ("posonly", "ok"),
    ]


def test_publication_error_on_requested_action_does_not_leave_spurious_lost_receipt_at_seal():
    out = []
    fail_publish = True

    def publish(pub):
        if fail_publish and pub["kind"] == "action" and pub["data"]["phase"] == "requested":
            raise OSError("disk full")
        out.append(pub)

    def react(event, ctx):
        if event.name == "started":
            ctx.actions.stop()

    engine = FlowSession(
        Flow("audit", automations=[Automation("a", on_event=react)]),
        clock_enabled=True,
        automation_enabled=True,
        publish=publish,
        control={"stop": lambda **kw: ActionOutcome("accepted", "ok")},
    )
    engine.start()
    assert engine.wait_automation()
    fail_publish = False
    engine._seal(incomplete=True)
    lost = [
        p for p in out
        if p["kind"] == "action" and "lost at attempt close" in p["data"].get("reason", "")
    ]
    assert lost == []
