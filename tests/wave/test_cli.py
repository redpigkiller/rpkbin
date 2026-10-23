"""CLI contracts: definition safety, outcome codes and output provenance."""

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
        "import sys\nfrom rpkbin.wave import Job, JobManager, Event, Expect\n"
        "mgr = JobManager(max_workers=2)\n" + body,
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


def test_pass_warning_and_no_checks_return_zero_with_distinct_sources(tmp_path):
    path = work_file(
        tmp_path,
        "job = mgr.add(Job('checked', [sys.executable, '-u', '-c', \"print('NOTICE'); print('COMPLETE')\"]))\n"
        "job.check('completion', match='^COMPLETE$', expect=Expect.PRESENT)\n"
        "job.on(Event.LOG, match='NOTICE', warn=True, message='Review notice')\n"
        "mgr.add(Job('plain', [sys.executable, '-c', \"print('ordinary output')\"]))\n",
    )
    result = run_cli(path)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "[child checked #1] NOTICE" in result.stdout
    assert "PASS" in result.stdout and "N/A" in result.stdout
    assert "[Wave] Final summary" in result.stdout
    assert "completion: OK" in result.stdout


def test_exit_zero_with_failed_check_returns_one(tmp_path):
    path = work_file(
        tmp_path,
        "job = mgr.add(Job('failed_check', [sys.executable, '-c', \"print('ERROR')\"]))\n"
        "job.check('no_error', match='ERROR', expect=Expect.ABSENT)\n",
    )
    result = run_cli(path)
    assert result.returncode == 1, result.stderr + result.stdout
    assert "FAIL" in result.stdout and "no_error: FAIL" in result.stdout


def test_isolated_monitor_failure_returns_one(tmp_path):
    path = work_file(
        tmp_path,
        "job = mgr.add(Job('monitor_error', [sys.executable, '-c', \"print('value=oops')\"]))\n"
        "job.track('value', r'value=(.*)', cast=int)\n",
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


def test_interruption_requests_cleanup_and_returns_130(monkeypatch, capsys):
    from rpkbin.wave.cli import run_headless
    from rpkbin.wave import Job, JobManager

    manager = JobManager()
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
