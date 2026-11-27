"""Explicit report identity, display text and no implicit verdict/control."""

import pytest

from rpkbin.flow import AnalysisReport, Analyzer, Automation, Binding, Flow, FlowSession, Parser


def test_reports_are_independent_named_outputs_with_arbitrary_result_text():
    def analyze(record, ctx):
        return [AnalysisReport("upper", result="OUT_OF_RANGE", reason="explicit", values={"x": record["x"]}),
                AnalysisReport("lower", result="READY"), AnalysisReport("calculated", values={"twice": record["x"] * 2})]

    output = []
    source = Parser("samples", lambda line, ctx: {"x": int(line)})
    engine = FlowSession(Flow("reports", parsers=[source], analyzers=[Analyzer("checks", records=[Binding(source, analyze)])]), publish=output.append)
    engine.feed("12", evidence={"line_no": 120})
    reports = [row for row in output if row["kind"] == "report"]
    assert [row["data"]["name"] for row in reports] == ["upper", "lower", "calculated"]
    assert [row["data"]["result"] for row in reports] == ["OUT_OF_RANGE", "READY", None]
    assert all(row["data"]["version"] == 1 and row["evidence"]["line_no"] == 120 for row in reports)
    snapshot = engine.finish()
    assert snapshot["analyzers"][0]["report_count"] == 3
    assert snapshot["analyzers"][0]["result"] is None
    assert snapshot["result"] == "N/A"


def test_report_payload_is_detached_and_nested_types_preserved():
    values = {"big": 9007199254740993, "nested": {"flag": True}, "array": [1, None]}
    report = AnalysisReport("payload", values=values)
    values["nested"]["flag"] = False
    values["array"].append(3)
    output = []
    source = Parser("p", lambda line, ctx: {})
    engine = FlowSession(Flow("detached", parsers=[source], analyzers=[Analyzer("a", records=[Binding(source, lambda record, ctx: report)])]), publish=output.append)
    engine.feed("one")
    payload = output[-1]["data"]["values"]
    assert payload == {"big": 9007199254740993, "nested": {"flag": True}, "array": [1, None]}


def test_dict_that_looks_like_report_is_a_parser_record_not_analyzer_output():
    data = {"name": "not-a-report", "result": "FAIL", "values": {"x": 1}}
    output = []
    source = Parser("p", lambda line, ctx: data)
    engine = FlowSession(Flow("plain", parsers=[source]), publish=output.append)
    engine.feed("one")
    assert output[-1]["kind"] == "record"
    assert output[-1]["data"] == data
    assert engine.finish()["result"] == "N/A"
    invalid = FlowSession(Flow("wrong-category", parsers=[source], analyzers=[Analyzer("a", records=[Binding(source, lambda record, ctx: data)])]))
    invalid.feed("one")
    assert invalid.finish()["result"] == "ERROR"


def test_returned_reports_finish_reports_and_disabled_automation_are_independent():
    invoked = []

    def analyze(record, ctx):
        return AnalysisReport("state", result="FAIL", values=dict(record))

    def finish(end, ctx):
        assert end.complete
        return AnalysisReport("state", result="FINISHED", values={"done": True}, evidence={"line_no": 9})

    output = []
    source = Parser("p", lambda line, ctx: {"x": 1})
    analyzed = Analyzer("a", records=[Binding(source, analyze)], on_finish=finish)
    engine = FlowSession(Flow("finish", parsers=[source], analyzers=[analyzed],
        automations=[Automation("disabled", reports=[Binding(analyzed, lambda pub, ctx: invoked.append(pub))], enabled=False)]),
        publish=output.append, clock_enabled=True, automation_enabled=True)
    engine.feed("one")
    assert engine.finish()["result"] == "N/A"
    assert not invoked
    assert output[-1]["data"]["result"] == "FINISHED"
    assert output[-1]["evidence"]["line_no"] == 9


def test_downstream_analyzer_receives_explicit_report_payload_and_origin():
    received = []

    def downstream(record, ctx):
        received.append((record, dict(ctx.source)))

    source = Parser("p", lambda line, ctx: {"x": 1})
    first = Analyzer("first", records=[Binding(source, lambda row, ctx: AnalysisReport("item", values=dict(row)))])
    engine = FlowSession(Flow("chain", parsers=[source], analyzers=[first, Analyzer("second", reports=[Binding(first, downstream)])]))
    engine.feed("one")
    assert isinstance(received[0][0], AnalysisReport)
    assert received[0][0].name == "item"
    assert received[0][0].values == {"x": 1}
    assert received[0][1]["kind"] == "report"


@pytest.mark.parametrize("kwargs", [dict(name=""), dict(name="a", result=False),
    dict(name="a", reason={}), dict(name="a", values=[]), dict(name="a", values={"x": float("nan")})])
def test_invalid_report_is_rejected_instead_of_inferred(kwargs):
    with pytest.raises((TypeError, ValueError)):
        AnalysisReport(**kwargs)


def test_parser_cannot_publish_report_and_report_generator_limit_is_enforced(monkeypatch):
    engine = FlowSession(Flow("bad-parser", parsers=[Parser("p", lambda line, ctx: AnalysisReport("a"))]))
    engine.feed("one")
    assert engine.snapshot()["result"] == "ERROR"

    import rpkbin.flow._session as flow_module
    monkeypatch.setattr(flow_module, "MAX_OUTPUTS_PER_CALL", 3)
    output = []

    def forever(record, ctx):
        while True:
            yield AnalysisReport("repeated")

    source = Parser("p", lambda line, ctx: {})
    engine = FlowSession(Flow("bounded", parsers=[source], analyzers=[Analyzer("a", records=[Binding(source, forever)])]), publish=output.append)
    engine.feed("one")
    assert len([row for row in output if row["kind"] == "report"]) == 0
    assert engine.snapshot()["result"] == "ERROR"



def test_many_report_names_do_not_accumulate_report_payloads_in_engine_memory():
    published = 0

    def count(publication):
        nonlocal published
        published += publication["kind"] == "report"

    source = Parser("p", lambda line, ctx: {"x": line})
    engine = FlowSession(Flow("many", parsers=[source], analyzers=[Analyzer("a", records=[Binding(source, lambda row, ctx: AnalysisReport(row["x"], values=dict(row)))])]), publish=count)
    for index in range(10000):
        engine.feed(str(index))
    snapshot = engine.snapshot()["analyzers"][0]
    assert published == snapshot["report_count"] == 10000
    assert snapshot["latest"] == {}
    assert not engine._records and not engine._reactions
