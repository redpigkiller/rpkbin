"""Latest projections preserve publication identity and never change user data."""

import json
import sqlite3

import pytest

from rpkbin.wave import RunArchive
from rpkbin.wave._archive_format import CHUNK_CHARS, PREVIEW_CHARS


@pytest.fixture
def archive(tmp_path):
    store = RunArchive(tmp_path / "projection.sqlite3", create=True)
    store.save_attempt(dict(id="job", attempt_id="a", attempt_number=1))
    store.save_attempt(dict(id="job", attempt_id="b", attempt_number=2))
    yield store
    store.close()


def publish(store, seq, data, *, producer="p", stage="parser", kind=None, attempt="a"):
    assert store.append_flow(attempt, dict(seq=seq, stage=stage, producer=producer,
                                          kind=kind or ("record" if stage == "parser" else "report"),
                                          data=data, evidence=dict(line_no=seq + 100, line=f"input {seq}")))


def report(name, *, result=None, reason="", **values):
    return dict(version=1, name=name, result=result, reason=reason, values=values)


def latest(store, **kwargs):
    return store.flow_latest("job", "a", stage="parser", **kwargs)


def test_record_and_fields_keep_absence_null_and_provenance_separate(archive):
    publish(archive, 1, {"old": 1, "replace": "previous", "null": 9})
    publish(archive, 2, {"replace": "latest", "null": None})
    whole = latest(archive)["rows"][0]
    assert whole["data"] == {"replace": "latest", "null": None}
    assert whole["seq"] == 2
    assert whole["evidence"]["line_no"] == 102
    assert whole["field_sources"] == {}
    assert whole["known_fields"] == ["null", "old", "replace"]
    assert not whole["fields_truncated"]

    merged = latest(archive, mode="fields")["rows"][0]
    assert merged["data"] == {"old": 1, "replace": "latest", "null": None}
    assert merged["field_sources"] == {"old": 1, "replace": 2, "null": 2}
    assert merged["evidence"] == whole["evidence"]
    assert "field_sources" not in merged["data"]


def test_analyzer_groups_by_producer_and_report_name_and_retains_latest_metadata(archive):
    publish(archive, 1, report("alpha", result="collecting", reason="first", old=1, value=1), stage="analyzer")
    publish(archive, 2, report("beta", result="arbitrary status", value=99), stage="analyzer")
    publish(archive, 3, report("alpha", result=None, reason="latest", value=None), stage="analyzer")
    publish(archive, 4, report("alpha", result="other producer", value=4), stage="analyzer", producer="q")
    publish(archive, 5, {"name": "alpha", "reason": "diagnostic"}, stage="analyzer", kind="error")
    publish(archive, 6, report("alpha", value=100), stage="analyzer", attempt="b")

    whole = archive.flow_latest("job", "a", stage="analyzer")["rows"]
    assert [(row["producer"], row["report_name"], row["seq"]) for row in whole] == [("p", "beta", 2), ("p", "alpha", 3), ("q", "alpha", 4)]
    assert whole[1]["data"] == report("alpha", reason="latest", value=None)
    merged = archive.flow_latest("job", "a", stage="analyzer", producer="p", report_name="alpha", mode="fields")["rows"][0]
    assert merged["data"] == report("alpha", reason="latest", old=1, value=None)
    assert merged["field_sources"] == {"old": 1, "value": 3}
    assert merged["known_fields"] == ["old", "value"]
    history = archive.flow_page("job", "a", report_name="alpha")["rows"]
    assert [row["seq"] for row in history] == [1, 3, 4]
    assert archive.flow_page("job", "a", report_name="alpha", kind="error")["rows"] == []
    assert archive.flow_latest("job", "b", stage="analyzer")["rows"][0]["seq"] == 6


def test_cursors_apply_after_grouping_and_page_in_both_directions(archive):
    publish(archive, 1, {"v": 1}, producer="a")
    publish(archive, 2, {"v": 2}, producer="b")
    publish(archive, 3, {"v": 3}, producer="c")
    publish(archive, 4, {"v": 4}, producer="a")
    assert [row["seq"] for row in latest(archive, before=4)["rows"]] == [2, 3]
    assert [row["seq"] for row in latest(archive, after=2)["rows"]] == [3, 4]
    page = latest(archive, limit=1)
    assert [row["seq"] for row in page["rows"]] == [2]
    assert page["has_more"] and page["next_seq"] == 2
    assert [row["seq"] for row in latest(archive, after=page["next_seq"])["rows"]] == [3, 4]
    page = latest(archive, before=5, limit=2)
    assert [row["seq"] for row in page["rows"]] == [3, 4]
    assert page["has_more"] and page["next_seq"] == 3
    assert [row["seq"] for row in latest(archive, before=page["next_seq"])["rows"]] == [2]


def test_exact_bigints_and_nested_values_are_not_sqlite_numeric_values(archive):
    large = 2 ** 120 + 1
    publish(archive, 1, {"number": large, "nested": {"x": [large, -large, None, True]}})
    publish(archive, 2, {})
    merged = latest(archive, mode="fields")["rows"][0]
    assert merged["data"] == {"number": large, "nested": {"x": [large, -large, None, True]}}
    text = archive.flow_latest_text("job", "a", 2)
    assert json.loads(text["text"])["data"] == merged["data"]
    assert text["total"] == len(text["text"])
    assert not text["has_more"]


def test_oversized_projection_streams_exact_data_and_provenance(archive):
    data = {"large": "中文\"\\" * CHUNK_CHARS, "integer": 2 ** 100 + 7}
    publish(archive, 1, data)
    publish(archive, 2, {"new": None})
    page = latest(archive, mode="fields")
    row = page["rows"][0]
    assert row["truncated"] and row["data"] == {}
    assert len(row["data_preview"]) <= PREVIEW_CHARS
    assert len(json.dumps(page)) < 16000
    chunks, offset = [], 0
    while True:
        chunk = archive.flow_latest_text("job", "a", 2, offset=offset)
        assert len(chunk["text"]) <= CHUNK_CHARS
        chunks.append(chunk["text"])
        offset = chunk["next_offset"]
        if not chunk["has_more"]:
            break
    result = json.loads("".join(chunks))
    assert result == dict(data={**data, "new": None}, field_sources={"large": 1, "integer": 1, "new": 2})
    assert offset == chunk["total"]
    assert archive.flow_latest_text("job", "a", 2, offset=offset)["text"] == ""
    with pytest.raises(ValueError, match="beyond"):
        archive.flow_latest_text("job", "a", 2, offset=offset + 1)


def test_report_full_projection_keeps_metadata_and_large_values(archive):
    publish(archive, 1, report("温度", result="not a verdict", reason="r" * 4000, old=7), stage="analyzer")
    publish(archive, 2, report("温度", reason="new" * 4000, new=8), stage="analyzer")
    row = archive.flow_latest("job", "a", stage="analyzer", mode="fields")["rows"][0]
    assert row["truncated"]
    chunk = archive.flow_latest_text("job", "a", 2)
    assert json.loads(chunk["text"]) == dict(data=report("温度", reason="new" * 4000, old=7, new=8), field_sources={"old": 1, "new": 2})
    assert chunk["total"] == len(chunk["text"])


def test_projection_changes_require_refresh_but_saved_record_stays_readable(archive):
    publish(archive, 1, {"x": 1})
    assert archive.flow_latest_text("job", "a", 1, limit=4)["has_more"]
    publish(archive, 2, {"y": 2})
    with pytest.raises(ValueError, match="refresh"):
        archive.flow_latest_text("job", "a", 1, offset=4)
    assert json.loads(archive.flow_text("job", "a", 1)["text"]) == {"x": 1}


def test_large_field_catalog_and_provenance_are_bounded_and_retrievable(archive):
    values = {f"key-{index:04d}": index for index in range(500)}
    publish(archive, 1, values)
    publish(archive, 2, {})
    row = latest(archive, mode="fields")["rows"][0]
    assert row["truncated"] and row["fields_truncated"]
    assert len(row["known_fields"]) <= 256
    assert len(json.dumps(row["known_fields"], separators=(",", ":"))) <= PREVIEW_CHARS
    full = archive.flow_latest_text("job", "a", 2)
    assert json.loads(full["text"]) == dict(data=values, field_sources=dict.fromkeys(values, 1))


def test_huge_field_key_is_never_loaded_into_catalog_or_truncated_identity(archive):
    key = "large-key" * CHUNK_CHARS
    publish(archive, 1, {key: 2 ** 100})
    row = latest(archive, mode="fields")["rows"][0]
    assert row["known_fields"] == [] and row["fields_truncated"]
    # Skip directly into the SQL span; no reconstruction of the preceding key.
    chunk = archive.flow_latest_text("job", "a", 1, offset=100000, limit=100)
    expected = json.dumps(dict(data={key: 2 ** 100}, field_sources={key: 1}), separators=(",", ":"))
    assert chunk["text"] == expected[100000:100100]
    assert chunk["total"] == len(expected)


def test_latest_projection_does_not_drop_fields_beyond_history_scan_limit(archive):
    publish(archive, 1, {"old": 1})
    for seq in range(2, 1105):
        publish(archive, seq, {"current": seq})
    row = latest(archive, mode="fields")["rows"][0]
    assert row["data"] == {"old": 1, "current": 1104}
    assert row["field_sources"] == {"old": 1, "current": 1104}
    assert latest(archive, before=1104)["rows"] == []


def test_defensive_out_of_order_append_still_chooses_largest_seq(archive):
    publish(archive, 3, {"new": 3, "shared": 3})
    publish(archive, 1, {"old": 1, "shared": 1})
    assert latest(archive)["rows"][0]["data"] == {"new": 3, "shared": 3}
    row = latest(archive, mode="fields")["rows"][0]
    assert row["data"] == {"new": 3, "old": 1, "shared": 3}
    assert row["seq"] == 3


def test_report_name_catalog_paginates_distinct_literal_prefixes(archive):
    for seq, (producer, name) in enumerate((("p", "a%_1"), ("q", "a%_1"), ("p", "a%_2"), ("p", "abc"), ("q", "<script>")), 1):
        publish(archive, seq, report(name), producer=producer, stage="analyzer")
    page = archive.flow_report_names("job", "a", query="a%_", limit=1)
    assert page["rows"] == ["a%_1"] and page["has_more"]
    assert archive.flow_report_names("job", "a", query="a%_", after=page["cursor"])["rows"] == ["a%_2"]
    assert archive.flow_report_names("job", "a", producer="q")["rows"] == ["<script>", "a%_1"]
    assert archive.flow_report_names("job", "b")["rows"] == []


@pytest.mark.parametrize("options", [dict(stage="trigger"), dict(stage="parser", report_name="r"),
                                    dict(stage="parser", mode="unknown"), dict(stage="parser", producer=""),
                                    dict(stage="parser", after=True), dict(stage="parser", before=-1),
                                    dict(stage="parser", after=1, before=2), dict(stage="parser", limit=101)])
def test_invalid_latest_queries_are_errors(archive, options):
    with pytest.raises(ValueError):
        archive.flow_latest("job", "a", **options)


def test_cross_job_access_and_invalid_full_requests_are_errors(archive):
    publish(archive, 1, {"x": 1})
    for call in (lambda: archive.flow_latest("other", "a", stage="parser"),
                 lambda: archive.flow_latest_text("other", "a", 1),
                 lambda: archive.flow_report_names("other", "a"),
                 lambda: archive.flow_latest_text("job", "a", 1, limit=CHUNK_CHARS + 1),
                 lambda: archive.flow_latest_text("job", "a", True),
                 lambda: archive.flow_report_names("job", "a", query=3)):
        with pytest.raises(ValueError):
            call()


@pytest.mark.parametrize("version", [1, 2, 3])
def test_previous_archive_schemas_are_rejected(archive, version):
    archive.close()
    with sqlite3.connect(archive.path) as conn:
        conn.execute("UPDATE metadata SET value=? WHERE key='schema_version'", (str(version),))
    with pytest.raises(ValueError, match="schema"):
        RunArchive(archive.path)


def test_missing_projection_is_corruption_not_a_fallback(archive):
    archive.close()
    with sqlite3.connect(archive.path) as conn:
        conn.execute("DROP TABLE flow_heads")
    with pytest.raises(ValueError, match="Incomplete"):
        RunArchive(archive.path)


def test_projection_reopen_and_duplicate_failure_are_transactional(archive):
    publish(archive, 1, {"x": 1})
    archive.flush()
    assert not archive.append_flow("a", dict(seq=1, stage="parser", producer="p", kind="record", data={"x": 999}))
    assert archive.error is not None
    archive.close()
    reopened = RunArchive(archive.path)
    assert latest(reopened, mode="fields")["rows"][0]["data"] == {"x": 1}
    reopened.close()
