from rpkbin.wave import WaveWorkspace

from contextlib import closing

import pytest

from rpkbin.flow import Flow
from rpkbin.wave import FileSource, RunArchive, TailSource


@pytest.mark.parametrize("complete", [True, False])
def test_reanalysis_preserves_source_and_replays_exact_log(tmp_path, complete):
    from rpkbin.wave import ReanalysisSource

    source, definition = tmp_path / "input.log", tmp_path / "flow.py"
    lines = ["sample", "", "\u00e9" * 5000]
    source.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    old_source = FileSource(source) if complete else TailSource(source, idle_timeout=0)
    original = old_source.run(Flow("original"), archive_dir=tmp_path)
    saved_bytes = original.archive_path.read_bytes()
    source.unlink()
    definition.write_text(
        "from rpkbin.flow import Analyzer, Automation, Flow, Parser\n"
        "def parse(line, ctx): return {'length': len(line)}\n"
        "def react(*args): raise RuntimeError('automation must not run')\n"
        "flow = Flow('new', parsers=[Parser('replay', parse)], "
        "analyzers=[Analyzer('clock', every=0.01, on_tick=react)], "
        "automations=[Automation('off', on_event=react)])\n",
        encoding="utf-8",
    )
    result = ReanalysisSource(original.archive_path, original.snapshot["attempt_id"]).run(
        definition, archive_dir=tmp_path)
    assert result.complete is complete
    assert original.archive_path.read_bytes() == saved_bytes
    assert result.archive_path != original.archive_path
    snapshot = result.snapshot
    assert snapshot["attempt_id"] != original.snapshot["attempt_id"]
    assert snapshot["source_kind"] == "reanalysis"
    assert (snapshot["origin"], snapshot["origin_label"], snapshot["pid"]) == (
        "external", "External (replay)", None)
    assert snapshot["flow"]["error_count"] == 0
    assert snapshot["completion_reason"] == ("replayed" if complete else "source_incomplete")
    assert not any(snapshot["capabilities"].values())
    desc = snapshot["source_descriptor"]
    assert desc["archive_path"] == str(original.archive_path)
    assert desc["archive_run_id"] == RunArchive(original.archive_path).run_id
    assert desc["source_attempt_id"] == original.snapshot["attempt_id"]
    assert desc["flow_path"] == str(definition)
    with closing(RunArchive(result.archive_path)) as archive:
        assert archive.attempt(snapshot["id"], snapshot["attempt_id"])["verified"] is True
        row, = archive.attempts()["rows"]
        assert (row["origin"], row["origin_label"], row["pid"]) == (
            "external", "External (replay)", None)
        args = (snapshot["id"], snapshot["attempt_id"])
        assert [row["data"]["length"] for row in archive.flow_page(*args, producer="replay", kind="record")["rows"]] == [
            len(line) for line in lines]


def test_process_snapshots_include_capabilities_and_final_rerun(tmp_path):
    import sys

    from rpkbin.jobcore import Job, JobManager

    manager = WaveWorkspace(JobManager(), archive_dir=tmp_path)
    try:
        job = manager.add(Job("process", [sys.executable, "-c", "print('done')"], pty=False))
        pending = manager.snapshot()["jobs"][0]
        assert pending["source_kind"] == "process"
        assert (pending["origin"], pending["origin_label"]) == ("managed", None)
        assert pending["completion_reason"] is None
        assert pending["capabilities"] == dict(
            has_process=True, can_stop=True, can_send=True, can_interrupt=True,
            can_signal=True, can_kill=True, can_rerun=False,
        )
        manager.start()
        assert manager._drain(8)
        done = job.to_snapshot_dict()
        assert done["capabilities"]["can_rerun"]
        assert done["completion_reason"] == "process_exit"
        path = manager.archive.path
    finally:
        manager.close()
    with closing(RunArchive(path)) as archive:
        assert archive.attempt(done["id"], done["attempt_id"])["capabilities"] == done["capabilities"]
        row, = archive.attempts()["rows"]
        assert row["origin"] == "managed" and row["origin_label"] is None
        assert isinstance(row["pid"], int)
