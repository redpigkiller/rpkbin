"""Phase 4 regression tests for rpkbin.app.isasim_gui."""

from __future__ import annotations

import email.message
import io
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from rpkbin import __version__
from rpkbin.app.isasim_gui.cli import build_parser, main
from rpkbin.app.isasim_gui.server import DebuggerRequestHandler, _exec
from rpkbin.app.isasim_gui.session import (
    DebugSession,
    parse_memory_filter,
)

ROOT = Path(__file__).resolve().parents[2]
TOY_A = ROOT / "tests" / "isasim" / "toys" / "toy_a.py"


def _write_asm(tmp_path: Path, text: str, name: str = "prog.asm") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _invoke_handler(
    session: DebugSession,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""

    class _DummyServer:
        server_port = 8000
        token = "test-token"
        action_lock = threading.Lock()

        def __init__(self, sess: DebugSession) -> None:
            self.session = sess

    responses: list[tuple[int, dict[str, object]]] = []
    handler = object.__new__(DebuggerRequestHandler)
    handler.server = _DummyServer(session)
    handler.client_address = ("127.0.0.1", 50000)
    handler.path = path
    headers = email.message.Message()
    headers["Host"] = "127.0.0.1:8000"
    headers["Origin"] = "http://127.0.0.1:8000"
    if method == "POST":
        headers["X-Isasim-Token"] = "test-token"
        headers["Content-Length"] = str(len(body))
    handler.headers = headers
    handler.rfile = io.BytesIO(body)
    handler._json = lambda data, status=200: responses.append((status, data))
    if method == "GET":
        handler.do_GET()
    else:
        handler.do_POST()
    assert len(responses) == 1
    return responses[0]


def test_u7_01_python_m_exits_nonzero_on_error(tmp_path: Path):
    asm = _write_asm(tmp_path, "HLT\n")
    missing_mcu = tmp_path / "missing_mcu.py"
    proc = subprocess.run(
        [sys.executable, "-m", "rpkbin.app.isasim_gui", str(missing_mcu), str(asm)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 2
    assert "MCUDefinitionError" in proc.stderr


def test_u7_02_cli_catches_startup_and_bind_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    bad_asm = _write_asm(tmp_path, "NOT_AN_INSTR\n", "bad.asm")
    assert main([str(TOY_A), str(bad_asm)]) == 2
    assert "AssemblyError" in capsys.readouterr().err

    good_asm = _write_asm(tmp_path, "HLT\n", "good.asm")
    missing_script = tmp_path / "no_such_setup.py"
    assert main([str(TOY_A), str(good_asm), "--script", str(missing_script)]) == 2
    assert "FileNotFoundError" in capsys.readouterr().err

    no_setup_script = tmp_path / "no_setup.py"
    no_setup_script.write_text("x = 1\n", encoding="utf-8")
    assert main([str(TOY_A), str(good_asm), "--script", str(no_setup_script)]) == 2
    assert "UsageError" in capsys.readouterr().err

    bad_syntax_script = tmp_path / "bad_syntax.py"
    bad_syntax_script.write_text("def setup(sim):\n    if True print(1)\n", encoding="utf-8")
    assert main([str(TOY_A), str(good_asm), "--script", str(bad_syntax_script)]) == 2
    assert "SyntaxError" in capsys.readouterr().err

    def fail_bind(*args, **kwargs):
        raise OSError("address already in use")

    monkeypatch.setattr("rpkbin.app.isasim_gui.cli.create_server", fail_bind)
    assert main([str(TOY_A), str(good_asm)]) == 2
    assert "OSError" in capsys.readouterr().err


def test_u7_03_step_back_and_nonwriting_step_diffs_and_trace(tmp_path: Path):
    asm = _write_asm(
        tmp_path,
        "LDI r0, 5\nLDI r1, 10\nST r0, [0x04]\nJMP next\nnext:\nHLT\n",
    )
    session = DebugSession(TOY_A, asm)
    session.set_memory_view(memory_space="data", hide_uninit=False)

    # Step 1: LDI r0, 5
    st1 = session.step_in()
    r0_1 = next(r for r in st1["registers"] if r["name"] == "r0")
    assert r0_1["changed"] is True
    assert r0_1["old_hex"] == "0x0000"

    # Step 2: LDI r1, 10 -> r0 should no longer be marked changed
    st2 = session.step_in()
    r0_2 = next(r for r in st2["registers"] if r["name"] == "r0")
    r1_2 = next(r for r in st2["registers"] if r["name"] == "r1")
    assert r0_2["changed"] is False
    assert r0_2["old_hex"] is None
    assert r1_2["changed"] is True
    assert r1_2["old_hex"] == "0x0000"

    # Step 3: ST r0, [0x04]
    st3 = session.step_in()
    r1_3 = next(r for r in st3["registers"] if r["name"] == "r1")
    assert r1_3["changed"] is False
    assert st3["memory"]["cells"][4]["changed"] is True

    # Step 4: JMP next (non-writing step) -> clears previous reg & mem highlights
    st4 = session.step_in()
    assert not any(r["changed"] for r in st4["registers"])
    assert not any(c["changed"] for c in st4["memory"]["cells"])
    trace_len_at_3 = len(st3["trace"])

    # Step back from JMP -> restores trace before JMP, leaves memory unhighlighted
    st_b4 = session.step_back()
    assert len(st_b4["trace"]) == trace_len_at_3
    assert not any(ev["text"] == "restore" for ev in st_b4["trace"])
    assert not any(c["changed"] for c in st_b4["memory"]["cells"])

    # Step back from ST -> cell 4 goes back to uninitialized, trace removes ST & restore
    st_b3 = session.step_back()
    assert st_b3["memory"]["cells"][4]["initialized"] is False
    assert not any(ev["text"] == "restore" for ev in st_b3["trace"])
    assert all("ST" not in ev["text"] for ev in st_b3["trace"])

    # Step back from LDI r1, 10 -> old_hex must be 0x000a (not 0x0000!), trace has no LDI r1
    st_b2 = session.step_back()
    r1_b2 = next(r for r in st_b2["registers"] if r["name"] == "r1")
    assert r1_b2["value"] == 0
    assert r1_b2["changed"] is True
    assert r1_b2["old_hex"] == "0x000a"
    assert r1_b2["hex"] == "0x0000"
    assert all("LDI r1" not in ev["text"] for ev in st_b2["trace"])
    assert not any(ev["text"] == "restore" for ev in st_b2["trace"])


def test_u7_04_advance_catches_watchpoint_debug_error_and_continue_preserves_contract(
    tmp_path: Path,
):
    asm = _write_asm(tmp_path, "LDI r0, 5\nST r0, [0]\nHLT\n")
    session = DebugSession(TOY_A, asm, call_mnemonics=("JMP",))
    session.add_watchpoint("data[0]", kind="w", condition="1 // r1 == 0")
    session.step_in()  # advance to ST r0, [0]
    st = session.step_over()
    assert st["status"]["stop_reason"] == "debug_error"
    assert "DebugError" in (st["status"]["error"] or "")

    # Partial continue_run raising DebugError still records progress for /api/state refresh
    session.reset()
    session.clear_watchpoints()
    session.toggle_breakpoint(session._program.main, 2, condition="1 // r1 == 0")
    from rpkbin.isasim import DebugError

    with pytest.raises(DebugError):
        session.continue_run()
    refreshed = session.get_state()
    assert refreshed["status"]["steps"] == 1
    assert refreshed["status"]["pc"] == 1
    assert refreshed["status"]["can_step_back"] is True


def test_u7_08_stable_program_path_across_include(tmp_path: Path):
    inc = tmp_path / "sub.asm"
    inc.write_text("LDI r1, 7\n", encoding="utf-8")
    main_asm = _write_asm(tmp_path, 'LDI r0, 5\n.include "sub.asm"\nHLT\n', "main.asm")
    session = DebugSession(TOY_A, main_asm)
    st0 = session.get_state()
    st1 = session.step_in()
    assert st0["status"]["active_file_path"] != st1["status"]["active_file_path"]
    assert st0["status"]["program_path"] == str(main_asm.resolve())
    assert st1["status"]["program_path"] == st0["status"]["program_path"]


def test_u7_09_strict_exec_validation_and_ordering(tmp_path: Path):
    asm = _write_asm(tmp_path, "LDI r0, 1\nLDI r1, 2\nHLT\n")
    session = DebugSession(TOY_A, asm)
    session.set_memory_view(memory_space="data", memory_filter="0x00", hide_uninit=False)

    # Reject bool/float line numbers
    for bad_line in (True, False, 2.9, "2.5"):
        with pytest.raises(ValueError, match="integer"):
            _exec(session, "set_pc", {"line": bad_line, "space": "prog", "hide_uninit": True})
        with pytest.raises(ValueError, match="integer"):
            _exec(session, "run_to_cursor", {"line": bad_line, "space": "prog"})
        with pytest.raises(ValueError, match="integer"):
            _exec(session, "breakpoint", {"line": bad_line, "space": "prog"})

    # Failed validation must NOT mutate session memory view
    assert session._memory_space == "data"
    assert session._memory_filter == "0x00"
    assert session._hide_uninit is False

    # Unknown action must NOT mutate session memory view
    with pytest.raises(ValueError, match="Unknown action"):
        _exec(session, "nope", {"space": "prog", "filter": "1", "hide_uninit": True})
    assert session._memory_space == "data"
    assert session._memory_filter == "0x00"
    assert session._hide_uninit is False

    # hide_uninit string "false" must parse as False, invalid string rejected
    st = _exec(session, "poke", {"target": "r0", "value": "3", "hide_uninit": "false"})
    assert session._hide_uninit is False
    assert st["registers"][1]["value"] == 3
    with pytest.raises(ValueError, match="boolean"):
        _exec(session, "poke", {"target": "r0", "value": "4", "hide_uninit": "maybe"})
    with pytest.raises(ValueError, match="boolean"):
        session.set_memory_view(hide_uninit="false")  # type: ignore[arg-type]

    # HTTP handler returns 400 cleanly
    status, err = _invoke_handler(session, "POST", "/api/set_pc", {"line": True})
    assert status == 400
    assert "detail" in err


def test_u7_11_no_history_on_zero_step_undefined_opcode(tmp_path: Path):
    asm = _write_asm(tmp_path, ".cell 0xEEEE\n")
    session = DebugSession(TOY_A, asm)
    st = session.step_in()
    assert st["status"]["stop_reason"] == "undefined_opcode"
    assert st["status"]["steps"] == 0
    assert st["status"]["can_step_back"] is False
    assert len(session._history) == 0

    # At max_history=1, hitting undefined_opcode after 1 valid step keeps prior snapshot intact
    asm2 = _write_asm(tmp_path, "LDI r0, 1\n.cell 0xEEEE\n", "prog2.asm")
    s2 = DebugSession(TOY_A, asm2, max_history=1)
    assert s2.step_in()["status"]["steps"] == 1
    assert s2.step_in()["status"]["stop_reason"] == "undefined_opcode"
    assert s2.step_in()["status"]["steps"] == 1
    assert s2.step_back()["status"]["pc"] == 0


def test_u7_13_get_catches_isasim_error_and_watchpoint_normalizes_empty_cond(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    asm = _write_asm(tmp_path, "LDI r0, 1\nHLT\n")
    session = DebugSession(TOY_A, asm)

    st = session.add_watchpoint("r0", kind="w", condition="")
    assert st["watchpoints"][0]["condition"] is None

    from rpkbin.isasim import StateAccessError

    def boom(**kwargs):
        raise StateAccessError("bad state")

    monkeypatch.setattr(session, "view_state", boom)
    status, body = _invoke_handler(session, "GET", "/api/state")
    assert status == 400
    assert "bad state" in body["detail"]


def test_u7_14_memory_filter_reports_invalid_tokens(tmp_path: Path):
    from rpkbin.app.isasim_gui.session import parse_memory_filter_details

    addrs, errs = parse_memory_filter_details("0x02, 0xZZ, 10.., 0x20..0x10, -5, 0x04..0x05", 64)
    assert addrs == {2, 4, 5}
    assert errs == ["0xZZ", "10..", "0x20..0x10", "-5"]
    assert parse_memory_filter("0x02, 0xZZ, 10.., 0x04..0x05", 64) == {2, 4, 5}

    asm = _write_asm(tmp_path, "HLT\n")
    session = DebugSession(TOY_A, asm)
    st = session.get_state(memory_space="data", memory_filter="0xZZ, 0x20..0x10")
    assert st["memory"]["filter_errors"] == ["0xZZ", "0x20..0x10"]


def test_u7_15_and_cc07_session_ergonomics_and_cli_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    asm = _write_asm(tmp_path, "HLT\n")
    session = DebugSession(TOY_A, asm)
    rep = repr(session)
    assert "DebugSession(" in rep
    assert "prog.asm" in rep

    # view_state has explicit typed parameters
    import inspect

    sig = inspect.signature(DebugSession.view_state)
    assert list(sig.parameters) == ["self", "memory_space", "memory_filter", "hide_uninit"]

    parser = build_parser()
    args = parser.parse_args(["mcu.py", "prog.asm", "--host", "localhost", "-p", "8123", "--open"])
    assert args.host == "localhost"
    assert args.port == 8123
    assert args.open is True
    assert args.no_browser is False

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out

    # --open and --no-browser conflict
    with pytest.raises(SystemExit) as exc_conflict:
        main([str(TOY_A), str(asm), "--open", "--no-browser"])
    assert exc_conflict.value.code == 2

    # --host localhost binds 127.0.0.1 safely and opens http://localhost:<port>
    opened: list[str] = []
    bound: list[tuple[str, int]] = []

    class _FakeServer:
        server_port = 8123

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            pass

    def fake_create_server(sess, *, host="127.0.0.1", port=8000):
        bound.append((host, port))
        return _FakeServer()

    monkeypatch.setattr("rpkbin.app.isasim_gui.cli.create_server", fake_create_server)
    monkeypatch.setattr("rpkbin.app.isasim_gui.cli.webbrowser.open", opened.append)
    assert main([str(TOY_A), str(asm), "--host", "localhost", "-p", "8123", "--open"]) == 0
    assert bound == [("127.0.0.1", 8123)]
    assert opened == ["http://localhost:8123"]
