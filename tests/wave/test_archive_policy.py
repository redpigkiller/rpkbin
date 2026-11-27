"""Tests for Archive persistence modes, custom scratch dirs, NFS staging, and metadata pruning."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

import pytest

from rpkbin.flow import Analyzer, Flow
from rpkbin.jobcore import Job
from rpkbin.wave import ArchivePolicy, RunArchive, WaveWorkspace, cli, prune_archives
from rpkbin.wave.archive import (
    _ARCHIVE_STEM_RE,
    _infer_archive_outcome,
    default_archive_dir,
    default_temp_dir,
    find_archive_by_run_id,
    resolve_directory_option,
)


def _result_job(name: str, *, ok: bool) -> tuple[Job, Flow]:
    verdict = "PASS" if ok else "FAIL"
    reason = "ok" if ok else "boom"
    flow = Flow(
        f"{name}_flow",
        analyzers=[
            Analyzer(
                "verdict",
                on_finish=lambda end, ctx, v=verdict, r=reason: ctx.summary.result(
                    v, r, final=True
                ),
            )
        ],
    )
    code = f"print('line-for-{name}', flush=True); import sys; sys.exit({0 if ok else 1})"
    job = Job(name, [sys.executable, "-u", "-c", code], pty=False)
    return job, flow


def _create_closed_archive(
    directory: Path,
    *,
    ok: bool,
    closed_cleanly: bool = True,
    age_days: float = 0.0,
    now: float,
) -> Path:
    ws = WaveWorkspace(archive_dir=directory)
    job, flow = _result_job("sample", ok=ok)
    ws.add(job, flow=flow)
    if closed_cleanly:
        ws.start()
        assert ws._drain(8)
        ws.close()
        path = ws.archive.path
    else:
        path = ws.archive.path
        ws.archive.close(clean=False)
        ws.manager.close()
    ts = now - age_days * 86400.0
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT OR REPLACE INTO metadata VALUES('created',?)", (json.dumps(ts),))
        if closed_cleanly:
            conn.execute("INSERT OR REPLACE INTO metadata VALUES('closed_at',?)", (json.dumps(ts),))
        conn.commit()
    return path


def test_ephemeral_mode_allows_live_reads_then_cleans_up(tmp_path):
    arch_dir = tmp_path / "archives"
    temp_dir = tmp_path / "scratch"
    ws = WaveWorkspace(archive_mode="ephemeral", archive_dir=arch_dir, temp_dir=temp_dir)
    ws.enable_archive()
    job, flow = _result_job("ephem", ok=True)
    ws.add(job, flow=flow)
    ws.start()
    assert ws._drain(8)

    # Live archive queries work during execution in scratch dir.
    assert ws.archive.path.parent == temp_dir.resolve()
    assert ws.archive_catalog_dir == arch_dir.resolve()
    rows = ws.archive.page(job.id, job.attempt_id)["rows"]
    assert any("line-for-ephem" in r["text"] for r in rows)

    ws.close()
    assert ws.archive_health["disposition"] == "discarded_ephemeral"
    assert list(temp_dir.glob("*")) == []
    assert not arch_dir.exists() or list(arch_dir.glob("*")) == []


def test_keep_on_fail_discards_pass_and_saves_single_file_on_fail(tmp_path):
    arch_dir = tmp_path / "archives"
    temp_dir = tmp_path / "scratch"

    ws_pass = WaveWorkspace(archive_mode="keep-on-fail", archive_dir=arch_dir, temp_dir=temp_dir)
    ws_pass.enable_archive()
    job_p, flow_p = _result_job("pass_job", ok=True)
    ws_pass.add(job_p, flow=flow_p)
    ws_pass.start()
    assert ws_pass._drain(8)
    ws_pass.close()
    assert ws_pass.archive_health["disposition"] == "discarded_pass"
    assert list(temp_dir.glob("*")) == []
    assert not arch_dir.exists() or list(arch_dir.glob("*")) == []

    ws_fail = WaveWorkspace(archive_mode="keep_on_fail", archive_dir=arch_dir, temp_dir=temp_dir)
    ws_fail.enable_archive()
    job_f, flow_f = _result_job("fail_job", ok=False)
    ws_fail.add(job_f, flow=flow_f)
    ws_fail.start()
    assert ws_fail._drain(8)
    ws_fail.close()

    assert ws_fail.archive_health["disposition"] == "saved_on_failure"
    assert list(temp_dir.glob("*")) == []
    saved = sorted(p.name for p in arch_dir.glob("*"))
    assert len(saved) == 1 and saved[0].endswith(".sqlite3")
    reopened = RunArchive(arch_dir / saved[0])
    assert reopened.health["closed_cleanly"] is True
    assert reopened.metadata["run_outcome"] == "FAIL"
    rows = reopened.page(job_f.id, job_f.attempt_id)["rows"]
    assert any("line-for-fail_job" in r["text"] for r in rows)


def test_persist_relative_dir_and_nfs_staging_moves_cleanly_on_close(tmp_path, monkeypatch):
    cwd_dir = tmp_path / "project_cwd"
    cwd_dir.mkdir()
    scratch_dir = tmp_path / "local_tmp"
    expected_dir = (cwd_dir / ".wave_archives").resolve()
    monkeypatch.chdir(cwd_dir)

    def fake_net_fs(directory):
        return "nfs4" if Path(directory).resolve() == expected_dir else None

    monkeypatch.setattr("rpkbin.wave.workspace._network_filesystem", fake_net_fs)

    ws = WaveWorkspace(archive_dir="./.wave_archives", temp_dir=scratch_dir)
    job, flow = _result_job("nfs_job", ok=True)
    ws.add(job, flow=flow)

    assert ws.archive is not None
    assert ws.archive.path.parent == scratch_dir.resolve()
    assert ws.archive_catalog_dir == expected_dir
    assert ws.archive_health["staged"] is True
    assert not expected_dir.exists() or list(expected_dir.glob("*.sqlite3")) == []

    ws.start()
    assert ws._drain(8)
    ws.close()

    assert ws.archive_health["disposition"] == "saved"
    assert ws.archive.path.parent == expected_dir
    assert list(scratch_dir.glob("*")) == []
    saved = list(expected_dir.glob("*.sqlite3"))
    assert len(saved) == 1
    assert _ARCHIVE_STEM_RE.fullmatch(saved[0].stem)
    assert saved[0].stem.endswith(f"_{ws.run_id[:8]}")
    assert not (expected_dir / ".gitignore").exists()
    assert not (cwd_dir / ".gitignore").exists()
    assert RunArchive(saved[0]).health["complete"] is True


def test_cli_archive_flags_override_work_file_and_validate_conflicts(tmp_path, monkeypatch):
    script_arch = tmp_path / "script_archives"
    custom_tmp = tmp_path / "custom_tmp"
    run_cwd = tmp_path / "run_cwd"
    run_cwd.mkdir()
    target_arch = (run_cwd / ".wave_archives").resolve()
    monkeypatch.chdir(run_cwd)

    pass_work = tmp_path / "work_pass.py"
    pass_work.write_text(
        f"""
import sys
from rpkbin.flow import Analyzer, Flow
from rpkbin.jobcore import Job
from rpkbin.wave import WaveWorkspace

flow = Flow("p", analyzers=[Analyzer("v", on_finish=lambda e, c: c.summary.result("PASS", "ok", final=True))])
ws = WaveWorkspace(archive_dir={str(script_arch)!r})
ws.add(Job("ok", [sys.executable, "-c", "print('ok')"], pty=False), flow=flow)
""",
        encoding="utf-8",
    )

    fail_work = tmp_path / "work_fail.py"
    fail_work.write_text(
        f"""
import sys
from rpkbin.flow import Analyzer, Flow
from rpkbin.jobcore import Job
from rpkbin.wave import WaveWorkspace

flow = Flow("f", analyzers=[Analyzer("v", on_finish=lambda e, c: c.summary.result("FAIL", "no", final=True))])
ws = WaveWorkspace(archive_dir={str(script_arch)!r})
ws.add(Job("bad", [sys.executable, "-c", "import sys; sys.exit(1)"], pty=False), flow=flow)
""",
        encoding="utf-8",
    )

    assert cli.main([str(pass_work), "--headless", "--no-save", "--temp-dir", str(custom_tmp)]) == 0
    assert not script_arch.exists() or list(script_arch.glob("*")) == []
    assert list(custom_tmp.glob("*")) == []

    assert cli.main([
        str(pass_work), "--headless", "--archive-mode", "keep-on-fail",
        "--archive-dir", ".wave_archives", "--temp-dir", str(custom_tmp),
    ]) == 0
    assert not target_arch.exists() or list(target_arch.glob("*.sqlite3")) == []
    assert not script_arch.exists() or list(script_arch.glob("*")) == []

    assert cli.main([
        str(fail_work), "--headless", "--archive-mode", "keep-on-fail",
        "--archive-dir", "./.wave_archives", "--temp-dir", str(custom_tmp),
    ]) == 1
    saved = list(target_arch.glob("*.sqlite3"))
    assert len(saved) == 1
    assert "_work_fail_" in saved[0].stem
    assert list(custom_tmp.glob("*")) == []
    assert not script_arch.exists() or list(script_arch.glob("*")) == []

    with pytest.raises(SystemExit):
        cli.main([str(pass_work), "--no-save", "--archive-mode", "persist"])
    with pytest.raises(SystemExit):
        cli.main([str(pass_work), "--retention-days", "-1"])
    with pytest.raises(SystemExit):
        cli.main(["--archive", "missing.sqlite3", "--no-save"])


def test_prune_archives_uses_sqlite_metadata_and_protects_unclosed_or_unrelated(tmp_path):
    arch_dir = tmp_path / "archives"
    now = time.time()

    old_pass = _create_closed_archive(arch_dir, ok=True, age_days=10.0, now=now)
    fresh_pass = _create_closed_archive(arch_dir, ok=True, age_days=2.0, now=now)
    old_fail = _create_closed_archive(arch_dir, ok=False, age_days=10.0, now=now)
    unclosed = _create_closed_archive(arch_dir, ok=True, closed_cleanly=False, age_days=20.0, now=now)
    active = _create_closed_archive(arch_dir, ok=True, age_days=20.0, now=now)
    active_run_id = RunArchive(active).run_id

    # Legacy <32-hex>.sqlite3 filename remains supported alongside readable names.
    legacy_run_id = "0123456789abcdef0123456789abcdef"
    legacy_old = arch_dir / f"{legacy_run_id}.sqlite3"
    legacy_store = RunArchive(legacy_old, create=True, run_id=legacy_run_id)
    legacy_store.set_outcome("PASS")
    legacy_store.close(clean=True)
    with closing(sqlite3.connect(legacy_old)) as conn:
        old_ts = now - 12.0 * 86400.0
        conn.execute("INSERT OR REPLACE INTO metadata VALUES('created',?)", (json.dumps(old_ts),))
        conn.execute("INSERT OR REPLACE INTO metadata VALUES('closed_at',?)", (json.dumps(old_ts),))
        conn.commit()

    unrelated = arch_dir / "custom_notes.sqlite3"
    unrelated.write_bytes(b"not a wave archive")

    res = prune_archives(
        arch_dir,
        pass_retention_days=7,
        fail_retention_days=30,
        exclude_run_id=active_run_id,
        now=now,
    )
    assert set(res["deleted"]) == {str(old_pass), str(legacy_old)}
    assert not old_pass.exists()
    assert not legacy_old.exists()
    assert fresh_pass.exists()
    assert old_fail.exists()
    assert unclosed.exists()
    assert active.exists()
    assert unrelated.exists()

    # Advance 25 days: old_fail is now 35 days old (> 30d) and gets pruned; unclosed & active remain safe.
    res2 = prune_archives(
        arch_dir,
        pass_retention_days=7,
        fail_retention_days=30,
        exclude_run_id=active_run_id,
        now=now + 25 * 86400.0,
    )
    assert str(old_fail) in res2["deleted"]
    assert not old_fail.exists()
    assert unclosed.exists()
    assert active.exists()
    assert unrelated.exists()


def test_default_dirs_and_policy_validation(tmp_path, monkeypatch):
    monkeypatch.delenv("RPK_WAVE_TEMP_DIR", raising=False)
    monkeypatch.delenv("RPK_WAVE_ARCHIVE_DIR", raising=False)
    assert default_temp_dir().name.startswith("rpk-wave-")

    cwd_dir = tmp_path / "ws_cwd"
    cwd_dir.mkdir()
    monkeypatch.chdir(cwd_dir)
    # Standard relative paths resolve against CWD; literal "cwd" is a normal directory name.
    assert resolve_directory_option("cwd", default_fn=lambda: tmp_path) == (cwd_dir / "cwd").resolve()
    monkeypatch.setenv("RPK_WAVE_TEMP_DIR", "./.wave_tmp")
    monkeypatch.setenv("RPK_WAVE_ARCHIVE_DIR", ".wave_archives")
    assert default_temp_dir() == (cwd_dir / ".wave_tmp").resolve()
    assert default_archive_dir() == (cwd_dir / ".wave_archives").resolve()

    monkeypatch.setenv("RPK_WAVE_ARCHIVE_DIR", "cwd")
    assert default_archive_dir() == (cwd_dir / "cwd").resolve()

    policy = ArchivePolicy(mode="keep-on-fail", retention_days=14, pass_retention_days=7)
    assert policy.mode == "keep_on_fail"
    assert policy.effective_pass_retention_days() == 7.0
    assert policy.effective_fail_retention_days() == 14.0

    with pytest.raises(ValueError):
        ArchivePolicy(mode="invalid")
    with pytest.raises(ValueError):
        ArchivePolicy(retention_days=-0.5)


def test_readable_archive_catalog_and_find_legacy_compatibility(tmp_path):
    arch_dir = tmp_path / "archives"
    arch_dir.mkdir()
    src_alpha = str((tmp_path / "work_alpha.py").resolve())
    src_beta = str((tmp_path / "work_beta.py").resolve())

    ws_a = WaveWorkspace()
    ws_a.configure_archive(archive_dir=arch_dir, override=True)
    ws_a.enable_archive(source_path=src_alpha)
    run_a = ws_a.run_id
    path_a = ws_a.archive.path
    ws_a.close()
    assert path_a.name.startswith("20")
    assert "_work_alpha_" in path_a.name
    assert path_a.name.endswith(f"_{run_a[:8]}.sqlite3")

    legacy_id = "fedcba9876543210fedcba9876543210"
    legacy_path = arch_dir / f"{legacy_id}.sqlite3"
    legacy_store = RunArchive(legacy_path, create=True, run_id=legacy_id, source_path=src_beta)
    legacy_store.set_outcome("FAIL")
    legacy_store.close(clean=True)

    assert find_archive_by_run_id(arch_dir, run_a) == path_a
    assert find_archive_by_run_id(arch_dir, legacy_id) == legacy_path
    assert find_archive_by_run_id(arch_dir, "0" * 32) is None

    filtered = RunArchive.catalog(arch_dir, source_path=src_alpha)
    assert [row["run_id"] for row in filtered["rows"]] == [run_a]
    assert filtered["rows"][0]["filename"] == path_a.name
    assert filtered["rows"][0]["path"] == str(path_a)
    assert filtered["rows"][0]["run_outcome"] == "UNSTARTED"

    everything = RunArchive.catalog(arch_dir, source_path=src_alpha, include_all=True)
    assert {row["run_id"] for row in everything["rows"]} == {run_a, legacy_id}
    outcomes = {row["run_id"]: row["run_outcome"] for row in everything["rows"]}
    assert outcomes[legacy_id] == "FAIL"


def test_clean_close_removes_wal_sidecars_and_readonly_infer_outcome(tmp_path):
    arch_dir = tmp_path / "archives_clean"
    arch_dir.mkdir()
    pass_id = "a" * 32
    fail_id = "b" * 32
    pass_path = arch_dir / f"{pass_id}.sqlite3"
    fail_path = arch_dir / f"{fail_id}.sqlite3"

    pass_store = RunArchive(pass_path, create=True, run_id=pass_id, source_path="/src/pass.py")
    pass_store.save_attempt(
        {"attempt_id": "ap1", "id": "jp1", "attempt_number": 1, "status": "DONE", "result": "PASS", "error_count": 0},
        flush=True,
    )
    pass_store.close(clean=True)

    fail_store = RunArchive(fail_path, create=True, run_id=fail_id, source_path="/src/fail.py")
    fail_store.save_attempt(
        {"attempt_id": "af1", "id": "jf1", "attempt_number": 1, "status": "DONE", "result": "FAIL", "error_count": 2},
        flush=True,
    )
    fail_store.close(clean=True)

    for p in (pass_path, fail_path):
        assert not p.with_name(p.name + "-wal").exists()
        assert not p.with_name(p.name + "-shm").exists()

    old = time.time() - 30.0
    for p in (pass_path, fail_path):
        os.utime(p, (old, old))

    catalog = RunArchive.catalog(arch_dir, include_all=True)
    outcomes = {row["run_id"]: row["run_outcome"] for row in catalog["rows"]}
    assert outcomes == {pass_id: "PASS", fail_id: "FAIL"}
    assert _infer_archive_outcome(RunArchive(pass_path)) == "PASS"
    assert _infer_archive_outcome(RunArchive(fail_path)) == "FAIL"
    for p in (pass_path, fail_path):
        assert not p.with_name(p.name + "-wal").exists()
        assert not p.with_name(p.name + "-shm").exists()


def test_catalog_missing_dir_and_bounded_early_stop_with_cache(tmp_path):
    missing = RunArchive.catalog(tmp_path / "does_not_exist")
    assert missing == {
        "rows": [],
        "cursor": None,
        "has_more": False,
        "unreadable_files": 0,
        "total_files": 0,
        "scanned_files": 0,
        "partial": False,
    }

    arch_dir = tmp_path / "bulk_archives"
    arch_dir.mkdir()
    (arch_dir / "notes.txt").write_text("ignore", encoding="utf-8")
    base_ts = time.time() - 120.0
    expected_ids: list[str] = []
    for idx in range(90):
        run_id = f"{idx:032x}"
        expected_ids.append(run_id)
        db_path = arch_dir / f"{run_id}.sqlite3"
        ts = base_ts - (89 - idx) * 10.0
        store = RunArchive(db_path, create=True, run_id=run_id, source_path="/src/bulk.py")
        store.set_outcome("PASS")
        store.close(clean=True)
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute("INSERT OR REPLACE INTO metadata VALUES('created',?)", (json.dumps(ts),))
            conn.commit()
        os.utime(db_path, (ts, ts))

    newest_20 = list(reversed(expected_ids))[:20]
    first = RunArchive.catalog(arch_dir, include_all=True, limit=20, max_seconds=3.0)
    assert [row["run_id"] for row in first["rows"]] == newest_20
    assert first["has_more"] is True
    assert bool(first["cursor"]) is True
    assert first["total_files"] == 90
    assert first["scanned_files"] < 90
    assert first["partial"] is False

    second = RunArchive.catalog(arch_dir, include_all=True, limit=20, max_seconds=3.0)
    assert second["rows"] == first["rows"]
    assert second["scanned_files"] == first["scanned_files"]
