"""Standalone Flow execution facts, clock policy and action delivery."""

import pytest

from rpkbin.flow import ActionOutcome, AnalysisReport, Analyzer, Automation, Binding, Flow, FlowSession, Parser


def test_clock_and_automation_are_independent_and_no_control_is_audited():
    ticks, receipts, publications = [], [], []
    facts = {"attempt_id": "example", "elapsed": 0}

    def execution():
        return facts.copy()

    def on_tick(tick, ctx):
        ticks.append((tick.elapsed, ctx.source["execution"]["elapsed"]))

    def on_event(event, ctx):
        if event.name == "started":
            receipts.append((event.data["attempt_id"], ctx.actions.send("command")))

    flow = Flow(
        "seam",
        analyzers=[Analyzer("clock", on_tick=on_tick, every=1)],
        automations=[Automation("observer", on_event=on_event)],
    )
    clock = FlowSession(flow, clock_enabled=True, execution_provider=execution)
    clock.start()
    clock.tick(2)
    facts = {"attempt_id": "example", "elapsed": 4}
    clock.tick()
    clock.finish()
    assert clock.wait_automation()
    assert ticks == [(2, 2), (4, 4)] and not receipts

    facts = {"attempt_id": "new-attempt"}
    automation = FlowSession(
        flow, automation_enabled=True, execution_provider=execution,
        control={"send": lambda text: ActionOutcome("rejected", "no control channel")}, publish=publications.append,
    )
    automation.start()
    automation.tick(3)
    automation.finish()
    assert automation.wait_automation()
    assert ticks == [(2, 2), (4, 4)]
    [(attempt_id, receipt)] = receipts
    assert attempt_id == "new-attempt"
    assert (receipt.status, receipt.reason) == ("rejected", "no control channel")
    actions = [p["data"] for p in publications if p["kind"] == "action"]
    assert [p["phase"] for p in actions] == ["requested", "returned"]
    assert actions[-1]["receipt"] == {"status": "rejected", "reason": "no control channel"}


@pytest.mark.parametrize("failing_stage", ["parser", "analyzer"])
def test_provider_error_is_local_to_sync_step(failing_stage):
    samples = 0
    calls = []
    failure_at = 1 if failing_stage == "parser" else 2

    def provider():
        nonlocal samples
        samples += 1
        if samples == failure_at:
            raise RuntimeError("execution facts unavailable")
        return {}

    def broken(value, ctx):
        calls.append(("broken", value))

    def healthy(value, ctx):
        calls.append(("healthy", value))

    source = Parser("source", lambda line, ctx: {"line": line})
    if failing_stage == "parser":
        flow = Flow("isolation", parsers=[Parser("broken", broken), Parser("healthy", healthy)])
    else:
        flow = Flow("isolation", parsers=[source], analyzers=[
            Analyzer("broken", records=[Binding(source, broken)]),
            Analyzer("healthy", records=[Binding(source, healthy)]),
        ])

    engine = FlowSession(flow, execution_provider=provider)
    engine.feed("first")
    engine.feed("next")
    expected = ["first", "next"] if failing_stage == "parser" else [{"line": "first"}, {"line": "next"}]
    assert calls == [("healthy", value) for value in expected]
    snapshot = engine.finish()
    assert snapshot["error_count"] == 1
    assert snapshot["diagnostics"][-1]["producer"] == "broken"
    assert "execution facts unavailable" in snapshot["diagnostics"][-1]["data"]["message"]


def test_provider_error_in_automation_context_leaves_worker_running():
    samples = 0
    calls = []

    def provider():
        nonlocal samples
        samples += 1
        if samples == 2:
            raise RuntimeError("execution facts unavailable")
        return {}

    flow = Flow("worker", automations=[
        Automation("broken", on_event=lambda event, ctx: calls.append(("broken", event.name))),
        Automation("healthy", on_event=lambda event, ctx: calls.append(("healthy", event.name))),
    ])
    engine = FlowSession(flow, execution_provider=provider, automation_enabled=True)
    try:
        engine.start()
        assert engine.wait_automation(0.5)
        engine.emit_event("status_changed")
        assert engine.wait_automation(0.5)
        engine.finish()
        assert engine.wait_automation(0.5)
        assert calls == [("healthy", name) for name in ("started", "status_changed", "finished")]
        snapshot = engine.snapshot()
        assert snapshot["automation_error_count"] == 1
        assert snapshot["diagnostics"][-1]["producer"] == "broken"
        assert "execution facts unavailable" in snapshot["diagnostics"][-1]["data"]["message"]
    finally:
        engine.close_automation()


def test_tick_uses_one_execution_sample_for_its_callbacks():
    samples = 0
    observed = []

    def provider():
        nonlocal samples
        samples += 1
        return {"elapsed": samples, "sample": samples}

    def observe(name, ctx, elapsed=None):
        facts = ctx.source["execution"]
        observed.append((name, facts["elapsed"], facts["sample"], elapsed))

    def first(tick, ctx):
        observe("first", ctx, tick.elapsed)
        return AnalysisReport("reading")

    clock = Analyzer("first", on_tick=first, every=1)
    flow = Flow("tick", analyzers=[
        clock,
        Analyzer("second", on_tick=lambda tick, ctx: observe("second", ctx, tick.elapsed), every=1),
        Analyzer("downstream", reports=[Binding(clock, lambda report, ctx: observe("downstream", ctx))]),
    ], automations=[
        Automation("reaction", reports=[Binding(clock, lambda report, ctx: observe("reaction", ctx))]),
    ])
    engine = FlowSession(flow, execution_provider=provider, clock_enabled=True, automation_enabled=True)
    try:
        engine.tick()
        assert engine.wait_automation()
        engine.tick(5)
        assert engine.wait_automation()
        assert samples == 2
        assert observed == [
            (name, elapsed, sample, elapsed if name in ("first", "second") else None)
            for sample, elapsed in [(1, 1), (2, 5)]
            for name in ("first", "second", "downstream", "reaction")
        ]
    finally:
        engine.close_automation()
