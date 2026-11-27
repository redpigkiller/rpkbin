"""Saved Automation actions page by request identity, independently of receipts."""

import json
import sqlite3

import pytest

from rpkbin.wave import RunArchive
from rpkbin.wave._archive_format import CHUNK_CHARS, PAGE_BYTES, PREVIEW_CHARS


@pytest.fixture
def archive(tmp_path):
    value = RunArchive(tmp_path / "actions.sqlite3", create=True)
    value.save_attempt(dict(id="job", attempt_id="a", attempt_number=1))
    value.save_attempt(dict(id="job", attempt_id="b", attempt_number=2))
    value.save_attempt(dict(id="other", attempt_id="c", attempt_number=1))
    yield value
    value.close()


def action(store, seq, *, request_id=None, phase="requested", status="requested",
           producer="reaction", attempt="a", name="stop", source=None, args=None,
           kwargs=None, receipt=None, error=None):
    data = dict(version=1, request_id=seq if request_id is None else request_id,
                action=name, args=[] if args is None else args,
                kwargs={} if kwargs is None else kwargs, phase=phase, status=status,
                receipt=receipt, source=source)
    if error is not None:
        data["error"] = error
    assert store.append_flow(attempt, dict(seq=seq, stage="automation", producer=producer,
                                          kind="action", data=data, evidence=dict(line_no=700 + seq, line=f"original {seq}")))
    return data


def page(store, **kwargs):
    return store.automation_history("job", "a", **kwargs)


def test_pending_and_terminal_publications_form_one_row_per_request(archive):
    action(archive, 1)
    action(archive, 2)
    action(archive, 3, request_id=1, phase="returned", status="accepted", receipt={"accepted": True})
    result = page(archive)
    assert [(row["request_id"], row["seq"], row["data"]["status"]) for row in result["rows"]] == [(1, 3, "accepted"), (2, 2, "requested")]
    assert result["next_seq"] == result["end_seq"] == 2
    assert not result["has_more"]
    assert [row["seq"] for row in archive.flow_page("job", "a", stage="automation")["rows"]] == [1, 2, 3]


def test_identical_actions_remain_distinct_and_highest_terminal_seq_wins(archive):
    action(archive, 1)
    action(archive, 2)
    action(archive, 8, request_id=1, phase="failed", status="failed", error="transport lost")
    action(archive, 4, request_id=1, phase="returned", status="rejected", receipt=False)
    result = page(archive)
    assert [(row["request_id"], row["seq"]) for row in result["rows"]] == [(1, 8), (2, 2)]
    assert result["rows"][0]["data"]["error"] == "transport lost"


def test_forward_backward_cursors_use_stable_requests_not_terminal_seq(archive):
    for seq in (1, 2, 3, 4):
        action(archive, seq)
    first = page(archive, limit=2)
    assert [row["request_id"] for row in first["rows"]] == [1, 2]
    assert first["has_more"] and first["next_seq"] == 2
    action(archive, 9, request_id=1, phase="returned", status="accepted")
    second = page(archive, after=first["next_seq"], limit=2)
    assert [row["request_id"] for row in second["rows"]] == [3, 4]
    assert not second["has_more"]
    refreshed = page(archive, before=3)
    assert [(row["request_id"], row["seq"]) for row in refreshed["rows"]] == [(1, 9), (2, 2)]
    backward = page(archive, before=5, limit=2)
    assert [row["request_id"] for row in backward["rows"]] == [3, 4]
    assert backward["has_more"] and backward["next_seq"] == 3
    assert [row["request_id"] for row in page(archive, before=backward["next_seq"])["rows"]] == [1, 2]
    assert page(archive, after=4)["rows"] == []


def test_source_publication_and_raw_log_evidence_are_separate(archive):
    source = dict(seq=200, producer="limits", stage="analyzer", kind="report", name="upper")
    requested = action(archive, 1, source=source, name="send", args=["go"], kwargs={"timeout": 3})
    row = page(archive)["rows"][0]
    assert row["data"] == requested
    assert row["request_id"] == 1 and row["data"]["source"]["seq"] == 200
    assert row["evidence"]["line_no"] == 701
    action(archive, 2, request_id=1, source=source, name="send", phase="returned", status="rejected", receipt={"accepted": False})
    assert page(archive)["rows"][0]["data"]["source"] == source
    assert json.loads(archive.flow_text("job", "a", 1)["text"]) == requested


def test_large_arguments_have_bounded_preview_and_exact_full_chunks(archive):
    huge = 2 ** 150 + 7
    expected = action(archive, 1, args=["中文" * CHUNK_CHARS, huge], kwargs={"values": [True, None, huge]}, receipt={"n": huge})
    result = page(archive)
    row = result["rows"][0]
    assert row["truncated"] and row["data"] == {}
    assert len(row["data_preview"]) <= PREVIEW_CHARS
    assert len(json.dumps(result)) < 10000
    text, offset = [], 0
    while True:
        chunk = archive.flow_text("job", "a", row["seq"], offset=offset)
        assert len(chunk["text"]) <= CHUNK_CHARS
        text.append(chunk["text"])
        offset = chunk["next_offset"]
        if not chunk["has_more"]:
            break
    assert json.loads("".join(text)) == expected
    assert offset == row["data_chars"]


def test_producer_attempt_job_and_run_are_isolated(archive, tmp_path):
    action(archive, 1, producer="left")
    action(archive, 2, producer="right")
    action(archive, 3, producer="left")
    action(archive, 1, producer="other-attempt", attempt="b")
    action(archive, 1, producer="other-job", attempt="c")
    assert [row["request_id"] for row in page(archive, producer="left")["rows"]] == [1, 3]
    assert page(archive, producer="missing")["rows"] == []
    assert archive.automation_history("job", "b")["rows"][0]["producer"] == "other-attempt"
    assert archive.automation_history("other", "c")["rows"][0]["producer"] == "other-job"
    other = RunArchive(tmp_path / "another-run.sqlite3", create=True)
    try:
        other.save_attempt(dict(id="job", attempt_id="a", attempt_number=1))
        action(other, 1, producer="another-run")
        assert page(other)["rows"][0]["producer"] == "another-run"
        assert page(archive)["rows"][0]["producer"] == "left"
    finally:
        other.close()
    with pytest.raises(ValueError, match="Job or attempt"):
        archive.automation_history("other", "a")


@pytest.mark.parametrize("options", [dict(producer=""), dict(producer=3), dict(producer="p" * 257),
                                    dict(after=True), dict(before=-1), dict(after=1.0),
                                    dict(after=1, before=2), dict(limit=0), dict(limit=101), dict(limit=True)])
def test_invalid_requests_are_rejected(archive, options):
    with pytest.raises(ValueError):
        page(archive, **options)


def test_page_bytes_and_requests_remain_bounded_under_history_growth(archive):
    for seq in range(1, 151):
        action(archive, seq, args=["\\" * 4000])
    for seq in range(151, 1300):
        action(archive, seq, request_id=1, phase="returned", status="accepted")
    result = page(archive)
    assert len(result["rows"]) <= 100 and result["has_more"]
    assert len(json.dumps(result, ensure_ascii=True, separators=(",", ":"))) < PAGE_BYTES + 2000
    assert result["rows"][0]["seq"] == 1299 and result["rows"][0]["request_id"] == 1
    assert result["end_seq"] == 150


def test_reopen_keeps_heads_and_rejects_missing_projection(archive):
    action(archive, 1)
    assert archive.append_flow("a", dict(seq=2, stage="parser", producer="parser", kind="record", data={"x": 3}))
    archive.close()
    reopened = RunArchive(archive.path)
    assert page(reopened)["rows"][0]["request_id"] == 1
    assert reopened.flow_latest("job", "a", stage="parser")["rows"][0]["data"] == {"x": 3}
    reopened.close()
    with sqlite3.connect(archive.path) as conn:
        conn.execute("DROP TABLE automation_heads")
    with pytest.raises(ValueError, match="Incomplete"):
        RunArchive(archive.path)


def test_failed_duplicate_publication_cannot_replace_committed_request_head(archive):
    requested = action(archive, 1)
    archive.flush()
    assert not archive.append_flow("a", dict(seq=1, stage="automation", producer="reaction", kind="action",
                                             data={**requested, "phase": "returned", "status": "accepted"}))
    result = page(archive)
    assert result["health"]["error"] is not None
    assert result["rows"][0]["data"] == requested
