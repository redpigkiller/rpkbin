import json
import subprocess
import sys

import pytest

from rpkbin.flow import Flow, FlowSession, Parser


def waveform(source, **kwargs):
    from rpkbin.flow import ViewSpec

    return ViewSpec.waveform(
        "rtl", source=source, axis={"field": "tick", "origin": 10**22},
        series={"mode": "long", "id_field": "signal", "value_field": "value",
                "value_type": "bit"}, **kwargs,
    )


def test_views_are_detached_and_digest_is_canonical():
    parser = Parser("rtl", lambda ctx, line: None)
    from rpkbin.flow import ViewSpec

    axis = {"field": "cycle", "fallback": ["seq"]}
    item = {"id": "snr", "field": "snr", "type": "number"}
    spec = ViewSpec.xy_line("tdd", source=parser, axis=axis,
                           series={"mode": "wide", "items": [item]})
    axis["field"] = "wrong"
    item["type"] = "state"
    with pytest.raises(TypeError):
        spec.axis["field"] = "wrong"
    flow = Flow("example", parsers=[parser], views=[spec])
    exported = FlowSession(flow)._snapshot()["views"]
    assert json.loads(json.dumps(exported)) == exported
    assert exported["items"][0]["axis"]["field"] == "cycle"
    assert exported["items"][0]["series"]["items"][0]["type"] == "number"
    assert exported["digest"] == FlowSession(flow)._snapshot()["views"]["digest"]
    assert exported["digest"] != FlowSession(
        Flow("example", parsers=[parser], views=[waveform(parser)])
    )._snapshot()["views"]["digest"]


def test_view_rejects_invalid_sources_duplicate_ids_and_executable_options():
    parser = Parser("rtl", lambda ctx, line: None)
    spec = waveform(parser)
    with pytest.raises(ValueError):
        Flow("example", parsers=[Parser("rtl", lambda ctx, line: None)], views=[spec])
    with pytest.raises(ValueError):
        Flow("example", parsers=[parser], views=[spec, spec])
    for options in ({"callback": lambda: None}, {"radix": "<script>"}, {"bins": float("inf")}):
        with pytest.raises((TypeError, ValueError)):
            waveform(parser, options=options)


def test_view_import_is_standalone():
    subprocess.run([sys.executable, "-c", """
import sys
from rpkbin.flow import ViewSpec
assert not any(n.startswith(('rpkbin.wave', 'rpkbin.jobcore')) for n in sys.modules)
"""], check=True)
