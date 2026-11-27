"""Independent S13 contract checks; failures are intentionally not xfailed."""

from rpkbin.wave import WaveWorkspace

import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from hashlib import sha256
from threading import Event

import pytest

from rpkbin.flow import Flow
from rpkbin.jobcore import ControlReceipt, Job, JobManager
from rpkbin.wave import FileSource, ReanalysisSource, RunArchive, TailSource
from rpkbin.wave.cli import main


CAPABILITIES = (
    "has_process", "can_stop", "can_send", "can_interrupt", "can_signal",
    "can_kill", "can_rerun",
)


def _identity(row):
    return row["id"], row["attempt_id"]


def _guard(row):
    return dict(expected_attempt=row["attempt_id"], expected_submission=row["submission_id"])


def _fingerprint(path):
    """Hash persisted data; opening a reader can create an empty WAL and SHM."""
    fingerprints = {path.name: sha256(path.read_bytes()).hexdigest()}
    for suffix in ("-wal", "-journal"):
        item = path.with_name(path.name + suffix)
        if item.exists() and (data := item.read_bytes()):
            fingerprints[item.name] = sha256(data).hexdigest()
    return fingerprints


def _saved(path, row):
    with closing(RunArchive(path)) as archive:
        return archive.attempt(*_identity(row))


def _log(path, row):
    with closing(RunArchive(path)) as archive:
        records, cursor = [], None
        while True:
            page = archive.page(*_identity(row), cursor=cursor, limit=29)
            for item in page["rows"]:
                seq, offset, chunks = item["seq"], 0, []
                while True:
                    chunk = archive.read_text(*_identity(row), seq, offset=offset)
                    chunks.append(chunk["text"])
                    if not chunk["has_more"]:
                        break
                    offset = chunk["next_offset"]
                records.append((seq, "".join(chunks)))
            if not page["has_more"]:
                return records
            cursor = page["last"]


def _parser_rows(path, row, producer):
    with closing(RunArchive(path)) as archive:
        rows, after = [], None
        while True:
            page = archive.flow_page(*_identity(row), producer=producer, kind="record",
                                     after=after, limit=31)
            rows.extend((item["evidence"]["line_no"], item["data"]) for item in page["rows"])
            if not page["has_more"]:
                return rows
            after = page["next_seq"]


def _flow(path, body="flow = Flow('audit')\n"):
    path.write_text("from rpkbin.flow import Flow, Parser\n" + body, encoding="utf-8")
    return path


@pytest.fixture
def process_archive(tmp_path):
    payload = tmp_path / "child-output.bin"
    long_line = "界😀" * 12001 + " tail"
    lines = ["A\u030a 𠮷", "", "literal � and invalid �", long_line]
    lines += [f"row {index % 13:02}" for index in range(269)]
    lines += ["last without terminator"]
    payload.write_bytes("\r\n".join(lines).encode().replace(b"invalid \xef\xbf\xbd", b"invalid \xff"))
    management = "operator note\nkept in one record \x1b[31m"
    with closing(WaveWorkspace(JobManager(), archive_dir=tmp_path / "process")) as manager:
        job = manager.add(Job(
            "selected", [sys.executable, "-c",
                         "import pathlib,sys; sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())",
                         str(payload)], pty=False, max_log_lines=7,
        ), flow=Flow("original"))
        # Decoy lives in the same archive; selecting the latest attempt is insufficient.
        decoy = manager.add(Job(
            "decoy", [sys.executable, "-c", "print('not the selected attempt')"], pty=False,
        ))
        job.log(management)
        manager.start()
        assert manager._drain(15)
        row = job.to_snapshot_dict(include_details=True)
        assert row["returncode"] == 0 and row["finalized"]
        assert decoy.to_snapshot_dict()["returncode"] == 0
        path = manager.archive.path
    payload.unlink()
    return path, job, row, [management, *lines]


def test_reanalysis_preserves_original_archive_and_attempt(tmp_path, process_archive):
    path, job, row, _ = process_archive
    before = _saved(path, row)
    original_files = _fingerprint(path)
    assert path.name in original_files and before["verified"]
    result = ReanalysisSource(path, row["attempt_id"]).run(
        _flow(tmp_path / "new flow.py"), archive_dir=path.parent,
    )
    assert result.archive_error is None and result.complete
    assert result.archive_path != path and result.snapshot["attempt_id"] != row["attempt_id"]
    assert _fingerprint(path) == original_files
    assert _saved(path, row) == before
    assert job.to_snapshot_dict(include_details=True) == row


def test_reanalysis_replays_full_log_and_parser_line_numbers(tmp_path, process_archive):
    path, _, row, lines = process_archive
    expected = list(enumerate(lines, 1))
    assert _log(path, row) == expected
    flow = _flow(tmp_path / "digest flow.py", """\
from hashlib import sha256
def fingerprint(line, ctx):
    return {"hash": sha256(line.encode()).hexdigest(), "size": len(line),
            "number": ctx.source["evidence"]["line_no"]}
flow = Flow("digest", parsers=[Parser("fingerprint", fingerprint)])
""")
    result = ReanalysisSource(path, row["attempt_id"]).run(flow, archive_dir=tmp_path / "replay")
    assert result.archive_error is None and result.complete
    assert result.snapshot["flow"]["error_count"] == 0
    assert result.snapshot["total_lines"] == row["total_lines"] == len(expected)
    assert _log(result.archive_path, result.snapshot) == expected
    assert _parser_rows(result.archive_path, result.snapshot, "fingerprint") == [
        (number, {"hash": sha256(line.encode()).hexdigest(), "size": len(line), "number": number})
        for number, line in expected
    ]


def test_reanalysis_provenance_uses_archive_identity_and_selected_attempt(tmp_path, process_archive):
    path, _, row, _ = process_archive
    renamed = path.rename(path.with_name("copied source name.sqlite3"))
    with closing(RunArchive(renamed)) as source:
        archive_id = source.run_id
    assert archive_id != renamed.stem
    definitions = tmp_path / "flow definitions"
    definitions.mkdir()
    definition = _flow(definitions / "選定.py")
    result = ReanalysisSource(renamed, row["attempt_id"]).run(
        definitions / ".." / definitions.name / definition.name, archive_dir=tmp_path / "provenance",
    )
    assert result.archive_error is None and result.complete
    saved = _saved(result.archive_path, result.snapshot)
    assert saved["verified"]
    for snapshot in (result.snapshot, saved):
        assert snapshot["source_kind"] == "reanalysis"
        descriptor = snapshot["source_descriptor"]
        assert descriptor["version"] == 1
        assert descriptor["archive_path"] == str(renamed)
        assert descriptor["archive_run_id"] == archive_id
        assert descriptor["source_attempt_id"] == row["attempt_id"]
        assert descriptor["flow_path"] == str(definition.resolve())
        assert descriptor["completion_reason"] == snapshot["completion_reason"] == "replayed"


@pytest.fixture
def make_source(tmp_path):
    def make(kind):
        log = tmp_path / "capture.log"
        log.write_bytes(b"ready|49\r\nnext|51\r\n")
        if kind == "file":
            return FileSource(log, chunk_size=3), Flow("file capture")
        if kind == "tail":
            return TailSource(log, chunk_size=3, final_offset=log.stat().st_size), Flow("tail capture")
        original = FileSource(log).run(Flow("seed"), archive_dir=tmp_path / "seed")
        assert original.complete and original.archive_error is None
        return (ReanalysisSource(original.archive_path, original.snapshot["attempt_id"]),
                _flow(tmp_path / "replay.py"))
    return make


@pytest.mark.parametrize("kind", ["process", "tail", "file", "reanalysis"])
def test_snapshot_capability_matrix(tmp_path, kind, make_source):
    out = tmp_path / "matrix"
    if kind == "process":
        with closing(WaveWorkspace(JobManager(), archive_dir=out)) as manager:
            job = manager.add(Job("matrix", [sys.executable, "-c", "print(13 * 17)"], pty=False))
            pending = manager.snapshot()["jobs"][0]
            assert pending["capabilities"] == {name: name != "can_rerun" for name in CAPABILITIES}
            manager.start()
            assert manager._drain(10)
            path, final = manager.archive.path, job.to_snapshot_dict()
            assert final["returncode"] == 0
    else:
        source, flow = make_source(kind)
        result = source.run(flow, archive_dir=out)
        assert result.archive_error is None and result.complete
        path, final = result.archive_path, result.snapshot
    saved = _saved(path, final)
    assert saved["verified"] and saved["finalized"]
    for row in (final, saved):
        assert row["source_kind"] == kind
        assert row["capabilities"] == dict.fromkeys(CAPABILITIES, kind == "process")


def _invoke(manager, row, action):
    job_id, _ = _identity(row)
    try:
        if action == "priority":
            return manager.set_priority(job_id, row["priority"] + 17, **_guard(row))
        if action == "rerun":
            return manager.rerun(job_id, **_guard(row))
        return manager.control(job_id, action, text="ready|53", **_guard(row))
    except (ValueError, RuntimeError) as exc:
        return exc  # The requested API contract specifically requires a receipt.


@pytest.mark.parametrize("kind", ["tail", "file", "reanalysis"])
@pytest.mark.parametrize("action", ["stop", "send", "interrupt", "priority", "rerun"])
def test_nonprocess_control_requires_rejected_receipt(tmp_path, monkeypatch, make_source, kind, action):
    source, flow = make_source(kind)
    live, release = Event(), Event()
    observed = {}
    add, close = JobManager.add, JobManager.close

    def exercise(manager, job):
        row = job.to_snapshot_dict()
        reply = _invoke(manager, row, action)
        after = job.to_snapshot_dict()
        observed.update(before=row, reply=reply, after=after)

    def observe_add(manager, job):
        added = add(manager, job)
        if action == "priority":
            # A queued Run isolates capability rejection from the scheduler's state guard.
            exercise(manager, job)
        elif action in ("stop", "send", "interrupt"):
            def hold_first_line(record):
                if record["line_no"] == 1:
                    live.set()
                    release.wait(10)
            manager.add_log_listener(hold_first_line)
            observed.update(manager=manager, job=job)
        return added

    def observe_close(manager):
        try:
            if action == "rerun":
                job, = manager.jobs
                # A finalized Run on an open manager passes the usual rerun state guards.
                exercise(manager, job)
        finally:
            close(manager)

    monkeypatch.setattr(JobManager, "add", observe_add)
    monkeypatch.setattr(JobManager, "close", observe_close)
    out = tmp_path / "control"
    if action in ("stop", "send", "interrupt"):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(source.run, flow, archive_dir=out)
            try:
                assert live.wait(8), "first archived line never reached the live Run"
                exercise(observed["manager"], observed["job"])
            finally:
                release.set()
            future.result(timeout=12)
    else:
        source.run(flow, archive_dir=out)

    before, reply, after = (observed[key] for key in ("before", "reply", "after"))
    assert before["source_kind"] == kind
    assert before["capabilities"] == dict.fromkeys(CAPABILITIES, False)
    assert before["status"] == {"priority": "PENDING", "rerun": "DONE"}.get(action, "RUNNING")
    assert before["finalized"] == (action == "rerun")
    assert isinstance(reply, ControlReceipt) and reply.status == "rejected", (
        f"{kind}/{action}: got {reply!r}; "
        f"termination entries {len(before['termination'])} -> {len(after['termination'])}, "
        f"priority {before['priority']} -> {after['priority']}, "
        f"attempt changed: {before['attempt_id'] != after['attempt_id']}"
    )
    for key in ("attempt_id", "submission_id", "priority", "termination",
                "interrupt_requested", "total_lines"):
        assert before[key] == after[key], key


def test_pending_process_rerun_requires_rejected_receipt():
    with closing(WaveWorkspace(JobManager())) as manager:
        job = manager.add(Job("queued", [sys.executable, "-c", "print(1337)"], pty=False))
        row = job.to_snapshot_dict()
        assert not row["capabilities"]["can_rerun"]
        reply = _invoke(manager, row, "rerun")
        assert isinstance(reply, ControlReceipt) and reply.status == "rejected", repr(reply)


@pytest.mark.parametrize(
    "case,code,diagnostic",
    [
        ("unclean", 2, "unverified_source"),
        ("missing_attempt", 2, "Attempt (attempt_id) not found"),
        ("syntax_error", 2, "SyntaxError"),
        ("load_exception", 2, "RuntimeError: broken calibration during load"),
        ("parser_exception", 2, "bad replay calibration: 67"),
    ],
)
def test_reanalysis_cli_failure_is_not_archived_as_success(tmp_path, capsys, case, code, diagnostic):
    log = tmp_path / "probe.log"
    log.write_bytes(b"reading=67\r\nreading=71\n")
    original = FileSource(log).run(Flow("probe"), archive_dir=tmp_path / "source")
    assert original.complete and original.archive_error is None
    path = original.archive_path
    if case == "unclean":
        # Model the missing close marker with committed snapshots and Log intact.
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("UPDATE metadata SET value='false' WHERE key='closed_cleanly'")
    original_row = _saved(path, original.snapshot)
    assert original_row["verified"] == (case != "unclean")
    before = _fingerprint(path)
    definition = _flow(tmp_path / "calibration.py", {
        "syntax_error": "flow = Flow('calibration',\n",
        "load_exception": "raise RuntimeError('broken calibration during load')\n",
        "parser_exception": """\
def parse(line, ctx):
    raise ValueError("bad replay calibration: " + line.split("=")[1])
flow = Flow("calibration", parsers=[Parser("calibrate", parse)])
""",
    }.get(case, "flow = Flow('calibration', result=lambda _: 'PASS')\n"))
    run = "absent-" + original.snapshot["attempt_id"] if case == "missing_attempt" else original.snapshot["attempt_id"]
    directory = tmp_path / "cli-replay"
    rc = main(["reanalyze", str(path), "--headless", "--run", run, "--flow", str(definition),
               "--archive-dir", str(directory)])
    captured = capsys.readouterr()
    paths = list(directory.glob("*.sqlite3"))
    assert _fingerprint(path) == before and _saved(path, original.snapshot) == original_row
    assert rc == code, captured
    if case in ("unclean", "parser_exception"):
        archive_path, = paths
        with closing(RunArchive(archive_path)) as archive:
            row, = archive.attempts()["rows"]
            saved = archive.attempt(*_identity(row))
            assert archive.health["closed_cleanly"] and saved["verified"]
        assert saved["result"] == ("INCOMPLETE" if case == "unclean" else "ERROR")
        assert str(archive_path) in captured.out
        if case == "unclean":
            assert not saved["flow"]["complete"]
            assert any("Source archive did not close cleanly" in item["message"]
                       for item in saved["diagnostics"])
            assert _log(archive_path, saved) == _log(path, original_row)
            assert diagnostic in captured.out
        else:
            assert any(diagnostic in parser.get("error", "") for parser in saved["flow"]["parsers"])
            assert diagnostic in captured.err
    else:
        assert not paths and "Archive:" not in captured.out
        assert diagnostic in captured.err


def test_two_reanalyses_keep_independent_archives_and_flow_outputs(tmp_path, make_source):
    source, flow = make_source("file")
    directory = tmp_path / "all runs"
    original = source.run(flow, archive_dir=directory)
    assert original.complete and original.archive_error is None
    replay = ReanalysisSource(original.archive_path, original.snapshot["attempt_id"])
    definitions = [
        _flow(tmp_path / f"scale {scale}.py",
              "def parse(line, ctx):\n"
              f"    return {{'score': int(line.split('|')[1]) * ({scale})}}\n"
              f"flow = Flow('scale {scale}', parsers=[Parser('score', parse)])\n")
        for scale in (3, -2)
    ]
    left = replay.run(definitions[0], archive_dir=directory)
    assert left.complete and left.archive_error is None
    left_files = _fingerprint(left.archive_path)
    left_saved = _saved(left.archive_path, left.snapshot)
    left_rows = _parser_rows(left.archive_path, left.snapshot, "score")
    right = replay.run(definitions[1], archive_dir=directory)
    assert right.complete and right.archive_error is None
    assert len({original.archive_path, left.archive_path, right.archive_path}) == 3
    assert len({item.snapshot["attempt_id"] for item in (original, left, right)}) == 3
    assert _fingerprint(left.archive_path) == left_files
    assert _saved(left.archive_path, left.snapshot) == left_saved
    assert _parser_rows(left.archive_path, left.snapshot, "score") == left_rows == [
        (1, {"score": 147}), (2, {"score": 153}),
    ]
    assert _parser_rows(right.archive_path, right.snapshot, "score") == [
        (1, {"score": -98}), (2, {"score": -102}),
    ]
    for result, definition in zip((left, right), definitions):
        saved = _saved(result.archive_path, result.snapshot)
        assert saved["verified"] and saved["flow"]["complete"]
        assert saved["source_descriptor"]["flow_path"] == str(definition)
        assert saved["source_descriptor"]["source_attempt_id"] == original.snapshot["attempt_id"]
        assert _log(result.archive_path, saved) == _log(original.archive_path, original.snapshot)
