"""RC review regression tests for rpkbin.wave backend (WB-01..WB-12, CC items)."""

from __future__ import annotations

import builtins
import importlib
import io
import json
import os
import sqlite3
import stat
import subprocess
import sys
import urllib.parse
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from rpkbin import __version__
from rpkbin.flow import Flow, FlowSession, Parser, ViewSpec
from rpkbin.jobcore import Job
from rpkbin.wave import (
    FileSource,
    ReanalysisSource,
    RunArchive,
    TailSource,
    WaveWorkspace,
    cli,
    web,
)
from rpkbin.wave import archive as archive_mod
from rpkbin.wave._web_routes import _browse_files, make_handler
from rpkbin.wave.archive import default_archive_dir, find_archive_by_run_id, move_archive_file
from rpkbin.wave.web import WaveServer


def _invoke_get(owner, path_with_query: str) -> tuple[int, dict]:
    """Invoke Handler.do_GET in-memory without opening a TCP socket."""
    handler_cls = make_handler(owner)
    raw_req = (
        f"GET {path_with_query} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:8000\r\n"
        f"X-Wave-Token: {owner._token}\r\n"
        "\r\n"
    ).encode("ascii")

    class _FakeSocket:
        def __init__(self, data: bytes):
            self._r = io.BytesIO(data)
            self._w = io.BytesIO()
            self._timeout = None

        def makefile(self, mode, buffering=-1):
            return self._r if "r" in mode else self._w

        def setsockopt(self, *args, **kwargs):
            pass

        def settimeout(self, value):
            self._timeout = value

        def gettimeout(self):
            return self._timeout

        def sendall(self, data: bytes):
            self._w.write(data)

    sock = _FakeSocket(raw_req)
    handler_cls(sock, ("127.0.0.1", 12345), SimpleNamespace())
    raw_resp = sock._w.getvalue()
    head, _, body = raw_resp.partition(b"\r\n\r\n")
    status_line = head.splitlines()[0].decode("ascii")
    status_code = int(status_line.split(" ", 2)[1])
    return status_code, json.loads(body.decode("utf-8"))


# ---------------------------------------------------------------------------
# S0/S1 regression tests (written before fixes)
# ---------------------------------------------------------------------------


def test_wb01_close_detects_busy_wal_and_move_refuses_to_delete_wal(tmp_path):
    staging = tmp_path / "staging"
    dest = tmp_path / "dest"
    staging.mkdir()
    src = staging / "run.sqlite3"
    store = RunArchive(src, create=True)
    store.save_attempt({"id": "j", "attempt_id": "a", "attempt_number": 1}, flush=True)
    for seq in range(1, 50):
        store.append("a", {"line_no": seq, "time": 1.0, "source": "stdout", "text": "x" * 200}, 0.1)
    store.flush()

    wal_path = Path(f"{src}-wal")
    assert wal_path.exists() and wal_path.stat().st_size > 0

    reader = sqlite3.connect(src.as_uri() + "?mode=ro", uri=True)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM records").fetchone()

        for seq in range(50, 120):
            store.append("a", {"line_no": seq, "time": 2.0, "source": "stdout", "text": "y" * 200}, 0.2)
        store.flush()

        store.close(clean=True)
        assert store.error is not None
        assert store.health["complete"] is False
        assert wal_path.exists() and wal_path.stat().st_size > 0

        with pytest.raises(OSError):
            move_archive_file(src, dest)
        assert src.exists() and wal_path.exists()
        assert not (dest / src.name).exists()
    finally:
        reader.close()

    with closing(RunArchive(src)) as reopened:
        assert len(reopened.page("j", "a", limit=100)["rows"]) == 100


def test_wb01_launch_stops_server_before_closing_workspace(monkeypatch):
    events = []

    class DummyWorkspace:
        jobs = []

        def close(self):
            events.append("close")

    class DummyServer:
        workspace = DummyWorkspace()

        def start(self, *, open_browser=False):
            return "http://127.0.0.1:9999/?token=t"

        def stop(self):
            events.append("stop")

    monkeypatch.setattr(web, "WaveServer", lambda *a, **kw: DummyServer())
    monkeypatch.setattr(web.time, "sleep", lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    web.launch(block=True)
    assert events == ["stop", "close"]


def test_wb02_export_vcd_bit_zero_float(tmp_path):
    parser = Parser("rtl", lambda line, ctx: None)
    rtl = ViewSpec.waveform(
        "rtl",
        source={"stage": "parser", "producer": "rtl"},
        axis={"field": "tick", "unit": "ns"},
        series={"mode": "long", "id_field": "sig", "path_field": "path", "value_field": "val", "value_type": "bit"},
    )
    flow = Flow("rtl_flow", parsers=[parser], views=[rtl])
    store = RunArchive(tmp_path / "vcd.sqlite3", create=True)
    try:
        store.save_attempt(
            {"id": "j", "attempt_id": "a", "attempt_number": 1, "flow": FlowSession(flow)._snapshot()},
            flush=True,
        )
        for seq, (tick, val) in enumerate(((10, 0), (20, 1), (30, 0)), 1):
            store.append("a", {"line_no": seq, "time": float(seq), "source": "stdout", "text": f"t={tick}"}, float(seq))
            store.append_flow(
                "a",
                {
                    "seq": seq,
                    "stage": "parser",
                    "producer": "rtl",
                    "kind": "record",
                    "data": {"tick": tick, "sig": "s", "path": ["top", "s"], "val": val},
                    "evidence": {"line_no": seq},
                },
            )
        store.flush()
        vcd = store.export_vcd("j", "a", view_id="rtl")
    finally:
        store.close()

    assert "#10\n0!\n#20\n1!\n#30\n0!\n" in vcd


def test_wb03_source_run_returns_moved_archive_path_and_closes_on_init_error(tmp_path, monkeypatch):
    log = tmp_path / "in.log"
    log.write_bytes(b"err\n")
    arch_kof = tmp_path / "arch_kof"
    temp_kof = tmp_path / "temp_kof"
    monkeypatch.setenv("RPK_WAVE_ARCHIVE_MODE", "keep-on-fail")
    monkeypatch.setenv("RPK_WAVE_TEMP_DIR", str(temp_kof))

    res_kof = FileSource(log).run(Flow("fail", result=lambda _: "FAIL"), archive_dir=arch_kof)
    assert res_kof.archive_path.parent == arch_kof.resolve()
    assert res_kof.archive_path.exists()

    monkeypatch.setenv("RPK_WAVE_ARCHIVE_MODE", "persist")
    arch_nfs = tmp_path / "arch_nfs"
    temp_nfs = tmp_path / "temp_nfs"
    monkeypatch.setenv("RPK_WAVE_TEMP_DIR", str(temp_nfs))
    monkeypatch.setattr(
        "rpkbin.wave.workspace._network_filesystem",
        lambda d: "nfs4" if Path(d).resolve() == arch_nfs.resolve() else None,
    )
    res_nfs = FileSource(log).run(Flow("ok"), archive_dir=arch_nfs)
    assert res_nfs.archive_path.parent == arch_nfs.resolve()
    assert res_nfs.archive_path.exists()

    flow_py = tmp_path / "flow.py"
    flow_py.write_text("from rpkbin.flow import Flow\nflow = Flow('replay')\n", encoding="utf-8")
    arch_re = tmp_path / "arch_re"
    monkeypatch.setattr(
        "rpkbin.wave.workspace._network_filesystem",
        lambda d: "nfs4" if Path(d).resolve() == arch_re.resolve() else None,
    )
    res_re = ReanalysisSource(res_nfs.archive_path, res_nfs.snapshot["attempt_id"]).run(
        flow_py, archive_dir=arch_re
    )
    assert res_re.archive_path.parent == arch_re.resolve()
    assert res_re.archive_path.exists()

    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir", encoding="utf-8")
    closes = []
    orig_close = WaveWorkspace.close

    def track_close(self):
        closes.append(self)
        return orig_close(self)

    monkeypatch.setattr(WaveWorkspace, "close", track_close)
    with pytest.raises(OSError, match="Cannot initialize archive"):
        FileSource(log).run(archive_dir=blocker)
    assert len(closes) == 1


def test_wb04_remove_job_by_name_pops_and_tracks_canonical_job_id():
    ws = WaveWorkspace()
    try:
        flow = Flow("f")
        job = ws.add(Job("worker_name", [sys.executable, "-c", "pass"]), flow=flow)
        receipt = ws.remove_job("worker_name")
        assert receipt.accepted
        assert ws.flow_for(job) is None
        assert job.id not in ws._flows
        assert ws._removed_flows == {job.id: flow}
        assert ws.restore_job("worker_name").accepted
        assert ws.flow_for(job) is flow
        assert ws._removed_flows == {}

        assert ws.remove_job(job).accepted
        assert ws.flow_for(job) is None
        assert ws._removed_flows == {job.id: flow}
        assert ws.restore_job(job).accepted
        assert ws.flow_for(job) is flow
        assert ws._removed_flows == {}
    finally:
        ws.close()


def test_wb05_empty_watched_dir_timeout_outcome_is_incomplete(tmp_path):
    watch_dir = tmp_path / "empty"
    watch_dir.mkdir()
    arch_dir = tmp_path / "arch"
    temp_dir = tmp_path / "temp"

    rc = cli.main([
        "logs",
        str(watch_dir),
        "--headless",
        "--timeout",
        "0.15",
        "--archive-mode",
        "keep-on-fail",
        "--archive-dir",
        str(arch_dir),
        "--temp-dir",
        str(temp_dir),
    ])
    assert rc == 1
    saved = list(arch_dir.glob("*.sqlite3"))
    assert len(saved) == 1
    with closing(RunArchive(saved[0])) as store:
        assert store.metadata["run_outcome"] == "INCOMPLETE"


def test_wb06_invalid_host_rejected_before_work_load_or_archive(tmp_path):
    marker = tmp_path / "executed"
    arch_dir = tmp_path / "arch"
    work = tmp_path / "work.py"
    work.write_text(
        f"import sys\nfrom pathlib import Path\nfrom rpkbin.jobcore import Job\nfrom rpkbin.wave import WaveWorkspace\n"
        f"Path({str(marker)!r}).touch()\n"
        f"ws = WaveWorkspace()\n"
        f"ws.add(Job('j', [sys.executable, '-c', 'pass']))\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        cli.main([str(work), "--host", "0.0.0.0", "--archive-dir", str(arch_dir)])
    assert exc.value.code == 2
    assert not marker.exists()
    assert not arch_dir.exists() or list(arch_dir.glob("*.sqlite3")) == []

    log = tmp_path / "sample.log"
    log.write_text("ok\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc_logs:
        cli.main(["logs", str(log), "--once", "--host", "0.0.0.0", "--archive-dir", str(arch_dir)])
    assert exc_logs.value.code == 2
    assert not arch_dir.exists() or list(arch_dir.glob("*.sqlite3")) == []

    flow_py = tmp_path / "flow.py"
    flow_py.write_text("from rpkbin.flow import Flow\nflow = Flow('f')\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc_re:
        cli.main(["reanalyze", str(tmp_path / "a.sqlite3"), "--flow", str(flow_py), "--host", "0.0.0.0", "--archive-dir", str(arch_dir)])
    assert exc_re.value.code == 2


def test_wb07_network_filesystem_resolves_realpath_before_reading_proc_mounts(monkeypatch):
    mounted = [False]
    calls = []

    def fake_realpath(p):
        calls.append("realpath")
        mounted[0] = True
        return "/mnt/autofs/proj"

    real_open = builtins.open

    def fake_open(file, *args, **kwargs):
        if str(file) == "/proc/mounts":
            calls.append("open")
            text = (
                "server:/vol /mnt/autofs nfs4 rw 0 0\n"
                if mounted[0]
                else "auto /mnt/autofs autofs rw 0 0\n"
            )
            return io.StringIO(text)
        return real_open(file, *args, **kwargs)

    fake_os = SimpleNamespace(
        name="posix",
        path=SimpleNamespace(realpath=fake_realpath),
    )
    monkeypatch.setattr(archive_mod, "os", fake_os)
    monkeypatch.setattr(builtins, "open", fake_open)
    assert archive_mod._network_filesystem("/mnt/autofs/proj") == "nfs4"
    assert calls == ["realpath", "open"]


def test_wb08_workspace_close_idempotent_and_run_archive_init_closes_writer(tmp_path, monkeypatch):
    arch_dir = tmp_path / "arch"
    ws = WaveWorkspace(archive_dir=arch_dir)
    finalize_calls = []
    prune_calls = []
    orig_finalize = ws._finalize_archive_disposition
    orig_prune = ws._prune_target_dir

    def spy_finalize(*, outcome):
        finalize_calls.append(outcome)
        return orig_finalize(outcome=outcome)

    def spy_prune():
        prune_calls.append(True)
        return orig_prune()

    monkeypatch.setattr(ws, "_finalize_archive_disposition", spy_finalize)
    monkeypatch.setattr(ws, "_prune_target_dir", spy_prune)
    ws.close()
    ws.close()
    assert len(finalize_calls) == 1
    assert len(prune_calls) == 1

    db = tmp_path / "bad_init.sqlite3"
    monkeypatch.setattr(
        archive_mod,
        "_validate_archive_connection",
        lambda conn: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError, match="boom"):
        RunArchive(db, create=True)
    db.unlink()


def test_cc01_default_archive_dir_ignores_empty_or_relative_env(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.delenv("RPK_WAVE_ARCHIVE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        archive_mod,
        "os",
        SimpleNamespace(name="posix", environ=os.environ, path=os.path),
    )
    for bad in ("", "   ", "relative/state"):
        monkeypatch.setenv("XDG_STATE_HOME", bad)
        assert default_archive_dir() == (fake_home / ".local/state/rpk-wave/archives").resolve()

    monkeypatch.setattr(
        archive_mod,
        "os",
        SimpleNamespace(name="nt", environ=os.environ, path=os.path),
    )
    for bad in ("", "   ", "relative/appdata"):
        monkeypatch.setenv("LOCALAPPDATA", bad)
        assert default_archive_dir() == (fake_home / "AppData/Local/rpk-wave/archives").resolve()


def test_cc03_browse_files_survives_home_and_archive_dir_errors(tmp_path, monkeypatch):
    monkeypatch.delenv("RPK_WAVE_ARCHIVE_DIR", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)

    def broken_home():
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", staticmethod(broken_home))

    listing = _browse_files(tmp_path, {}, archive_dir=tmp_path / "arch")
    shortcut_ids = [s["id"] for s in listing["shortcuts"]]
    assert "cwd" in shortcut_ids
    assert "home" not in shortcut_ids

    ws = WaveWorkspace()
    try:
        owner = SimpleNamespace(
            launch_cwd=tmp_path,
            workspace=ws,
            _opened_archive=None,
            source_path=None,
            _token="tok",
            _workspace_lock=ws._close_lock,
        )
        q = urllib.parse.urlencode({"path": str(tmp_path)})
        status, body = _invoke_get(owner, f"/api/files?{q}")
        assert status == 200
        assert "home" not in [s["id"] for s in body["shortcuts"]]
    finally:
        ws.close()


# ---------------------------------------------------------------------------
# S2 / S3 / Category-B/C tests
# ---------------------------------------------------------------------------


def _lines_from(result) -> list[str]:
    with RunArchive(result.archive_path) as store:
        return [
            row["text"]
            for row in store.page(result.snapshot["id"], result.snapshot["attempt_id"])["rows"]
        ]


def test_wb09_tail_end_marker_chunked_codecs_and_exact_offsets(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    class Polls:
        def __init__(self):
            self.waiting = Event()
            self.resume = Event()
            self.stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, seconds):
            self.waiting.set()
            assert self.resume.wait(10)
            self.resume.clear()
            return self.stopped

    # UTF-8 (_END_CODECS): baseline marker ignored, later marker stops before trailing bytes.
    utf8_live = tmp_path / "utf8_live.log"
    utf8_live.write_bytes(b"old\nEND\nmore\n")
    poll = Polls()
    with ThreadPoolExecutor(1) as pool:
        fut = pool.submit(
            TailSource(utf8_live, end_marker="END", chunk_size=65536).run,
            Flow("utf8_live"),
            archive_dir=tmp_path,
            stop_event=poll,
        )
        try:
            assert poll.waiting.wait(5)
            with utf8_live.open("ab") as fh:
                fh.write(b"mid\nEND\nignored\n")
            poll.resume.set()
            res = fut.result(5)
        finally:
            poll.stopped = True
            poll.resume.set()
    assert res.complete and res.reason == "end_marker"
    assert _lines_from(res) == ["old", "END", "more", "mid", "END"]
    assert res.snapshot["source_descriptor"]["end_offset"] == len(b"old\nEND\nmore\nmid\nEND\n")

    # Non-_END_CODECS (cp950) single chunk with baseline + trailing text after marker.
    cp950_log = tmp_path / "cp950.log"
    cp950_log.write_bytes("測試\n".encode("cp950"))
    poll2 = Polls()
    with ThreadPoolExecutor(1) as pool:
        fut2 = pool.submit(
            TailSource(cp950_log, encoding="cp950", end_marker="END", chunk_size=65536).run,
            Flow("cp950"),
            archive_dir=tmp_path,
            stop_event=poll2,
        )
        try:
            assert poll2.waiting.wait(5)
            with cp950_log.open("ab") as fh:
                fh.write("END\n忽略\n".encode("cp950"))
            poll2.resume.set()
            res_cp = fut2.result(5)
        finally:
            poll2.stopped = True
            poll2.resume.set()
    assert res_cp.complete and res_cp.reason == "end_marker"
    assert _lines_from(res_cp) == ["測試", "END"]
    assert res_cp.snapshot["source_descriptor"]["end_offset"] == len("測試\nEND\n".encode("cp950"))

    # UTF-16 with lone CR and single-chunk delivery after baseline.
    u16_log = tmp_path / "u16.log"
    u16_log.write_bytes("one\r".encode("utf-16"))
    poll3 = Polls()
    with ThreadPoolExecutor(1) as pool:
        fut3 = pool.submit(
            TailSource(u16_log, encoding="utf-16", end_marker="END", chunk_size=65536).run,
            Flow("u16"),
            archive_dir=tmp_path,
            stop_event=poll3,
        )
        try:
            assert poll3.waiting.wait(5)
            with u16_log.open("ab") as fh:
                fh.write("END\r後".encode("utf-16-le" if sys.byteorder == "little" else "utf-16-be"))
            poll3.resume.set()
            res_u16 = fut3.result(5)
        finally:
            poll3.stopped = True
            poll3.resume.set()
    assert res_u16.complete and res_u16.reason == "end_marker"
    assert _lines_from(res_u16) == ["one", "END"]
    assert res_u16.snapshot["source_descriptor"]["end_offset"] == len("one\rEND\r".encode("utf-16"))


def test_wb10_unknown_run_id_returns_404_and_files_uses_opened_archive_parent(tmp_path):
    opened_dir = tmp_path / "opened_archives"
    opened_dir.mkdir()
    saved_path = opened_dir / ("a" * 32 + ".sqlite3")
    with RunArchive(saved_path, create=True, run_id="a" * 32, source_path=None):
        pass

    live_dir = tmp_path / "live_archives"
    server = WaveServer(WaveWorkspace(archive_dir=live_dir), archive_path=saved_path)
    server._token = "tok"
    try:
        status, body = _invoke_get(
            server,
            f"/api/archive?workspace_id={server._workspace_id}&run_id={'0' * 32}&job_id=j&attempt_id=a",
        )
        assert status == 404
        assert "not found" in body["error"].lower()

        fq = urllib.parse.urlencode({"path": str(tmp_path)})
        f_status, f_body = _invoke_get(server, f"/api/files?{fq}")
        assert f_status == 200
        archives_shortcut = next(s for s in f_body["shortcuts"] if s["id"] == "archives")
        assert archives_shortcut["path"] == str(opened_dir.resolve())
    finally:
        server.workspace.close()


def test_wb11_archive_apis_expanduser_tilde(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    arch_dir = fake_home / "arch"
    arch_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    run_id = "f" * 32
    with RunArchive(f"~/arch/{run_id}.sqlite3", create=True, run_id=run_id) as store:
        expected = (arch_dir / f"{run_id}.sqlite3").resolve()
        assert store.path == expected

    assert find_archive_by_run_id("~/arch", run_id) == expected
    rows = RunArchive.catalog("~/arch", include_all=True)["rows"]
    assert [row["run_id"] for row in rows] == [run_id]


def test_wb12_workspace_add_many_accepts_flow():
    ws = WaveWorkspace()
    try:
        flow = Flow("batch")
        j1 = Job("j1", [sys.executable, "-c", "pass"])
        j2 = Job("j2", [sys.executable, "-c", "pass"])
        added = ws.add_many([j1, j2], flow=flow)
        assert added == [j1, j2]
        assert ws.flow_for(j1) is flow
        assert ws.flow_for(j2) is flow

        with pytest.raises(TypeError, match="flow"):
            ws.add_many([], flow="not-a-flow")
    finally:
        ws.close()


def test_cc06_wave_module_entrypoint_help_and_import():
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=str(root))
    proc = subprocess.run(
        [sys.executable, "-m", "rpkbin.wave", "--help"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        encoding="utf-8",
    )
    assert proc.returncode == 0
    assert "rpk-wave" in proc.stdout
    assert "RuntimeWarning" not in proc.stderr

    mod = importlib.import_module("rpkbin.wave.__main__")
    assert hasattr(mod, "main")


def test_cc08_reanalyze_attempt_flag_and_alias_and_error_message(tmp_path, capsys):
    log = tmp_path / "in.log"
    log.write_bytes(b"hello\n")
    original = FileSource(log).run(Flow("orig"), archive_dir=tmp_path / "orig")
    flow_py = tmp_path / "flow.py"
    flow_py.write_text("from rpkbin.flow import Flow\nflow = Flow('re')\n", encoding="utf-8")

    with pytest.raises(SystemExit) as help_exc:
        cli.main(["reanalyze", "--help"])
    assert help_exc.value.code == 0
    help_out = capsys.readouterr().out
    assert "--attempt" in help_out and "--run" in help_out

    attempt_id = original.snapshot["attempt_id"]
    out1 = tmp_path / "out_attempt"
    assert cli.main([
        "reanalyze", str(original.archive_path), "--flow", str(flow_py),
        "--attempt", attempt_id, "--headless", "--archive-dir", str(out1),
    ]) == 0

    out2 = tmp_path / "out_run_alias"
    assert cli.main([
        "reanalyze", str(original.archive_path), "--flow", str(flow_py),
        "--run", attempt_id, "--headless", "--archive-dir", str(out2),
    ]) == 0

    assert cli.main([
        "reanalyze", str(original.archive_path), "--flow", str(flow_py),
        "--attempt", "nonexistent", "--headless", "--archive-dir", str(tmp_path / "out_missing"),
    ]) == 2
    assert "Attempt (attempt_id) not found in this archive" in capsys.readouterr().err


def test_cc11_cli_version_flag(capsys):
    for flag in ("--version", "-V"):
        with pytest.raises(SystemExit) as exc:
            cli.main([flag])
        assert exc.value.code == 0
        assert f"rpk-wave {__version__}" in capsys.readouterr().out


def test_cc12_context_managers_and_repr(tmp_path):
    with WaveWorkspace(archive_dir=tmp_path / "ws_arch") as ws:
        r_open = repr(ws)
        assert "WaveWorkspace(" in r_open and "preview" in r_open and ws.run_id[:8] in r_open
    assert ws.manager._closed
    assert "closed" in repr(ws)

    ok_path = tmp_path / "ok.sqlite3"
    with RunArchive(ok_path, create=True) as ok_store:
        assert "RunArchive(" in repr(ok_store) and "open" in repr(ok_store)
    assert ok_store._closed and "closed" in repr(ok_store)
    with RunArchive(ok_path) as check_ok:
        assert check_ok.health["closed_cleanly"] is True

    err_path = tmp_path / "err.sqlite3"
    with pytest.raises(RuntimeError, match="abort"):
        with RunArchive(err_path, create=True) as err_store:
            raise RuntimeError("abort")
    assert err_store._closed
    with RunArchive(err_path) as check_err:
        assert check_err.health["closed_cleanly"] is False


def test_cc14_ensure_private_dir_falls_back_on_symlink_or_foreign_uid(tmp_path, monkeypatch):
    parent = tmp_path / "tmp"
    parent.mkdir()
    target = tmp_path / "other"
    target.mkdir()
    sus = parent / "rpk-wave-uid1000"
    sus.mkdir()

    # Foreign UID on POSIX triggers mkdtemp fallback inside parent.
    real_lstat = os.lstat

    def fake_lstat(p):
        if Path(p) == sus:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=9999)
        return real_lstat(p)

    fake_os = SimpleNamespace(**vars(os))
    fake_os.name = "posix"
    fake_os.getuid = lambda: 1000
    fake_os.lstat = fake_lstat
    fake_os.chmod = lambda *a, **kw: None
    monkeypatch.setattr(archive_mod, "os", fake_os)

    chosen = archive_mod._ensure_private_dir(sus)
    assert chosen != sus.resolve()
    assert chosen.parent == parent.resolve()
    assert chosen.name.startswith("rpk-wave-uid1000-")
    assert chosen.is_dir()

    # Symlink rejection fallback (mocked is_symlink so test runs regardless of Windows privileges).
    sus_link = parent / "rpk-wave-uid1001"
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == sus_link)
    chosen_link = archive_mod._ensure_private_dir(sus_link)
    assert chosen_link != sus_link
    assert chosen_link.parent == parent.resolve()
    assert chosen_link.name.startswith("rpk-wave-uid1001-")

    # WaveWorkspace staging adopts the returned mkdtemp directory rather than the compromised sus path.
    monkeypatch.delenv("RPK_WAVE_TEMP_DIR", raising=False)
    monkeypatch.setattr("rpkbin.wave.workspace._ensure_private_dir", archive_mod._ensure_private_dir)
    monkeypatch.setattr(archive_mod, "default_temp_dir", lambda: sus)
    with WaveWorkspace(archive_mode="ephemeral", archive_dir=tmp_path / "arch") as ws:
        ws.enable_archive()
        assert ws.archive is not None
        assert ws.archive.path.parent != sus.resolve()
        assert ws.archive.path.parent.parent == parent.resolve()
        assert ws.archive.path.parent.name.startswith("rpk-wave-uid1000-")
