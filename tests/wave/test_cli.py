"""CLI contracts: definition safety, outcome codes and output provenance."""

from rpkbin.wave import WaveWorkspace

from pathlib import Path
import os
import subprocess
import sys

import pytest

from rpkbin.wave.cli import load_work_file, main

ROOT = Path(__file__).resolve().parents[2]


def work_file(tmp_path, body):
    path = tmp_path / "work.py"
    path.write_text(
        "import sys\nfrom rpkbin.flow import Flow, Parser, Analyzer, Binding\nfrom rpkbin.jobcore import Job, JobManager\nfrom rpkbin.wave import WaveWorkspace\n"
        "mgr = WaveWorkspace(JobManager(max_workers=2))\n" + body,
        encoding="utf-8",
    )
    return path


def run_cli(path, *options):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from rpkbin.wave.cli import main; raise SystemExit(main())",
            str(path),
            "--headless",
            *options,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        encoding="utf-8",
    )


def test_load_only_preserves_cwd_and_does_not_dispatch(tmp_path):
    marker = tmp_path / "started"
    path = work_file(
        tmp_path,
        f"mgr.add(Job('safe', [sys.executable, '-c', {('from pathlib import Path; Path(' + repr(str(marker)) + ').touch()')!r}]))\n",
    )
    before = Path.cwd()
    manager = load_work_file(path)
    try:
        assert Path.cwd() == before
        assert not marker.exists()
        assert manager.jobs[0].status.value == "PENDING"
    finally:
        manager.close()


def test_work_file_cannot_start_during_definition(tmp_path):
    path = work_file(
        tmp_path,
        "mgr.add(Job('safe', [sys.executable, '-c', 'print(1)']))\nmgr.start()\n",
    )
    with pytest.raises(ValueError):
        load_work_file(path)


def test_aliases_are_allowed_but_ambiguous_managers_are_rejected(tmp_path):
    path = work_file(
        tmp_path,
        "mgr.add(Job('safe', [sys.executable, '-c', 'print(1)']))\nmanager = mgr\n",
    )
    manager = load_work_file(path)
    manager.close()
    path.write_text(path.read_text() + "manager = JobManager()\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unambiguous"):
        load_work_file(path)


def test_empty_missing_and_non_python_targets_are_errors(tmp_path):
    with pytest.raises(ValueError, match="no jobs"):
        load_work_file(work_file(tmp_path, ""))
    path = tmp_path / "work.json"
    path.write_text("{}")
    assert main([str(path), "--headless"]) == 2
    assert main([str(tmp_path / "missing.py"), "--headless"]) == 2


def test_python_system_exit_is_not_silent_success(tmp_path):
    path = work_file(tmp_path, "sys.exit(0)\n")
    assert main([str(path), "--headless"]) == 2


@pytest.mark.parametrize(
    "body, kind",
    [
        ("x =\n", "SyntaxError"),
        ("missing_function()\n", "NameError"),
        ("import wave_review_missing_module\n", "ModuleNotFoundError"),
    ],
)
def test_work_file_errors_identify_source_line(tmp_path, body, kind):
    path = tmp_path / "bad.py"
    path.write_text("# work definition\n" + body, encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_work_file(path)
    assert f"{path}:2" in str(exc.value)
    assert kind in str(exc.value)


def test_summary_distinguishes_absent_exit_code_from_zero(capsys):
    from rpkbin.jobcore import Job, JobManager
    from rpkbin.wave.cli import run_headless

    manager = WaveWorkspace(JobManager(max_workers=1))
    cancelled = manager.add(Job("cancelled", [sys.executable, "-c", "pass"]))
    manager.add(Job("zero", [sys.executable, "-c", "pass"]))
    manager.validate()
    cancelled.stop()
    assert run_headless(manager) == 1
    rows = capsys.readouterr().out.splitlines()
    assert next(row for row in rows if row.startswith("cancelled ")).split()[-1] == "—"
    assert next(row for row in rows if row.startswith("zero ")).split()[-1] == "0"


def test_failed_disk_log_is_visible_without_changing_pass(tmp_path, capsys):
    from rpkbin.flow import Flow, Analyzer
    from rpkbin.jobcore import Job, JobManager
    from rpkbin.wave.cli import run_headless

    blocker = tmp_path / "not_a_directory"
    blocker.write_text("block", encoding="utf-8")
    manager = WaveWorkspace(JobManager())
    job = Job(
            "save_failure",
            [
                sys.executable,
                "-u",
                "-c",
                "print('COMPLETE'); [print('data') for _ in range(120)]",
            ],
            log_file=blocker / "out.log",
            pty=False,
        )
    manager.add(job, flow=Flow("complete", analyzers=[Analyzer("completion", on_finish=lambda end, ctx: ctx.summary.result("PASS", final=True))]))
    assert run_headless(manager) == 0
    output = capsys.readouterr().out
    assert "log save failed:" in output and str(blocker / "out.log") in output
    snapshot = job.to_snapshot_dict()
    assert snapshot["result"] == "PASS"
    assert snapshot["warning_count"] == snapshot["error_count"] == 0
    assert snapshot["log_file_error"]["count"] == 121
    assert len([d for d in snapshot["diagnostics"] if d["source"] == "log_file"]) == 1


def test_unrepresentable_child_output_is_escaped_in_terminal(monkeypatch):
    import io

    from rpkbin.jobcore import Job, JobManager
    from rpkbin.wave.cli import run_headless

    manager = WaveWorkspace(JobManager())
    manager.add(
        Job(
            "unicode",
            [sys.executable, "-c", "print(chr(0x5b8c))"],
            env={"PYTHONIOENCODING": "utf-8"},
            pty=False,
        )
    )
    raw = io.BytesIO()
    output = io.TextIOWrapper(raw, encoding="ascii")
    monkeypatch.setattr(sys, "stdout", output)
    assert run_headless(manager) == 0
    output.flush()
    assert b"[child unicode #1] \\u5b8c" in raw.getvalue()


def test_pass_warning_and_plain_jobs_return_zero_with_distinct_sources(tmp_path):
    path = work_file(
        tmp_path,
        "job = Job('checked', [sys.executable, '-u', '-c', \"print('NOTICE'); print('COMPLETE')\"])\n"
        "def parse(line, ctx):\n    return {'notice': line == 'NOTICE'}\n"
        "def analyze(record, ctx):\n    if record['notice']: ctx.summary.issue('notice', level='warning', message='Review notice')\n"
        "parser = Parser('source', parse)\n"
        "mgr.add(job, flow=Flow('complete', parsers=[parser], analyzers=[Analyzer('completion', records=[Binding(parser, analyze)], on_finish=lambda end, ctx: ctx.summary.result('PASS', final=True))]))\n"
        "mgr.add(Job('plain', [sys.executable, '-c', \"print('ordinary output')\"]))\n",
    )
    result = run_cli(path)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "[child checked #1] NOTICE" in result.stdout
    assert "PASS" in result.stdout and "N/A" in result.stdout
    assert "[Wave] Final summary" in result.stdout
    assert "analysis completion: PASS" in result.stdout


def test_exit_zero_with_failed_analysis_returns_one(tmp_path):
    path = work_file(
        tmp_path,
        "job = Job('failed_check', [sys.executable, '-c', \"print('ERROR')\"])\n"
        "mgr.add(job, flow=Flow('failed', analyzers=[Analyzer('no_error', on_finish=lambda end, ctx: ctx.summary.result('FAIL', reason='ERROR output'))]))\n",
    )
    result = run_cli(path)
    assert result.returncode == 1, result.stderr + result.stdout
    assert "FAIL" in result.stdout and "analysis no_error: FAIL" in result.stdout


def test_isolated_monitor_failure_returns_one(tmp_path):
    path = work_file(
        tmp_path,
        "job = Job('monitor_error', [sys.executable, '-c', \"print('value=oops')\"])\n"
        "mgr.add(job, flow=Flow('error', parsers=[Parser('value', lambda line, ctx: {'value': int(line.split('=')[1])} if line.startswith('value=') else None)]))\n",
    )
    result = run_cli(path)
    assert result.returncode == 1, result.stderr + result.stdout
    assert "ERROR" in result.stdout


def test_timeout_stops_and_drains_batch(tmp_path):
    path = work_file(
        tmp_path,
        "mgr.add(Job('slow', [sys.executable, '-u', '-c', 'import time; print(1); time.sleep(30)'], stop_timeout=0.1))\n",
    )
    result = run_cli(path, "--timeout", "0.2")
    assert result.returncode == 1, result.stderr + result.stdout
    assert "Batch timeout" in result.stdout
    assert "[Wave] Final summary" in result.stdout


def test_invalid_options_are_definition_errors():
    for args in (
        ["--headless"],
        ["--demo", "other.py"],
        ["--demo", "--max-workers", "0"],
        ["--demo", "--timeout", "1"],
        ["--demo", "--headless", "--timeout", "nan"],
    ):
        with pytest.raises(SystemExit) as exc:
            main(args)
        assert exc.value.code == 2


def test_worker_override_is_applied_before_dispatch(tmp_path):
    path = work_file(
        tmp_path,
        "for index in range(3):\n"
        "    mgr.add(Job(str(index), [sys.executable, '-u', '-c', \"import time; print('begin'); time.sleep(.1); print('end')\"]))\n",
    )
    result = run_cli(path, "-j", "1")
    assert result.returncode == 0, result.stderr + result.stdout
    events = [
        line.rsplit("] ", 1)[-1]
        for line in result.stdout.splitlines()
        if line.endswith("] begin") or line.endswith("] end")
    ]
    assert events == ["begin", "end"] * 3


def test_gui_target_is_preview_and_receives_source_path(tmp_path, monkeypatch):
    from rpkbin.wave import web

    path = work_file(
        tmp_path, "mgr.add(Job('preview', [sys.executable, '-c', 'print(1)']))\n"
    )
    seen = {}

    def launch(manager, **kwargs):
        seen.update(
            started=manager.started,
            workers=manager.max_workers,
            state=manager.jobs[0].status.value,
            source=kwargs["source_path"],
        )

    monkeypatch.setattr(web, "launch", launch)
    assert main([str(path), "-j", "1", "--no-browser"]) == 0
    assert seen == {
        "started": False,
        "workers": 1,
        "state": "PENDING",
        "source": str(path),
    }


def test_gui_default_does_not_open_browser_and_open_enables_it(tmp_path, monkeypatch):
    from rpkbin.wave import web

    path = work_file(
        tmp_path, "mgr.add(Job('preview', [sys.executable, '-c', 'print(1)']))\n"
    )
    calls = []

    def launch(manager, **kwargs):
        calls.append(kwargs["open_browser"])

    monkeypatch.setattr(web, "launch", launch)
    assert main([str(path)]) == 0
    assert main([str(path), "--open"]) == 0
    assert calls == [False, True]


def test_gui_rejects_conflicting_open_and_no_browser(tmp_path):
    path = work_file(
        tmp_path, "mgr.add(Job('preview', [sys.executable, '-c', 'print(1)']))\n"
    )
    with pytest.raises(SystemExit) as exc:
        main([str(path), "--open", "--no-browser"])
    assert exc.value.code == 2


def test_interruption_requests_cleanup_and_returns_130(monkeypatch, capsys):
    from rpkbin.wave.cli import run_headless
    from rpkbin.jobcore import Job, JobManager

    manager = WaveWorkspace(JobManager())
    manager.add(
        Job(
            "interrupted",
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stop_timeout=0.1,
        )
    )
    original = manager._drain
    count = 0

    def interrupted_once(timeout=None):
        nonlocal count
        count += 1
        if count == 1:
            raise KeyboardInterrupt
        return original(timeout)

    monkeypatch.setattr(manager, "_drain", interrupted_once)
    assert run_headless(manager) == 130
    assert all(job.status.is_finished for job in manager.jobs)
    assert "Interrupted; stopping jobs" in capsys.readouterr().out


def test_terminate_as_interrupt_main_thread_and_worker_thread_safety():
    import signal
    import threading
    from rpkbin.wave.workspace import _terminate_handler, terminate_as_interrupt

    before = signal.getsignal(signal.SIGTERM)
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        with terminate_as_interrupt():
            active = signal.getsignal(signal.SIGTERM)
            assert active is _terminate_handler
            with pytest.raises(KeyboardInterrupt):
                active(signal.SIGTERM, None)
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL

        # Non-default handlers installed by an embedding host are preserved.
        def custom(*_):
            return None

        signal.signal(signal.SIGTERM, custom)
        with terminate_as_interrupt():
            assert signal.getsignal(signal.SIGTERM) is custom
        assert signal.getsignal(signal.SIGTERM) is custom

        # Non-main threads must never touch signal handlers or raise ValueError.
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        worker_ok = []

        def run_in_worker():
            with terminate_as_interrupt():
                worker_ok.append(signal.getsignal(signal.SIGTERM) is signal.SIG_DFL)

        thread = threading.Thread(target=run_in_worker)
        thread.start()
        thread.join(5)
        assert worker_ok == [True]
    finally:
        signal.signal(signal.SIGTERM, before)


def test_b14b_remove_job_close_interrupt_and_load_work_file_cleanup(tmp_path, monkeypatch):
    from rpkbin.jobcore import Job
    from rpkbin.wave.cli import load_work_file

    ws = WaveWorkspace()
    try:
        job = ws.add(Job("removable", [sys.executable, "-c", "pass"]))
        ws._source_tasks[job.id] = object()
        receipt = ws.remove_job(job.id, expected_attempt=job.attempt_id, expected_submission=job.submission_id)
        assert receipt.accepted
        assert job.id not in ws._source_tasks
    finally:
        ws.close()

    ws2 = WaveWorkspace(archive_dir=tmp_path)
    ws2.add(Job("noop", [sys.executable, "-c", "pass"]))

    def interrupted_join(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(ws2, "_join_sources", interrupted_join)
    with pytest.raises(KeyboardInterrupt):
        ws2.close()
    assert ws2.manager._closed
    assert ws2.archive._closed and not ws2.archive_health["closed_cleanly"]

    # load_work_file closes created workspace/archive if validation or empty-job check fails.
    arch_dir = tmp_path / "leaked_archives"
    empty_script = work_file(
        tmp_path,
        f"mgr = WaveWorkspace(archive_dir={str(arch_dir)!r})\n",
    )
    with pytest.raises(ValueError, match="contains no jobs"):
        load_work_file(empty_script)
    archives = list(arch_dir.glob("*.sqlite3"))
    assert len(archives) == 1
    from rpkbin.wave.archive import RunArchive
    reopened = RunArchive(archives[0])
    assert reopened.health["closed_cleanly"]

    bad_col_script = work_file(
        tmp_path,
        f"mgr = WaveWorkspace(columns=['bad.col.name'], archive_dir={str(arch_dir)!r})\n"
        "mgr.add(Job('j', [sys.executable, '-c', 'pass']))\n",
    )
    with pytest.raises(ValueError):
        load_work_file(bad_col_script)
    archives = sorted(arch_dir.glob("*.sqlite3"))
    assert len(archives) == 2
    assert all(RunArchive(p).health["closed_cleanly"] for p in archives)


def test_ascii_stdout_summary_and_launch_do_not_raise_unicode_error(tmp_path, monkeypatch):
    import io
    from rpkbin.jobcore import Job
    from rpkbin.wave import web
    from rpkbin.wave.cli import _summary

    raw_buf = io.BytesIO()
    ascii_stdout = io.TextIOWrapper(raw_buf, encoding="ascii", errors="strict")
    monkeypatch.setattr(sys, "stdout", ascii_stdout)

    ws = WaveWorkspace()
    try:
        ws.add(Job("unicode_—_job", [sys.executable, "-c", "pass"]))
        _summary(ws, ws.snapshot()["jobs"], source_mode=False)
    finally:
        ws.close()

    class DummyServer:
        url = "http://127.0.0.1:12345/?token=abc"
        workspace = WaveWorkspace()
        def start(self, *, open_browser=False):
            return self.url
        def stop(self):
            pass

    monkeypatch.setattr(web, "WaveServer", lambda *a, **kw: DummyServer())
    monkeypatch.setattr(web.time, "sleep", lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    web.launch(block=True)
    text = raw_buf.getvalue().decode("ascii")
    assert "\\u2014" in text and "\\u2026" in text
