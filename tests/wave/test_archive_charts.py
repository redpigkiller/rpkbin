"""Chart reads use the same committed prefix as original Log and Flow records."""

import json
import sqlite3
import time
from contextlib import contextmanager

import pytest

from rpkbin.flow import Flow, FlowSession, Parser, ViewSpec
from rpkbin.wave.archive import RunArchive


def specs():
    parser = Parser("sim", lambda ctx, line: None)
    rtl = ViewSpec.waveform(
        "rtl", source=parser, axis={"field": "tick", "origin": 10**22},
        series={"mode": "long", "id_field": "signal", "path_field": "path", "value_field": "value",
                "overrides": [{"id": "bus", "type": "bus", "bits": 80}]},
    )
    xy = ViewSpec.xy_line(
        "tdd", source=parser, axis={"field": "cycle"},
        series={"mode": "wide", "items": [{"id": i, "field": i} for i in ("snr", "evm")]},
        options={"thresholds": [{"series_id": "snr", "side": "min", "value": 20}]},
    )
    return parser, rtl, xy


def make_archive(tmp_path, *views):
    parser = specs()[0]
    flow = Flow("sim", parsers=[parser], views=views)
    store = RunArchive(tmp_path / "run.sqlite3", create=True)
    store.save_attempt(dict(id="job", attempt_id="attempt", attempt_number=1,
                            flow=FlowSession(flow)._snapshot()), flush=True)
    return store


def append(store, rows, start=1):
    raw, publications = [], []
    for seq, row in enumerate(rows, start):
        raw.append((dict(line_no=seq, time=seq, source="stdout", text=str(row)), seq / 10))
        publications.append(dict(seq=seq, stage="parser", producer="sim",
                                 kind="record", data=row, evidence={"line_no": seq}))
    return store._append_source_batch("attempt", raw, publications)


def test_left_hold_wide_bus_edge_and_prefix(tmp_path):
    _, rtl, _ = specs()
    spec = rtl.to_json()
    store = make_archive(tmp_path, ViewSpec(**spec))
    try:
        assert append(store, [dict(tick=10**22 + x, signal="bus", path=["top", "dut", "bus"], value=v)
                              for x, v in [(5, 1 << 79), (8, (1 << 79) + 16), (8, 0), (12, 16)]])
        store.flush()
        catalog = store.chart_series("job", "attempt", view_id="rtl", prefix=["top", "dut"])
        assert [item["id"] for item in catalog["series"]] == ["bus"]
        window = store.chart_window("job", "attempt", view_id="rtl", series=["bus"], lo=8, hi=12)
        assert window["left"]["bus"]["value"] == str(1 << 79)
        assert [(p["x"], p["flow_seq"], p["log_seq"]) for p in window["points"]] == [(8, 2, 2), (8, 3, 3)]
        for direction, expected in [("forward", 2), ("backward", 4)]:
            hit = store.chart_find("job", "attempt", view_id="rtl", series_id="bus",
                                   op="rising", bit_slice=[4, 4], direction=direction)["hit"]
            assert hit["log_seq"] == expected
    finally:
        store.close()


@pytest.mark.parametrize("view", ["rtl", "tdd"])
def test_20k_overview_preserves_pulse_spike_and_evidence(tmp_path, view):
    _, rtl, xy = specs()
    store = make_archive(tmp_path, ViewSpec(**(rtl if view == "rtl" else xy).to_json()))
    try:
        for offset in range(0, 20000, 250):
            assert append(store, [dict(tick=10**22 + i, signal="bus", value=int(i == 21),
                                       cycle=i, snr=3 if i == 21 else 30,
                                       evm=None if i == 22 else -25)
                                  for i in range(offset, offset + 250)], offset + 1)
        store.flush()
        started = time.perf_counter()
        overview = store.chart_window("job", "attempt", view_id=view,
                                      series=["bus"] if view == "rtl" else ["snr", "evm"],
                                      lo=0, hi=20000, mode="overview", buckets=120)
        elapsed = (time.perf_counter() - started) * 1000
        size = len(json.dumps(overview, separators=(",", ":")).encode())
        print(f"{view}: {overview['scanned']} rows, {elapsed:.1f} ms, {size} bytes")
        assert overview["complete"] and size <= 256 * 1024
        if view == "rtl":
            cell = overview["buckets"]["bus"][0]
            assert cell["transitions"] == 2 and cell["first_change"]["log_seq"] == 22
        else:
            assert overview["buckets"]["snr"][0]["min"]["log_seq"] == 22
            assert overview["buckets"]["snr"][0]["violation"]["value"] == 3
            assert overview["buckets"]["evm"][0]["has_gap"]
    finally:
        store.close()


def test_chart_rollback_and_pinned_watermark(tmp_path):
    _, rtl, _ = specs()
    store = make_archive(tmp_path, ViewSpec(**rtl.to_json()))
    try:
        first = dict(tick=10**22 + 5, signal="bus", value=1)
        assert append(store, [first])
        store.flush()
        old = store.chart_meta("job", "attempt")["as_of_seq"]
        store._writer.execute("CREATE TRIGGER fail_chart BEFORE INSERT ON chart_samples "
                              "WHEN new.flow_seq = 3 BEGIN SELECT RAISE(ABORT,'injected'); END")
        with store.record_batch():
            assert append(store, [dict(first, tick=10**22 + 3)], 2)
            assert not append(store, [dict(first, tick=10**22 + 8)], 3)
        assert store.error and store.chart_meta("job", "attempt")["as_of_seq"] == old == 1
        with sqlite3.connect(store.path) as conn:
            assert [conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    for table in ("chart_samples", "records", "flow_records")] == [1, 1, 1]
    finally:
        store.close()


def test_fallback_is_recent_parser_scalars_only(tmp_path):
    store = make_archive(tmp_path)
    try:
        assert append(store, [dict(snr=i, evm=-i, ignored=[i]) for i in range(120)])
        store.flush()
        meta = store.chart_meta("job", "attempt")
        assert meta["views"][0]["id"] == "__auto__"
        one = store.chart_series("job", "attempt", view_id="__auto__", limit=1)
        assert [item["id"] for item in one["series"]] == ["evm"]
        two = store.chart_series("job", "attempt", view_id="__auto__", limit=1, cursor=one["next_cursor"])
        assert two["complete"] and [item["id"] for item in two["series"]] == ["snr"]
        result = store.chart_series("job", "attempt", view_id="__auto__", query="snr")
        assert [item["id"] for item in result["series"]] == ["snr"]
        data = store.chart_window("job", "attempt", view_id="__auto__", axis="seq",
                                  series=["snr"], lo=0, hi=200)
        assert len(data["points"]) == 100
        assert data["points"][0]["log_seq"] == 21
    finally:
        store.close()


def test_archive_chart_queries_use_ordered_index_seeks(tmp_path, monkeypatch):
    _, rtl, _ = specs()
    store = make_archive(tmp_path, ViewSpec(**rtl.to_json()))
    statements = []
    original = store._reader

    @contextmanager
    def capture():
        with original() as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    try:
        assert append(store, [dict(tick=10**22 + x, signal="bus", path=["top", "bus"], value=x)
                              for x in range(10)])
        store.flush()
        monkeypatch.setattr(store, "_reader", capture)
        store.chart_series("job", "attempt", view_id="rtl", prefix=["top"])
        for axis in ("time", "elapsed", "seq"):
            store.chart_window("job", "attempt", view_id="rtl", series=["bus"], axis=axis, lo=2, hi=6)
        selected = [sql for sql in statements if "ORDER BY" in sql and
                    ("FROM chart_samples" in sql or "FROM chart_series" in sql)]
        assert len(selected) == 7
        with original() as conn:
            for sql in selected:
                plan = " ".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql))
                assert "SEARCH" in plan and "SCAN" not in plan and "TEMP B-TREE" not in plan
                if "SELECT x" in sql or "SELECT elapsed" in sql:
                    assert "COVERING INDEX" in plan
    finally:
        store.close()


def test_scatter_and_histogram_preserve_sample_identity_and_stats(tmp_path):
    source = {"stage": "parser", "producer": "sim"}
    series = {"mode": "wide", "items": [{"id": "q", "field": "q"}]}
    scatter = ViewSpec.scatter("iq", source=source, axis={"field": "i"}, series=series,
                               options={"equal_aspect": True})
    hist = ViewSpec.histogram("dist", source=source, axis={"field": "i"}, series=series,
                              options={"bins": [0, 2, 4]})
    store = make_archive(tmp_path, scatter, hist)
    try:
        assert append(store, [dict(i=i, q=q) for i, q in enumerate([-1, 0, 2, 4, 5, None])])
        store.flush()
        iq = store.chart_window("job", "attempt", view_id="iq", series=["q"], lo=0, hi=8, axis="seq")
        assert [(p["x"], p["y"], p["log_seq"]) for p in iq["points"]] == [
            (0, -1, 1), (1, 0, 2), (2, 2, 3), (3, 4, 4), (4, 5, 5),
        ]
        dist = store.chart_window("job", "attempt", view_id="dist", series=["q"], lo=0, hi=8)
        assert dist["bins"] == [1, 2] and dist["underflow"] == dist["overflow"] == 1
        assert dist["stats"] == dict(n=5, min=-1, max=5, mean=2, std=pytest.approx((26 / 5)**.5), missing=1)
    finally:
        store.close()


def test_resuming_overview_and_edge_search_keep_the_pinned_prefix(tmp_path, monkeypatch):
    from rpkbin.wave import _archive_charts

    _, rtl, _ = specs()
    store = make_archive(tmp_path, ViewSpec(**rtl.to_json()))
    try:
        assert append(store, [dict(tick=10**22 + x, signal="bus", value=v)
                              for x, v in enumerate([0, 0, 16, 0, 0, 0, 0, 0])])
        store.flush()
        monkeypatch.setattr(_archive_charts, "_SCAN", 2)
        kwargs = dict(view_id="rtl", series=["bus"], mode="overview", buckets=1,
                      lo=0, hi=8, as_of_seq=8)
        first = store.chart_window("job", "attempt", **kwargs)
        assert not first["complete"]
        assert append(store, [dict(tick=10**22 + 1, signal="bus", value=32)], 9)
        store.flush()
        transitions = first["buckets"]["bus"][0]["transitions"]
        next_page = first
        for _ in range(6):
            next_page = store.chart_window("job", "attempt", **kwargs, cursor=next_page["next_cursor"])
            transitions += sum(bucket["transitions"] for bucket in next_page["buckets"]["bus"])
            if next_page["complete"]:
                break
        assert next_page["complete"] and transitions == 2
        find = dict(view_id="rtl", series_id="bus", op="rising", bit_slice=[4, 4],
                    direction="backward", as_of_seq=8)
        found = store.chart_find("job", "attempt", **find)
        for _ in range(6):
            if found["complete"]:
                break
            found = store.chart_find("job", "attempt", **find, cursor=found["next_cursor"])
        assert found["hit"]["log_seq"] == 3
    finally:
        store.close()


def test_auto_histogram_counts_each_pinned_value_once_across_phases(tmp_path, monkeypatch):
    from rpkbin.wave import _archive_charts

    hist = ViewSpec.histogram(
        "dist", source={"stage": "parser", "producer": "sim"}, axis={"field": "cycle"},
        series={"mode": "wide", "items": [{"id": "q", "field": "q"}]}, options={"bins": 2},
    )
    store = make_archive(tmp_path, hist)
    try:
        assert append(store, [dict(cycle=i, q=i) for i in range(9)])
        store.flush()
        monkeypatch.setattr(_archive_charts, "_SCAN", 4)
        query = dict(view_id="dist", series=["q"], lo=0, hi=9, as_of_seq=9)
        cursor, counts = None, [0, 0]
        for _ in range(8):
            page = store.chart_window("job", "attempt", **query, cursor=cursor)
            if page["phase"] == "bins":
                assert page["edges"] == [0, 4, 8]
                counts = [a + b for a, b in zip(counts, page["bins"])]
            if page["complete"]:
                break
            cursor = page["next_cursor"]
        assert page["complete"] and counts == [4, 5]
        assert page["stats"]["n"] == 9 and page["stats"]["mean"] == 4
    finally:
        store.close()


def test_chart_projection_and_fallback_batch_record_lookups_and_hex_bin_find(tmp_path, monkeypatch):
    parser = Parser("sim", lambda ctx, line: None)
    signed_rtl = ViewSpec.waveform(
        "rtl", source={"stage": "parser", "producer": "sim"}, axis={"field": "tick"},
        series={
            "mode": "long", "id_field": "signal", "path_field": "path", "value_field": "value",
            "overrides": [{"id": "bus", "type": "signed_bus", "bits": 16}],
        },
    )
    store = make_archive(tmp_path, signed_rtl)
    try:
        # 550 records exceeds the 500-item chunk size, requiring at most 2 IN-clause queries.
        rows = [
            dict(tick=i, signal="bus", path=["bus"], value=(-0x12 if i == 525 else 0x2d0d if i == 300 else i))
            for i in range(1, 551)
        ]
        statements = []
        store._writer.set_trace_callback(statements.append)
        try:
            assert append(store, rows)
        finally:
            store._writer.set_trace_callback(None)
        store.flush()
        elapsed_queries = [sql for sql in statements if "SELECT seq,elapsed FROM records" in sql]
        assert len(elapsed_queries) == 2
        assert not [sql for sql in statements if "SELECT elapsed FROM records WHERE attempt_id=? AND seq=?" in sql]

        # Hex and binary literals in chart_find (positive, negative, and bit-sliced).
        hit_hex = store.chart_find("job", "attempt", view_id="rtl", series_id="bus", op="eq", value="0x2d0d")
        assert hit_hex["hit"]["flow_seq"] == 300
        hit_bin = store.chart_find("job", "attempt", view_id="rtl", series_id="bus", op="eq", value="0B10110100001101")
        assert hit_bin["hit"]["flow_seq"] == 300
        hit_neg = store.chart_find("job", "attempt", view_id="rtl", series_id="bus", op="eq", value="-0x12")
        assert hit_neg["hit"]["flow_seq"] == 525
        hit_neg_bin = store.chart_find("job", "attempt", view_id="rtl", series_id="bus", op="eq", value="-0b10010")
        assert hit_neg_bin["hit"]["flow_seq"] == 525
        hit_slice = store.chart_find(
            "job", "attempt", view_id="rtl", series_id="bus", op="eq", value="0b1101", bit_slice=[3, 0],
            anchor=[299, 299],
        )
        assert hit_slice["hit"]["flow_seq"] == 300
        for bad in ("0x", "0b2", "0x1g", "+0x10", "0x" + "f" * 80):
            with pytest.raises(ValueError, match="integer"):
                store.chart_find("job", "attempt", view_id="rtl", series_id="bus", op="eq", value=bad)
    finally:
        store.close()

    # Fallback auto-chart also batches record sequence checks into a single IN query.
    auto_flow = Flow("auto", parsers=[parser])
    auto_store = RunArchive(tmp_path / "auto.sqlite3", create=True)
    auto_store.save_attempt(dict(id="job", attempt_id="attempt", attempt_number=1,
                                 flow=FlowSession(auto_flow)._snapshot()), flush=True)
    try:
        assert append(auto_store, [dict(v=i) for i in range(1, 40)])
        auto_store.flush()
        traced = []
        original_reader = auto_store._reader

        @contextmanager
        def traced_reader():
            with original_reader() as conn:
                conn.set_trace_callback(traced.append)
                try:
                    yield conn
                finally:
                    conn.set_trace_callback(None)

        monkeypatch.setattr(auto_store, "_reader", traced_reader)
        meta = auto_store.chart_meta("job", "attempt")
        assert meta["views"][0]["id"] == "__auto__"
        rec_checks = [sql for sql in traced if "SELECT seq FROM records" in sql]
        assert len(rec_checks) == 1
    finally:
        auto_store.close()


def test_four_state_projection_slice_find_and_wide_lane_window(tmp_path):
    rtl = ViewSpec.waveform(
        "rtl",
        source={"stage": "parser", "producer": "sim"},
        axis={"field": "tick"},
        series={
            "mode": "long",
            "id_field": "signal",
            "path_field": "path",
            "value_field": "value",
            "value_type": "bus",
            "bits": 8,
            "overrides": [
                {"id": "b", "type": "bit"},
                {"id": "bus4", "type": "bus", "bits": 4},
                {"id": "bus8", "type": "bus", "bits": 8},
            ],
        },
    )
    store = make_archive(tmp_path, rtl)
    try:
        rows = [
            dict(tick=1, signal="b", path=["top", "b"], value=0),
            dict(tick=2, signal="b", path=["top", "b"], value="1"),
            dict(tick=3, signal="b", path=["top", "b"], value="X"),
            dict(tick=4, signal="b", path=["top", "b"], value="Z"),
            dict(tick=5, signal="b", path=["top", "b"], value=None),
            dict(tick=6, signal="b", path=["top", "b"], value="0"),
            dict(tick=7, signal="b", path=["top", "b"], value=1),
            dict(tick=8, signal="b", path=["top", "b"], value="2"),
            dict(tick=9, signal="b", path=["top", "b"], value="abc"),
            dict(tick=10, signal="bus4", path=["top", "bus4"], value="4'b10xz"),
            dict(tick=11, signal="bus4", path=["top", "bus4"], value=0b1010),
            dict(tick=12, signal="bus4", path=["top", "bus4"], value="Z"),
            dict(tick=13, signal="bus4", path=["top", "bus4"], value="X"),
            dict(tick=14, signal="bus8", path=["top", "bus8"], value="8'h1x"),
            dict(tick=15, signal="bus8", path=["top", "bus8"], value=0x55),
            dict(tick=16, signal="bus8", path=["top", "bus8"], value="257"),
            *[dict(tick=20 + i, signal=f"w{i}", path=["wide", f"w{i}"], value=i) for i in range(24)],
        ]
        assert append(store, rows)
        store.flush()

        meta = store.chart_meta("job", "attempt")
        assert meta["views"][0]["diagnostics"]["invalid"] == 3

        assert store.chart_find("job", "attempt", view_id="rtl", series_id="b", op="eq", value="X")["hit"]["log_seq"] == 3
        assert store.chart_find(
            "job", "attempt", view_id="rtl", series_id="b", op="eq", value="X", anchor=[3, 3]
        )["hit"]["log_seq"] == 5
        assert store.chart_find("job", "attempt", view_id="rtl", series_id="b", op="eq", value="Z")["hit"]["log_seq"] == 4
        assert store.chart_find("job", "attempt", view_id="rtl", series_id="b", op="rising")["hit"]["log_seq"] == 2
        assert store.chart_find(
            "job", "attempt", view_id="rtl", series_id="b", op="rising", anchor=[2, 2]
        )["hit"]["log_seq"] == 7

        assert store.chart_find(
            "job", "attempt", view_id="rtl", series_id="bus4", op="eq", value="X", bit_slice=[1, 1]
        )["hit"]["log_seq"] == 10
        assert store.chart_find(
            "job", "attempt", view_id="rtl", series_id="bus4", op="eq", value="Z", bit_slice=[0, 0]
        )["hit"]["log_seq"] == 10
        assert store.chart_find(
            "job", "attempt", view_id="rtl", series_id="bus4", op="eq", value="1", bit_slice=[3, 3]
        )["hit"]["log_seq"] == 10
        assert store.chart_find(
            "job", "attempt", view_id="rtl", series_id="bus8", op="eq", value="X"
        )["hit"]["log_seq"] == 14

        wide_win = store.chart_window(
            "job", "attempt", view_id="rtl", series=[f"w{i}" for i in range(24)], lo=0, hi=60, mode="raw"
        )
        assert wide_win["complete"]
        assert {pt["series_id"] for pt in wide_win["points"]} == {f"w{i}" for i in range(24)}

        vcd_text = store.export_vcd("job", "attempt", view_id="rtl", series=["b", "bus4", "bus8"])
        assert "$timescale 1ns $end" in vcd_text
        assert "$scope module top $end" in vcd_text
        assert "$var wire 1 " in vcd_text
        assert "$var wire 4 " in vcd_text
        assert "$var wire 8 " in vcd_text
        assert "#1\n0" in vcd_text or "\n#1\n" in vcd_text
        assert "b10xz " in vcd_text
    finally:
        store.close()
