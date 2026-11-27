"""Persistence health is visible and attempt-local, separate from acceptance."""

from rpkbin.wave import WaveWorkspace

import sys

from rpkbin.flow import Analyzer, Flow
from rpkbin.jobcore import Job, JobManager


def test_log_failure_survives_diagnostic_eviction_and_rerun_resets_health(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    manager = WaveWorkspace(JobManager())
    job = Job(
            "saved",
            [sys.executable, "-u", "-c", "print('COMPLETE')"],
            log_file=blocker / "output.log",
            pty=False,
        )
    manager.add(job, flow=Flow("persistence", analyzers=[Analyzer("accept", on_finish=lambda end, ctx: ctx.summary.result("PASS", "complete", final=True))]))
    try:
        manager.start()
        assert manager._drain(8)
        first = job.to_snapshot_dict()
        assert first["result"] == "PASS" and first["log_file_error"]["count"] == 1
        for index in range(101):
            job._attempt.diagnostics.append(
                dict(source="notification", message=str(index))
            )
        assert not any(d["source"] == "log_file" for d in job._attempt.diagnostics)
        assert job.to_snapshot_dict()["log_file_error"] == first["log_file_error"]

        blocker.unlink()
        blocker.mkdir()
        manager.rerun(job)
        assert manager._drain(8)
        current = job.to_snapshot_dict()
        history = job.to_snapshot_dict(attempt_id=first["attempt_id"])
        assert current["result"] == history["result"] == "PASS"
        assert current["log_file_error"] is None
        assert history["log_file_error"] == first["log_file_error"]
        assert "COMPLETE" in (blocker / "output.log").read_text(encoding="utf-8")
    finally:
        manager.close()


def test_manager_log_directory_failure_reports_actual_destination(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    manager = WaveWorkspace(JobManager(log_dir=blocker))
    job = manager.add(Job("plain", [sys.executable, "-c", "print('DATA')"], pty=False))
    try:
        manager.start()
        assert manager._drain(8)
        snapshot = job.to_snapshot_dict()
        assert snapshot["result"] == "N/A"
        assert snapshot["log_file_error"]["path"] == str(blocker / f"{job.id}.log")
        assert snapshot["warning_count"] == snapshot["error_count"] == 0
    finally:
        manager.close()
