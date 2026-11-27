"""Independent regressions for publication ordering and shutdown audit state."""

from rpkbin.wave import WaveWorkspace

import sys
import threading

import pytest

from rpkbin.jobcore import Job, JobManager
from rpkbin.wave import RunArchive
from rpkbin.flow import ActionOutcome, Automation, Flow, FlowSession, Parser


def test_parser_publication_cannot_be_overtaken_by_concurrent_action_persistence(tmp_path):
    store = RunArchive(tmp_path / "ordering.sqlite3", create=True)
    store.save_attempt(dict(id="job", attempt_id="attempt", attempt_number=1))
    entered, prepared, release, attempted, completed = (threading.Event() for _ in range(5))
    failures = []

    def publish(publication):
        if publication["stage"] == "parser":
            prepared.set()
            assert release.wait(10)
        assert store.append_flow("attempt", publication)

    def react(event, ctx):
        entered.set()
        assert prepared.wait(10)
        attempted.set()
        ctx.actions.stop()
        completed.set()

    engine = FlowSession(Flow("ordering", parsers=[Parser("p", lambda line, ctx: {"x": 1})],
                              automations=[Automation("a", on_event=react)]),
                         publish=publish, clock_enabled=True, automation_enabled=True, control={"stop": lambda: ActionOutcome("accepted", "Stop requested")})

    def feed():
        try:
            engine.feed("value")
        except Exception as exc:
            failures.append(exc)

    engine.start()
    assert entered.wait(10), "automation worker was not entered"
    feeder = threading.Thread(target=feed)
    feeder.start()
    try:
        # Hosted Linux runners can take longer to schedule the automation
        # worker while the full core suite is running.
        assert attempted.wait(10), "automation callback did not reach the publication gate"
        assert not completed.wait(.05)
        first = store.flow_page("job", "attempt")
        assert first["rows"] == []
        release.set()
        feeder.join(10)
        assert not feeder.is_alive() and not failures
        assert engine.wait_automation(10)
        second = store.flow_page("job", "attempt", after=first["next_seq"] or 0)
        assert [row["seq"] for row in second["rows"]] == [1, 2, 3]
        assert store.automation_history("job", "attempt")["rows"][0]["request_id"] == 2
    finally:
        release.set()
        feeder.join(10)
        engine.close_automation()
        engine.wait_automation(10)
        store.close()


def test_cancelled_never_started_callback_expires_and_saves_honest_metadata(tmp_path, monkeypatch):
    import rpkbin.flow._automation as worker

    monkeypatch.setattr(worker, "COMPLETION_TIMEOUT", .05)
    entered, release, completed = (threading.Event() for _ in range(3))
    native, rejections = [], []

    def event(value, ctx):
        native.append(value.name)
        if value.name == "finished":
            entered.set()
            release.wait(3)
            try:
                ctx.actions.stop()
            except RuntimeError as exc:
                rejections.append(str(exc))
            completed.set()

    manager = WaveWorkspace(JobManager(), archive_dir=tmp_path / "archives")
    job = manager.add(Job("cancelled", [sys.executable, "-c", "pass"], pty=False), flow=Flow("cancelled", automations=[Automation("a", on_event=event)]))
    try:
        assert job.stop()
        assert entered.wait(1)
        assert manager._drain(2)
        assert job.pid is None and job.status.value == "CANCELLED"
        assert not job._attempt.sink.flow_session.automation_pending
        assert job.to_snapshot_dict(include_details=True)["flow"]["automation_error_count"] == 1
        manager.close()
        reopened = RunArchive(manager.archive.path)
        try:
            saved = reopened.attempt(job.id, job.attempt_id)
            assert saved["status"] == "CANCELLED" and saved["finalized"]
            assert saved["flow"]["automation_error_count"] == 1
            assert "deadline" in saved["flow"]["automations"][0]["error"]
            assert reopened.automation_history(job.id, job.attempt_id)["rows"] == []
            release.set()
            assert completed.wait(2)
            assert job._attempt.sink.flow_session.wait_automation(2)
            assert rejections and native == ["status_changed", "finished"]
        finally:
            reopened.close()
    finally:
        release.set()
        job._attempt.sink.flow_session.close_automation()
        job._attempt.sink.flow_session.wait_automation(3)
        manager.close()


def test_finished_callback_timeout_cannot_control_a_rerun_and_is_archived(tmp_path, monkeypatch):
    import rpkbin.flow._automation as worker

    monkeypatch.setattr(worker, "COMPLETION_TIMEOUT", .05)
    entered, release, completed = (threading.Event() for _ in range(3))
    rejections = []

    def event(value, ctx):
        if value.name != "finished" or value.data["attempt_number"] != 1:
            return
        entered.set()
        release.wait(3)
        with pytest.raises(RuntimeError) as error:
            ctx.actions.stop()
        rejections.append(str(error.value))
        completed.set()

    manager = WaveWorkspace(JobManager(), archive_dir=tmp_path / "archives")
    job = manager.add(Job("rerun", [sys.executable, "-c", "pass"], pty=False), flow=Flow("rerun", automations=[Automation("a", on_event=event)]))
    first_session = job._attempt.sink.flow_session
    try:
        manager.start()
        assert entered.wait(2)
        assert manager._drain(4)
        first = job.attempt_id
        assert manager.archive.attempt(job.id, first)["flow"]["automation_error_count"] == 1
        manager.rerun(job.id)
        assert manager._drain(4)
        assert job.attempt_id != first and job.status.value == "DONE"
        release.set()
        assert completed.wait(2) and first_session.wait_automation(2)
        assert rejections and not job._attempt.stop_requested
        assert manager.archive.automation_history(job.id, first)["rows"] == []
        assert manager.archive.automation_history(job.id, job.attempt_id)["rows"] == []
    finally:
        release.set()
        first_session.close_automation()
        first_session.wait_automation(3)
        manager.close()
