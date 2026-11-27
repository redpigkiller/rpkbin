"""Read file-source archives through the existing CLI and Wave HTTP routes."""

from contextlib import closing
from threading import Event

import pytest

from rpkbin.flow import Flow
from rpkbin.wave import FileSource, RunArchive, WaveWorkspace
from rpkbin.wave.cli import main
from rpkbin.wave.web import WaveServer
from test_archive_api import archive
from test_wave import request, state


def _source_args(mode, target):
    command = "reanalyze" if mode == "reanalyze" else "logs"
    return [command, str(target), "--headless", *(["--once"] if mode == "file" else [])]


def test_logs_replaces_legacy_help_and_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as help_exit:
        main(["--help"])
    assert help_exit.value.code == 0
    help_text = capsys.readouterr().out
    assert "rpk-wave logs" in help_text and "rpk-wave reanalyze" in help_text
    assert "watch" not in help_text and "analyze" not in help_text.replace("reanalyze", "")
    for retired in ("watch", "analyze"):
        assert main([retired]) == 2
        assert "Work file not found" in capsys.readouterr().err


@pytest.mark.parametrize("option", [
    ["--start", "beginning"], ["--poll-interval", "0.1"], ["--commit-interval", "1.0"],
    ["--rotation-grace", "3.0"], ["--idle-timeout", "0"], ["--completion-file", "done"],
    ["--end-marker", "END"], ["--final-offset", "0"],
])
def test_once_rejects_explicit_tail_options(tmp_path, option):
    with pytest.raises(SystemExit) as exc:
        main(["logs", str(tmp_path), "--once", *option])
    assert exc.value.code == 2


@pytest.mark.parametrize("option", ["--completion-file", "--final-offset"])
@pytest.mark.parametrize("multiple", [False, True])
def test_file_completion_options_require_one_explicit_file(tmp_path, option, multiple):
    targets = [str(tmp_path / "a.log"), str(tmp_path / "b.log")] if multiple else [str(tmp_path)]
    with pytest.raises(SystemExit) as exc:
        main(["logs", *targets, option, "0"])
    assert exc.value.code == 2


@pytest.mark.parametrize("command", ["logs", "reanalyze"])
@pytest.mark.parametrize("opts", [["--timeout", "1"], ["--headless", "--timeout", "nan"],
                                  ["--headless", "--timeout", "0"], ["--port", "65536"]])
def test_source_cli_rejects_invalid_launch_args(command, tmp_path, opts):
    with pytest.raises(SystemExit) as exc:
        main([command, str(tmp_path), "--flow", "flow.py", *opts])
    assert exc.value.code == 2


def test_logs_once_mixed_recursive_targets_and_multiple_globs(tmp_path, capsys):
    root = tmp_path / "tree"
    (root / "sub").mkdir(parents=True)
    explicit = tmp_path / "explicit.txt"
    for path in (explicit, root / "a.log", root / "sub" / "b.trace", root / "ignored.txt"):
        path.write_text(path.name + "\n", encoding="utf8")
    destination = tmp_path / "out"
    assert main(["logs", str(explicit), str(root), "--once", "-r", "--headless",
                 "--glob", "*.log", "--glob", "*.trace", "--archive-dir", str(destination)]) == 0
    output = capsys.readouterr().out
    assert "External (file)" in output and "eof" in output and "[Wave] Archive:" in output
    path, = destination.glob("*.sqlite3")
    with closing(RunArchive(path)) as store:
        rows = store.attempts()["rows"]
        assert store.metadata["source_path"] is None
        assert {row["name"] for row in rows} == {"explicit.txt", "a.log", "sub/b.trace"}
        assert all(row["verified"] and row["result"] == "N/A" for row in rows)


def test_logs_rejects_empty_once_and_bad_flow_before_validating_target(tmp_path, monkeypatch, capsys):
    from rpkbin.wave import web

    monkeypatch.setattr(web, "launch", lambda *_a, **_kw: pytest.fail("started an empty preview"))
    assert main(["logs", str(tmp_path), "--once"]) == 2
    assert "No matching" in capsys.readouterr().err
    assert main(["logs", str(tmp_path / "missing"), "--once", "--flow", "missing.py"]) == 2
    assert "--flow must name" in capsys.readouterr().err


@pytest.mark.parametrize("interrupted", [False, True])
def test_logs_empty_live_directory_timeout_or_interrupt_stops_and_archives(
    tmp_path, monkeypatch, capsys, interrupted,
):
    root = tmp_path / "incoming"
    root.mkdir()
    destination = tmp_path / "out"
    drain = WaveWorkspace._drain
    stopped = Event()
    seen = Event()
    stop_sources = WaveWorkspace.stop_sources

    def observe_stop(workspace, job_id=None):
        stopped.set()
        return stop_sources(workspace, job_id)

    def produce_then_drain(workspace, timeout=None):
        if timeout is not None:
            assert not workspace.jobs
            workspace.add_log_listener(lambda record: seen.set() if record["text"] == "first line" else None)
            (root / "new.log").write_bytes(b"first line\n")
            assert seen.wait(5)
            if interrupted:
                raise KeyboardInterrupt
        return drain(workspace, timeout)

    monkeypatch.setattr(WaveWorkspace, "_drain", produce_then_drain)
    monkeypatch.setattr(WaveWorkspace, "stop_sources", observe_stop)
    assert main(["logs", str(root), "--headless", "--timeout", "0.05", "--start", "end",
                 "--poll-interval", "0.01", "--archive-dir", str(destination)]) == (130 if interrupted else 1)
    assert stopped.is_set()
    assert "first line" in capsys.readouterr().out
    path, = destination.glob("*.sqlite3")
    with closing(RunArchive(path)) as store:
        row, = store.attempts()["rows"]
        assert row["completion_reason"] == "user_stopped"
        assert row["verified"] and store.health["closed_cleanly"]


def test_logs_headless_handles_interrupt_while_waiting_without_timeout(tmp_path):
    import subprocess
    import sys

    root = tmp_path / "empty"
    root.mkdir()
    out = tmp_path / "out"
    code = """
import _thread
import sys
from threading import Timer
from rpkbin.wave import WaveWorkspace
from rpkbin.wave.cli import main

drain = WaveWorkspace._drain
def interrupt_while_waiting(workspace, timeout=None):
    if not any(task.stop.is_set() for task in workspace._watchers):
        timer = Timer(.2, _thread.interrupt_main)
        timer.daemon = True
        timer.start()
    return drain(workspace, timeout)
WaveWorkspace._drain = interrupt_while_waiting
raise SystemExit(main(sys.argv[1:]))
"""
    result = subprocess.run([sys.executable, "-B", "-c", code, "logs", str(root),
                             "--headless", "--archive-dir", str(out)],
                            capture_output=True, timeout=8)
    assert result.returncode == 130, (result.stdout, result.stderr)
    path, = out.glob("*.sqlite3")
    with closing(RunArchive(path)) as store:
        assert store.health["closed_cleanly"] and not store.attempts()["rows"]


def test_logs_interrupt_before_dispatch_closes_pending_sources(tmp_path, monkeypatch):
    def interrupt(workspace):
        raise KeyboardInterrupt

    def unexpected_drain(workspace, timeout=None):
        pytest.fail("Queued source attempts must be finalized before waiting for drain")

    monkeypatch.setattr(WaveWorkspace, "start", interrupt)
    monkeypatch.setattr(WaveWorkspace, "_drain", unexpected_drain)
    log = tmp_path / "input.log"
    log.write_bytes(b"hello\n")
    out = tmp_path / "out"
    assert main(["logs", str(log), "--headless", "--archive-dir", str(out)]) == 130
    path, = out.glob("*.sqlite3")
    with closing(RunArchive(path)) as store:
        row, = store.attempts()["rows"]
        assert row["finalized"] and row["verified"] and row["total_lines"] == 0


def test_logs_interrupt_during_reader_launch_closes_archive(tmp_path, monkeypatch):
    import _thread
    from threading import Thread

    launch = Thread.start

    def interrupt_after_launch(thread):
        launch(thread)
        if thread.name.startswith("Wave-source-"):
            _thread.interrupt_main()

    monkeypatch.setattr(Thread, "start", interrupt_after_launch)
    log = tmp_path / "input.log"
    log.write_bytes(b"hello\n")
    out = tmp_path / "out"
    assert main(["logs", str(log), "--headless", "--archive-dir", str(out)]) == 130
    path, = out.glob("*.sqlite3")
    with closing(RunArchive(path)) as store:
        row, = store.attempts()["rows"]
        assert row["finalized"] and row["verified"]


@pytest.mark.parametrize("scenario", ["logs", "empty_live", "reanalyze"])
def test_source_cli_default_gui_preview_starts_through_http(tmp_path, monkeypatch, scenario):
    from rpkbin.wave import web

    log = tmp_path / "one.log"
    definition = tmp_path / "flow.py"
    definition.write_text("from rpkbin.flow import Flow\nflow = Flow('replay')\n", encoding="utf8")
    if scenario != "empty_live":
        log.write_bytes(b"hello\n")
    target = log
    if scenario == "reanalyze":
        target = FileSource(log).run(archive_dir=tmp_path).archive_path
        args = ["reanalyze", str(target), "--flow", str(definition)]
        label = "External (replay)"
    elif scenario == "logs":
        args = ["logs", str(target), "--once"]
        label = "External (file)"
    else:
        target = tmp_path
        args = ["logs", str(target), "--poll-interval", "0.01"]
        label = "External (tail)"

    archived = tmp_path / "out"

    def launch(workspace, **kwargs):
        assert kwargs == dict(host="localhost", port=0, open_browser=False, block=True, source_path=target)
        server = WaveServer(workspace, host=kwargs["host"], source_path=kwargs["source_path"])
        base = server.start()
        try:
            preview = state(base)
            assert not preview["started"] and preview["ready"]
            assert len(preview["jobs"]) == (scenario != "empty_live")
            assert all(row["status"] == "PENDING" and row["origin_label"] == label
                       for row in preview["jobs"])
            status, body = request(base, "/api/manager/action",
                                   {"workspace_id": preview["workspace_id"], "action": "start"})
            assert status == 200 and body["ok"]
            if scenario == "empty_live":
                from test_workspace_sources import _wait_for

                log.write_bytes(b"hello\n")
                _wait_for(lambda: workspace.jobs and workspace.jobs[0].to_snapshot_dict()["total_lines"] == 1)
                workspace.stop_sources()
            assert workspace._drain(5)
            job, = state(base)["jobs"]
            assert job["origin_label"] == label and job["total_lines"] == 1
        finally:
            server.stop()

    monkeypatch.setattr(web, "launch", launch)
    assert main([*args, "--no-browser", "--host", "localhost", "--archive-dir", str(archived)]) == 0
    path, = archived.glob("*.sqlite3")
    with closing(RunArchive(path)) as store:
        assert store.health["closed_cleanly"] and store.metadata["source_path"] == str(target)


def test_closing_logs_preview_does_not_invoke_flow_callbacks(tmp_path, monkeypatch):
    from rpkbin.wave import web

    log = tmp_path / "input.log"
    log.write_bytes(b"hello\n")
    marker = tmp_path / "flow-finished"
    definition = tmp_path / "flow.py"
    definition.write_text(
        "from pathlib import Path\n"
        "from rpkbin.flow import Analyzer, Flow\n"
        f"def finished(end, ctx): Path({str(marker)!r}).touch()\n"
        "flow = Flow('preview', analyzers=[Analyzer('test', on_finish=finished)])\n",
        encoding="utf8",
    )
    monkeypatch.setattr(web, "launch", lambda workspace, **kwargs: workspace.close())
    out = tmp_path / "out"
    assert main(["logs", str(log), "--flow", str(definition), "--archive-dir", str(out)]) == 0
    assert not marker.exists()
    path, = out.glob("*.sqlite3")
    with closing(RunArchive(path)) as store:
        row, = store.attempts()["rows"]
        assert row["finalized"] and row["verified"] and row["total_lines"] == 0


def test_source_gui_shuts_http_listener_when_archive_close_fails(tmp_path, monkeypatch, capsys):
    import sqlite3
    from types import SimpleNamespace

    from rpkbin.wave import web

    def close_preview(_seconds):
        raise KeyboardInterrupt

    close = RunArchive.close

    def fail_close(store, **options):
        close(store, **options)
        if store._writer is not None:
            raise sqlite3.OperationalError("injected archive close failure")

    stopped = Event()
    stop = WaveServer.stop

    def observe_stop(server):
        stop(server)
        stopped.set()

    monkeypatch.setattr(web, "time", SimpleNamespace(sleep=close_preview))
    monkeypatch.setattr(RunArchive, "close", fail_close)
    monkeypatch.setattr(WaveServer, "stop", observe_stop)
    log = tmp_path / "input.log"
    log.write_bytes(b"hello\n")
    assert main(["logs", str(log), "--once", "--no-browser", "--archive-dir", str(tmp_path / "out")]) == 2
    output = capsys.readouterr()
    assert "injected archive close failure" in output.err and "Archive:" not in output.out
    assert stopped.is_set()


def test_reanalysis_batch_pages_and_uses_latest_finalized_attempt(tmp_path, monkeypatch):
    from rpkbin.jobcore import Job
    import sys

    first = tmp_path / "a.log"
    first.write_bytes(b"file log\n")
    original = WaveWorkspace(archive_dir=tmp_path)
    file_job = original.add_source(FileSource(first))
    process = original.add(Job("child", [sys.executable, "-c", "print('process log')"], pty=False))
    original.start()
    assert original._drain(5)
    finalized_id = process.attempt_id
    original.pause()
    assert original.rerun(process.id).status == "accepted"
    # Preserve a newer pending row as it would appear in a preview archive.
    original.archive.flush()
    original.archive.close()
    original.close()
    pages = []
    attempts = RunArchive.attempts

    def page(store, *args, **kwargs):
        pages.append(kwargs.get("before"))
        return attempts(store, *args, **(kwargs | {"limit": 1}))

    monkeypatch.setattr(RunArchive, "attempts", page)
    definition = tmp_path / "flow.py"
    definition.write_text("from rpkbin.flow import Flow\nflow = Flow('replayed')\n", encoding="utf8")
    out = tmp_path / "batch"
    args = ["reanalyze", str(original.archive.path), "--flow", str(definition), "--headless"]
    assert main([*args, "--archive-dir", str(out)]) == 0
    assert len(pages) == 3 and pages[0] is None and all(pages[1:])
    path, = out.glob("*.sqlite3")
    with closing(RunArchive(path)) as store:
        rows = attempts(store)["rows"]
        assert {row["name"] for row in rows} == {"a.log", "child"}
        for row in rows:
            saved = store.attempt(row["id"], row["attempt_id"])
            expected = finalized_id if row["name"] == "child" else file_job.attempt_id
            assert saved["source_descriptor"]["source_attempt_id"] == expected
    single = tmp_path / "single"
    assert main([*args, "--run", file_job.attempt_id, "--archive-dir", str(single)]) == 0
    path, = single.glob("*.sqlite3")
    with closing(RunArchive(path)) as store:
        row, = attempts(store)["rows"]
        assert row["name"] == "a.log"


@pytest.mark.parametrize("mode,kind", [("file", "file"), ("tail", "tail"), ("reanalyze", "reanalysis")])
def test_cli_file_source_archive_opens_in_existing_wave_view(tmp_path, capsys, mode, kind):
    source = tmp_path / "input.log"
    source.write_bytes(b"sample\n")
    definition = tmp_path / "flow.py"
    definition.write_text(
        "from rpkbin.flow import Flow, Parser\n"
        "flow = Flow('samples', parsers=[Parser('capture', lambda line, ctx: {'value': line})])\n",
        encoding="utf-8",
    )
    directory = tmp_path / "archives"
    args = _source_args(mode, source) + ["--flow", str(definition), "--archive-dir", str(directory)]
    if mode == "tail":
        args += ["--final-offset", str(source.stat().st_size)]
    if mode == "reanalyze":
        original = FileSource(source).run(Flow("original"), archive_dir=tmp_path)
        args[1] = str(original.archive_path)
        args += ["--run", original.snapshot["attempt_id"]]
    assert main(args) == 0
    printed = capsys.readouterr().out
    paths = list(directory.glob("*.sqlite3"))
    assert len(paths) == 1 and str(paths[0]) in printed
    server = WaveServer(archive_path=paths[0])
    server.start()
    try:
        snapshot = state(server.url.rstrip("/"))
        assert snapshot["opened_run_id"] == RunArchive(paths[0]).run_id
        status, listing = archive(server, view="attempts")
        assert status == 200
        row, = listing["rows"]
        assert row["source_kind"] == kind
        assert row["verified"] and not any(row["capabilities"].values())
        target = dict(job_id=row["id"], attempt_id=row["attempt_id"])
        status, detail = archive(server, view="attempt", **target)
        assert status == 200
        attempt = detail["attempt"]
        assert attempt["finalized"] and attempt["flow"]["complete"]
        assert attempt["source_descriptor"]["path"] == args[1]
        assert attempt["completion_reason"] == {
            "file": "eof", "tail": "final_offset", "reanalyze": "replayed",
        }[mode]
        status, logs = archive(server, view="logs", **target)
        assert status == 200 and [item["text"] for item in logs["rows"]] == ["sample"]
    finally:
        server.stop()
        server.workspace.close()


@pytest.mark.parametrize("case,expected", [
    ("complete", 0), ("incomplete", 1), ("missing", 2), ("flow_error", 2), ("fail", 3),
])
def test_source_cli_distinguishes_completion_and_errors(tmp_path, capsys, case, expected):
    source = tmp_path / "input.log"
    if case != "missing":
        source.write_bytes(b"sample\n")
    definition = tmp_path / "flow.py"
    body = {
        "flow_error": "def parse(line, ctx): raise ValueError('analysis failed')\n"
                      "flow = Flow('test', parsers=[Parser('bad', parse)])\n",
        "fail": "flow = Flow('test', result=lambda _: 'FAIL')\n",
    }.get(case, "flow = Flow('test')\n")
    definition.write_text("from rpkbin.flow import Flow, Parser\n" + body, encoding="utf-8")
    mode = "tail" if case == "incomplete" else "file"
    args = _source_args(mode, source) + ["--flow", str(definition), "--archive-dir", str(tmp_path)]
    if mode == "tail":
        args += ["--idle-timeout", "0"]
    assert main(args) == expected
    if expected == 2:
        assert capsys.readouterr().err


def test_source_cli_reports_failed_archive_write(tmp_path, capsys, monkeypatch):
    import sqlite3
    from rpkbin.wave import RunArchive

    original = RunArchive._append_source_batch

    def fail(store, *args):
        store._fail(sqlite3.OperationalError("database or disk is full"))
        return original(store, *args)

    monkeypatch.setattr(RunArchive, "_append_source_batch", fail)
    source, definition = tmp_path / "log", tmp_path / "flow.py"
    source.write_bytes(b"line\n")
    definition.write_text("from rpkbin.flow import Flow\nflow = Flow('test')\n", encoding="utf-8")
    assert main(_source_args("file", source) + ["--flow", str(definition), "--archive-dir", str(tmp_path)]) == 2
    output = capsys.readouterr()
    assert "database or disk is full" in output.err and "Archive:" not in output.out


def test_source_api_reports_failure_closing_archive(tmp_path, monkeypatch):
    import sqlite3
    from rpkbin.flow import Flow
    from rpkbin.wave import FileSource, RunArchive

    close = RunArchive.close

    def fail_close(store, **kwargs):
        close(store, **kwargs)
        raise sqlite3.OperationalError("close failed")

    monkeypatch.setattr(RunArchive, "close", fail_close)
    source = tmp_path / "log"
    source.write_bytes(b"line\n")
    result = FileSource(source).run(Flow("test"), archive_dir=tmp_path)
    assert not result.complete and result.reason == "archive_error"
    assert "close failed" in result.archive_error


def test_watch_archive_exposes_committed_log_and_flow_before_close(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from rpkbin.wave import RunArchive

    source, definition = tmp_path / "input.log", tmp_path / "flow.py"
    directory = tmp_path / "archives"
    initial = b"first\n" * 256
    source.write_bytes(initial)
    definition.write_text(
        "from rpkbin.flow import Flow, Parser\n"
        "flow = Flow('watch', parsers=[Parser('capture', lambda line, ctx: {'line': line})])\n",
        encoding="utf-8",
    )
    committed = Event()
    flush = RunArchive.flush

    def signal_commit(store):
        pending = store._pending
        flush(store)
        if pending and not store._record_depth and store._pending == 0 and not store.error:
            committed.set()

    monkeypatch.setattr(RunArchive, "flush", signal_commit)
    args = ["logs", str(source), "--headless", "--flow", str(definition), "--archive-dir", str(directory),
            "--final-offset", str(len(initial) + len(b"second\n")), "--idle-timeout", "10"]
    with ThreadPoolExecutor() as pool:
        run = pool.submit(main, args)
        try:
            assert committed.wait(3)
            path, = directory.glob("*.sqlite3")
            server = WaveServer(archive_path=path)
            server.start()
            try:
                status, listing = archive(server, view="attempts")
                assert status == 200
                row, = listing["rows"]
                assert row["verified"] is False
                target = dict(job_id=row["id"], attempt_id=row["attempt_id"])
                status, logs = archive(server, view="logs", limit=1, **target)
                assert status == 200 and [row["text"] for row in logs["rows"]] == ["first"]
                status, parsed = archive(server, view="flow", kind="record", limit=1, **target)
                assert status == 200 and [row["data"]["line"] for row in parsed["rows"]] == ["first"]
                assert not run.done()
                with source.open("ab") as log:
                    log.write(b"second\n")
                assert run.result(timeout=4) == 0
                status, detail = archive(server, view="attempt", **target)
                assert status == 200 and detail["attempt"]["verified"] and detail["health"]["closed_cleanly"]
                status, tail = archive(server, view="logs", direction="backward", limit=1, **target)
                assert status == 200 and [row["text"] for row in tail["rows"]] == ["second"]
            finally:
                server.stop()
                server.workspace.close()
        finally:
            if not run.done():
                with source.open("ab") as log:
                    log.write(b"second\n")


@pytest.mark.parametrize("case,expected", [("incomplete", 1), ("unknown_run", 2), ("fail", 3)])
def test_reanalysis_cli_preserves_source_exit_codes(tmp_path, case, expected):
    from rpkbin.wave import TailSource

    log, definition = tmp_path / "input.log", tmp_path / "flow.py"
    log.write_bytes(b"sample\n")
    source = TailSource(log, idle_timeout=0) if case == "incomplete" else FileSource(log)
    original = source.run(Flow("original"), archive_dir=tmp_path)
    expression = "Flow('replay', result=lambda _: 'FAIL')" if case == "fail" else "Flow('replay')"
    definition.write_text("from rpkbin.flow import Flow\nflow = " + expression + "\n", encoding="utf-8")
    run = "unknown" if case == "unknown_run" else original.snapshot["attempt_id"]
    assert main(["reanalyze", str(original.archive_path), "--headless", "--run", run,
                 "--flow", str(definition), "--archive-dir", str(tmp_path)]) == expected


@pytest.mark.parametrize("mode", ["reanalyze", "file", "tail"])
@pytest.mark.parametrize("stage", ["parser", "analyzer"])
def test_source_cli_reports_callback_exception(tmp_path, capsys, mode, stage):
    log, definition = tmp_path / "input.log", tmp_path / "flow.py"
    log.write_bytes(b"sample\n")
    callback = {
        "parser": "parsers=[Parser('broken', broken)]",
        "analyzer": "analyzers=[Analyzer('broken', on_finish=broken)]",
    }[stage]
    definition.write_text(
        "from rpkbin.flow import Flow, Parser, Analyzer\n"
        "def broken(*args): raise LookupError('invalid calibration: 67')\n"
        f"flow = Flow('samples', {callback})\n",
        encoding="utf-8",
    )
    args = _source_args(mode, log) + ["--flow", str(definition), "--archive-dir", str(tmp_path / "out")]
    if mode == "tail":
        args += ["--final-offset", str(log.stat().st_size)]
    if mode == "reanalyze":
        original = FileSource(log).run(Flow("original"), archive_dir=tmp_path)
        args[1] = str(original.archive_path)
        args += ["--run", original.snapshot["attempt_id"]]
    assert main(args) == 2
    output = capsys.readouterr()
    assert "ERROR" in output.out
    assert "LookupError: invalid calibration: 67" in output.err


def test_watch_commits_low_volume_with_incremental_http_cursors(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from rpkbin.wave import RunArchive

    log = tmp_path / "tail.log"
    log.write_bytes(b"one\n")
    definition = tmp_path / "flow.py"
    definition.write_text(
        "from rpkbin.flow import Flow, Parser\n"
        "flow = Flow('tail', parsers=[Parser('value', lambda line, ctx: {'line': line})])\n",
        encoding="utf-8",
    )
    commits = [Event(), Event()]
    flush = RunArchive.flush

    def observe(store):
        flush(store)
        if store._writer is None or store.error or store._record_depth:
            return
        count, = store._writer.execute("SELECT count(*) FROM records").fetchone()
        if count in (1, 2):
            commits[count - 1].set()

    monkeypatch.setattr(RunArchive, "flush", observe)
    directory = tmp_path / "archive"
    args = ["logs", str(log), "--headless", "--flow", str(definition), "--archive-dir", str(directory),
            "--commit-interval", "0.05", "--poll-interval", "0.02", "--idle-timeout", "8",
            "--final-offset", str(len(b"one\ntwo\nthree\n"))]

    def append(line):
        with log.open("ab") as stream:
            stream.write(line)

    with ThreadPoolExecutor() as pool:
        result = pool.submit(main, args)
        try:
            assert commits[0].wait(3)
            path, = directory.glob("*.sqlite3")
            server = WaveServer(archive_path=path)
            server.start()
            try:
                status, listing = archive(server, view="attempts")
                assert status == 200
                row, = listing["rows"]
                target = dict(job_id=row["id"], attempt_id=row["attempt_id"])
                cursor, after = None, 0
                for number, line in enumerate(("one", "two", "three"), 1):
                    status, detail = archive(server, view="attempt", **target)
                    assert status == 200 and detail["attempt"]["total_lines"] == number
                    if number < 3:
                        assert detail["health"]["writer_heartbeat"] > 0
                        assert not detail["attempt"]["finalized"] and not result.done()
                    else:
                        assert detail["attempt"]["finalized"] and detail["health"]["closed_cleanly"]
                    status, logs = archive(server, view="reader", **target,
                                            **({"cursor": cursor} if cursor else {}))
                    assert status == 200 and [(r["seq"], r["text"]) for r in logs["rows"]] == [(number, line)]
                    cursor = logs["next"]
                    status, flow = archive(server, view="flow", kind="record", after=after, **target)
                    assert status == 200 and [r["data"]["line"] for r in flow["rows"]] == [line]
                    after = flow["next_seq"]
                    if number == 1:
                        append(b"two\n")
                        assert commits[1].wait(3)
                    elif number == 2:
                        append(b"three\n")
                        assert result.result(timeout=4) == 0
            finally:
                server.stop()
                server.workspace.close()
        finally:
            if not result.done():
                append(b"two\nthree\n")
