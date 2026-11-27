"""Comprehensive tests for rpkbin.app.isasim_gui."""

from __future__ import annotations

from pathlib import Path
import json
import re
import threading
import urllib.error
import urllib.request

import pytest

from rpkbin.isasim import DebugError
from rpkbin.app.isasim_gui.session import (
    DebugSession,
    parse_memory_filter,
    relocate_breakpoints,
)

ROOT = Path(__file__).resolve().parents[2]
TOY_A = ROOT / "tests" / "isasim" / "toys" / "toy_a.py"
TOY_B = ROOT / "tests" / "isasim" / "toys" / "toy_b.py"


def test_relocate_breakpoints_basic():
    old_lines = [
        "; header",
        "LDI r0, 1",
        "LDI r1, 2",
        "ADD r0, r1",
        "HLT",
    ]
    # Breakpoint on line 4 (ADD r0, r1)
    bps = {4: "r.r0 == 1"}

    # Case 1: Same lines, stays on line 4
    reloc = relocate_breakpoints(old_lines, old_lines, bps)
    assert reloc == {4: "r.r0 == 1"}

    # Case 2: Insert 2 comment lines at the beginning, moves to line 6
    new_lines = [
        "; comment 1",
        "; comment 2",
        "; header",
        "LDI r0, 1",
        "LDI r1, 2",
        "ADD r0, r1",
        "HLT",
    ]
    reloc = relocate_breakpoints(old_lines, new_lines, bps)
    assert reloc == {6: "r.r0 == 1"}

    # Case 3: Completely modified lines, dropped
    new_lines_diff = [
        "SUB r0, r1",
        "SUB r1, r2",
        "JMP 0",
    ]
    reloc = relocate_breakpoints(old_lines, new_lines_diff, bps)
    assert reloc == {}

    # Case 4: Ambiguous multiple matches (when old_line no longer matches), dropped
    repeated_pattern = [
        "LDI r0, 1",
        "LDI r1, 2",
        "ADD r0, r1",
        "ST r0, [0]",
        "HLT",
    ]
    old_with_padding = ["; pad1", "; pad2"] + repeated_pattern + ["; pad3", "; pad4"]
    bps_pattern = {5: "r.r0 == 1"}
    ambiguous_lines = [
        "; mod1", "; mod2", "; mod3", "; mod4", "; mod5",
        *repeated_pattern,
        *repeated_pattern,
    ]
    reloc = relocate_breakpoints(old_with_padding, ambiguous_lines, bps_pattern)
    assert reloc == {}


def test_parse_memory_filter():
    assert parse_memory_filter("", 64) is None
    assert parse_memory_filter("   ", 64) is None

    # Single address (hex, dec, binary)
    assert parse_memory_filter("0x04", 64) == {4}
    assert parse_memory_filter("4", 64) == {4}
    assert parse_memory_filter("0b101", 64) == {5}

    # Range .. and -
    assert parse_memory_filter("0x10..0x12", 64) == {16, 17, 18}
    assert parse_memory_filter("10-12", 64) == {10, 11, 12}

    # Slice base:length
    assert parse_memory_filter("0x20:3", 64) == {32, 33, 34}

    # Combination
    combined = parse_memory_filter("0x01, 0x04..0x06, 0x10:3", 64)
    assert combined == {1, 4, 5, 6, 16, 17, 18}

    # Malformed / incomplete input resilience
    assert parse_memory_filter("0xZZ", 64) == set()
    assert parse_memory_filter("10..", 64) == set()
    assert parse_memory_filter("0x02, 0xZZ, 10.., 0x04..0x05", 64) == {2, 4, 5}


def test_session_toy_a_steps_and_poke(tmp_path: Path):
    asm_file = tmp_path / "prog.asm"
    asm_file.write_text(
        """LDI r0, 5
LDI r1, 10
ADD r0, r1
ST r0, [0x04]
HLT
""",
        encoding="utf-8",
    )

    session = DebugSession(TOY_A, asm_file)
    st = session.get_state()
    assert st["status"]["stop_reason"] == "ready"
    assert st["status"]["pc"] == 0
    assert not st["status"]["can_step_back"]

    # Step in
    st = session.step_in()
    assert st["status"]["stop_reason"] == "step_done"
    assert st["status"]["can_step_back"]
    # r0 should be 5
    r0 = next(r for r in st["registers"] if r["name"] == "r0")
    assert r0["value"] == 5
    assert r0["changed"] is True

    # Step back
    st = session.step_back()
    assert st["status"]["stop_reason"] == "step_back"
    r0 = next(r for r in st["registers"] if r["name"] == "r0")
    assert r0["value"] == 0

    # Poke register (hex and binary)
    st = session.poke("r0", "0x20")
    r0 = next(r for r in st["registers"] if r["name"] == "r0")
    assert r0["value"] == 32

    st = session.poke("r0", "0b1010")
    r0 = next(r for r in st["registers"] if r["name"] == "r0")
    assert r0["value"] == 10

    # Poke memory
    session.poke("data[0x04]", 99)
    st = session.get_state(memory_space="data")
    cell = next(c for c in st["memory"]["cells"] if c["addr"] == 4)
    assert cell["value"] == 99
    assert cell["initialized"] is True


def test_session_toy_a_breakpoints_and_cursor(tmp_path: Path):
    asm_file = tmp_path / "prog.asm"
    asm_file.write_text(
        """LDI r0, 5
LDI r1, 10
ADD r0, r1
HLT
""",
        encoding="utf-8",
    )

    session = DebugSession(TOY_A, asm_file)

    main_key = asm_file.resolve().as_posix()
    # Add unconditional breakpoint at line 3 (ADD r0, r1)
    st = session.toggle_breakpoint(main_key, 3)
    row = next(r for r in st["files"][main_key] if r["line"] == 3)
    assert row["bp_cond"] == ""

    # Continue run
    st = session.continue_run()
    assert st["status"]["stop_reason"] == "breakpoint"
    assert st["status"]["pc"] == 2  # Address of ADD r0, r1 (line 3)

    # Reset
    session.reset()
    assert session.sim.state.pc == 0

    # Bare conditional breakpoint (r0 == 5 rewritten transparently to r.r0 == 5)
    session.clear_breakpoints()
    session.toggle_breakpoint(main_key, 3, condition="r0 == 5")
    st = session.continue_run()
    assert st["status"]["stop_reason"] == "breakpoint"
    assert st["status"]["pc"] == 2

    # Invalid syntax conditional breakpoint raises error
    with pytest.raises(Exception):
        session.toggle_breakpoint(main_key, 3, condition="r0 ==")

    # Set PC
    st = session.set_pc(main_key, 4)  # HLT
    assert st["status"]["pc"] == 3


def test_session_absolute_source_key(tmp_path: Path):
    asm_file = (tmp_path / "absolute program.asm").resolve()
    asm_file.write_text("LDI r0, 5\nvalue: .cell 0\nHLT\n", encoding="utf-8")
    session = DebugSession(TOY_A, asm_file)
    main_key = asm_file.as_posix()

    st = session.get_state()
    assert st["status"]["active_file"] == main_key
    assert list(st["files"]) == [main_key]
    instruction, data, halt = st["files"][main_key]
    assert instruction["is_pc"]
    assert (data["addr"], data["is_instr"]) == (1, False)
    assert halt["text"] == "HLT"
    with pytest.raises(ValueError, match="No instruction"):
        session.set_pc(main_key, 2)

    st = session.set_pc(main_key, 3)
    assert st["status"]["pc"] == 2
    assert st["status"]["active_line"] == 3
    assert st["files"][main_key][2]["is_pc"]


def test_session_include_source_switching(tmp_path: Path):
    main = tmp_path / "program's.asm"
    include = tmp_path / "sub" / main.name
    include.parent.mkdir()
    main.write_text(
        'LDI r0, 5\n.include "sub/program\'s.asm"\nHLT\n', encoding="utf-8"
    )
    include.write_text("LDI r1, 7\n", encoding="utf-8")
    session = DebugSession(TOY_A, main)
    main_key = main.resolve().as_posix()
    include_key = "sub/program's.asm"

    st = session.get_state()
    assert set(st["files"]) == {main_key, include_key}
    assert st["status"]["active_file"] == main_key
    assert (st["files"][main_key][0]["text"], st["files"][main_key][0]["addr"]) == (
        "LDI r0, 5", 0
    )
    assert (st["files"][include_key][0]["text"], st["files"][include_key][0]["addr"]) == (
        "LDI r1, 7", 1
    )

    st = session.step_in()
    assert st["status"]["active_file"] == include_key
    assert st["files"][include_key][0]["is_pc"]
    assert st["trace"][-1]["file"] == main_key
    st = session.step_in()
    assert st["status"]["active_file"] == main_key
    assert st["status"]["active_line"] == 3
    assert next(t for t in st["trace"] if t["pc"] == 1)["file"] == include_key

    st = session.set_pc(include_key, 1)
    assert st["status"]["active_file"] == include_key
    assert st["status"]["pc"] == 1
    st = session.set_pc(main_key, 1)
    assert st["status"]["active_file"] == main_key
    assert st["status"]["pc"] == 0

    st = session.run_to_cursor(include_key, 1)
    assert st["status"]["stop_reason"] == "breakpoint"
    assert st["status"]["active_file"] == include_key


def test_session_toy_b_step_over_and_step_out(tmp_path: Path):
    asm_file = tmp_path / "call_ret.asm"
    asm_file.write_text(
        """CALL sub
HLT
sub:
SET 0
RET
""",
        encoding="utf-8",
    )

    # 1. Test step_over on CALL: executes the entire subroutine in 1 step_over
    session = DebugSession(
        TOY_B, asm_file, call_mnemonics=("CALL",), return_mnemonics=("RET",)
    )
    assert session.sim.state.pc == 0
    session.step_over()
    # Should have executed CALL sub -> SET 0 -> RET, and stopped at HLT (PC=2)
    assert session.sim.state.pc == 2
    # Check outp[0] was set to 1
    assert session.sim._memory["outp"][0] == 1

    # 2. Test step_out: step into CALL, then step_out should finish subroutine and land at HLT
    session.reset()
    assert session.sim.state.pc == 0
    session.step_in()  # executes CALL sub -> now PC is at sub (PC=3)
    assert session.sim.state.pc == 3
    session.step_out()
    assert session.sim.state.pc == 2  # right after CALL
    assert session.sim._memory["outp"][0] == 1


def test_session_reload_and_errors(tmp_path: Path):
    asm_file = tmp_path / "test.asm"
    asm_file.write_text(
        """LDI r0, 1
LDI r1, 2
ADD r0, r1
HLT
""",
        encoding="utf-8",
    )

    session = DebugSession(TOY_A, asm_file)
    main_key = asm_file.resolve().as_posix()
    session.toggle_breakpoint(main_key, 3)  # ADD r0, r1

    # Shift lines in file
    asm_file.write_text(
        """; Added comment
; Another comment
LDI r0, 1
LDI r1, 2
ADD r0, r1
HLT
""",
        encoding="utf-8",
    )
    st = session.reload()
    assert st["status"]["error"] is None
    # Breakpoint should now be relocated to line 5
    row = next(r for r in st["files"][main_key] if r["line"] == 5)
    assert row["bp_cond"] == ""

    # Reload with assembly error: should record error and not crash session
    asm_file.write_text("INVALID_OPCODE_SYNTAX !!!", encoding="utf-8")
    st = session.reload()
    assert st["status"]["error"] is not None

    # Subsequent step clears stale error
    session.step_in()
    assert session.get_state()["status"]["error"] is None


def test_standard_library_server_endpoints(tmp_path: Path):
    from rpkbin.app.isasim_gui.server import create_server

    asm_file = tmp_path / "web_test.asm"
    asm_file.write_text(
        """LDI r0, 7
LDI r1, 3
ADD r0, r1
HLT
""",
        encoding="utf-8",
    )

    session = DebugSession(TOY_A, asm_file)
    with pytest.raises(ValueError, match="loopback"):
        create_server(session, host="0.0.0.0", port=0)
    server = create_server(session, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    auth_headers = {}

    def request(
        path: str,
        payload: object | None = None,
        *,
        headers: dict | None = None,
        method: str | None = None,
    ):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        verb = method or ("POST" if payload is not None else "GET")
        req = urllib.request.Request(
            base_url + path,
            data=data,
            headers={
                "Content-Type": "application/json",
                **(auth_headers if verb == "POST" else {}),
                **(headers or {}),
            },
            method=verb,
        )
        try:
            with urllib.request.urlopen(req) as response:
                body = response.read().decode("utf-8")
                return response.status, body, json.loads(body) if "json" in response.headers.get("Content-Type", "") else None
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            return exc.code, body, json.loads(body)

    try:
        # GET / (HTML index)
        status, body, _ = request("/")
        assert status == 200
        assert "isasim Web Debugger" in body
        token = re.search(r'<meta name="isasim-token" content="([^"]+)">', body)[1]
        auth_headers.update({"Origin": base_url, "X-Isasim-Token": token})

        # GET /api/state
        status, _, data = request("/api/state")
        assert status == 200
        assert "status" in data
        assert "registers" in data
        assert "memory" in data
        assert "files" in data
        assert request("/api/state", headers={"Origin": base_url})[0] == 200

        # POST /api/step_in
        status, _, data = request("/api/step_in", method="POST")
        assert status == 200
        assert data["status"]["steps"] == 1

        # POST /api/step_back
        status, _, data = request("/api/step_back", method="POST")
        assert status == 200
        assert data["status"]["steps"] == 0

        # POST /api/breakpoint
        status, _, _ = request(
            "/api/breakpoint",
            {"file": asm_file.resolve().as_posix(), "line": 3, "condition": None},
        )
        assert status == 200

        # Invalid condition returns 400
        status, _, _ = request(
            "/api/breakpoint",
            {"file": asm_file.resolve().as_posix(), "line": 3, "condition": "r0 =="},
        )
        assert status == 400

        # POST /api/continue
        status, _, data = request("/api/continue", method="POST")
        assert status == 200
        assert data["status"]["stop_reason"] == "breakpoint"

        # POST /api/clear_breakpoints
        status, _, _ = request("/api/clear_breakpoints", method="POST")
        assert status == 200

        # POST /api/poke
        status, _, data = request("/api/poke", {"target": "r0", "value": "0x42"})
        assert status == 200
        r0 = next(r for r in data["registers"] if r["name"] == "r0")
        assert r0["value"] == 66

        # POST /api/action (generic dispatcher)
        status, _, data = request("/api/action", {"action": "reset"})
        assert status == 200
        assert data["status"]["pc"] == 0

        # Test error handling
        status, _, _ = request("/api/action", {"action": "unknown_action"})
        assert status == 400

        # Test set_pc via action
        status, _, data = request("/api/action", {"action": "set_pc", "line": 4})
        assert status == 200
        assert data["status"]["pc"] == 3

        # Host and Origin checks reject requests aimed at a remote or hostile site.
        status, _, _ = request("/api/state", headers={"Host": "example.com"})
        assert status == 403
        status, _, _ = request(
            "/api/state", headers={"Origin": "https://example.com"}
        )
        assert status == 403
        status, _, _ = request(
            "/api/action",
            {"action": "reset"},
            headers={"Origin": "https://example.com"},
        )
        assert status == 403
        status, _, _ = request(
            "/api/state", headers={"Sec-Fetch-Site": "cross-site"}
        )
        assert status == 403
        status, _, _ = request("/api/action", [1, 2])
        assert status == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_server_action_routes_dispatch_to_session_methods():
    from rpkbin.app.isasim_gui.server import create_server

    class RecordingSession:
        def __init__(self):
            self.calls = []

        def set_memory_view(self, **kwargs):
            pass

        def __getattr__(self, name):
            def record(*args):
                self.calls.append((name, args))
                return {"action": name}

            return record

    session = RecordingSession()
    server = create_server(session, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    auth_headers = {"Origin": base_url, "X-Isasim-Token": server.token}
    routes = [
        ("/api/step_in", {}, "step_in"),
        ("/api/step_over", {}, "step_over"),
        ("/api/step_out", {}, "step_out"),
        ("/api/step_back", {}, "step_back"),
        ("/api/continue", {}, "continue_run"),
        ("/api/run_to_cursor", {"file": "prog.asm", "line": 2}, "run_to_cursor"),
        ("/api/set_pc", {"file": "prog.asm", "line": 2}, "set_pc"),
        ("/api/poke", {"target": "r0", "value": 7}, "poke"),
        ("/api/breakpoint", {"file": "prog.asm", "line": 2}, "toggle_breakpoint"),
        ("/api/remove_watchpoint", {"id": 1}, "remove_watchpoint"),
        ("/api/clear_breakpoints", {}, "clear_breakpoints"),
        ("/api/reset", {}, "reset"),
        ("/api/reload", {}, "reload"),
        ("/api/action", {"action": "reset"}, "reset"),
    ]

    try:
        for path, payload, method_name in routes:
            req = urllib.request.Request(
                base_url + path,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", **auth_headers},
                method="POST",
            )
            with urllib.request.urlopen(req) as response:
                assert response.status == 200
                assert json.loads(response.read()) == {"action": method_name}
            assert session.calls[-1][0] == method_name

        req = urllib.request.Request(
            base_url + "/api/not-a-route", data=b"{}", method="POST", headers=auth_headers
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_cli_parser():
    from rpkbin.app.isasim_gui.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["mcu.py", "prog.asm", "--host", "127.0.0.1", "--port", "9000", "--max-history", "32"])
    assert args.mcu == "mcu.py"
    assert args.program == "prog.asm"
    assert args.host == "127.0.0.1"
    assert args.port == 9000
    assert args.max_history == 32


def test_session_memory_filter_and_hide_uninit(tmp_path: Path):
    asm_file = tmp_path / "mem_test.asm"
    asm_file.write_text(
        """LDI r0, 42
ST r0, [0x05]
HLT
""",
        encoding="utf-8",
    )
    session = DebugSession(TOY_A, asm_file)

    # Set filter and space before stepping
    session.get_state(memory_space="data", memory_filter="0x04..0x06", hide_uninit=True)
    # Memory cell 5 is not initialized yet
    st = session.get_state()
    assert len(st["memory"]["cells"]) == 0

    # Step: LDI r0, 42
    st_step1 = session.step_in()
    # Space and filter must persist across step_in without resetting!
    assert st_step1["memory"]["current_space"] == "data"
    assert len(st_step1["memory"]["cells"]) == 0

    # Step: ST r0, [0x05]
    st_step2 = session.step_in()
    assert st_step2["memory"]["current_space"] == "data"
    cells = st_step2["memory"]["cells"]
    assert len(cells) == 1
    assert cells[0]["addr"] == 5
    assert cells[0]["value"] == 42
    assert cells[0]["initialized"] is True

    # Poking an uninitialized memory cell
    session.poke("data[0x04]", 77)
    st_poke = session.get_state()
    assert len(st_poke["memory"]["cells"]) == 2
    cell4 = next(c for c in st_poke["memory"]["cells"] if c["addr"] == 4)
    assert cell4["value"] == 77
    assert cell4["initialized"] is True


def test_session_step_over_normal_instruction(tmp_path: Path):
    asm_file = tmp_path / "normal.asm"
    asm_file.write_text(
        """LDI r0, 1
LDI r1, 2
HLT
""",
        encoding="utf-8",
    )
    session = DebugSession(TOY_A, asm_file, call_mnemonics=("CALL",))
    # step_over on LDI r0, 1 behaves like step_in
    st = session.step_over()
    assert session.sim.state.pc == 1
    r0 = next(r for r in st["registers"] if r["name"] == "r0")
    assert r0["value"] == 1


def test_session_step_over_and_step_out_with_breakpoint(tmp_path: Path):
    asm_file = tmp_path / "call_bp.asm"
    asm_file.write_text(
        """CALL sub
HLT
sub:
SET 0
RET
""",
        encoding="utf-8",
    )
    # 1. Breakpoint inside subroutine during step_over stops at the breakpoint
    session = DebugSession(
        TOY_B, asm_file, call_mnemonics=("CALL",), return_mnemonics=("RET",)
    )
    main_key = asm_file.resolve().as_posix()
    session.toggle_breakpoint(main_key, 4)  # SET 0
    st = session.step_over()
    assert st["status"]["stop_reason"] == "breakpoint"
    assert session.sim.state.pc == 3  # PC at SET 0

    # 2. Breakpoint at RET during step_out stops at the breakpoint
    session.reset()
    session.clear_breakpoints()
    session.toggle_breakpoint(main_key, 5)  # RET
    session.step_in()  # CALL sub
    st = session.step_out()
    assert st["status"]["stop_reason"] == "breakpoint"
    assert session.sim.state.pc == 4  # PC at RET


def test_remove_watchpoint_session_and_server(tmp_path: Path):
    import email.message
    import io
    from rpkbin.app.isasim_gui.server import DebuggerRequestHandler, _exec
    from rpkbin.isasim import UsageError

    asm_file = tmp_path / "wp.asm"
    asm_file.write_text(
        """LDI r0, 1
ST r0, [0]
ST r0, [1]
HLT
""",
        encoding="utf-8",
    )
    session = DebugSession(TOY_A, asm_file)
    main_key = asm_file.resolve().as_posix()
    session.toggle_breakpoint(main_key, 4)
    bp_id = session._breakpoints[(main_key, 4)].id

    st = session.add_watchpoint("r0", kind="w")
    st = session.add_watchpoint("data[0]", kind="w")
    assert len(st["watchpoints"]) == 2
    wp1, wp2 = st["watchpoints"]

    st = session.remove_watchpoint(wp1["id"])
    assert [wp["id"] for wp in st["watchpoints"]] == [wp2["id"]]

    for bad_id in (True, False, None, "abc", 1.5, 9999, bp_id):
        with pytest.raises(UsageError):
            session.remove_watchpoint(bad_id)  # type: ignore[arg-type]

    for bad_payload in (
        {},
        {"id": None},
        {"id": True},
        {"id": "abc"},
        {"id": 1.5},
        {"id": 9999},
    ):
        with pytest.raises((ValueError, UsageError)):
            _exec(session, "remove_watchpoint", bad_payload)

    def invoke_post(path: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        body = json.dumps(payload).encode("utf-8")

        class _DummyServer:
            server_port = 8000
            token = "test-token"
            action_lock = threading.Lock()

            def __init__(self, sess):
                self.session = sess

        responses: list[tuple[int, dict[str, object]]] = []
        handler = object.__new__(DebuggerRequestHandler)
        handler.server = _DummyServer(session)
        handler.client_address = ("127.0.0.1", 50000)
        handler.path = path
        headers = email.message.Message()
        headers["Host"] = "127.0.0.1:8000"
        headers["Origin"] = "http://127.0.0.1:8000"
        headers["X-Isasim-Token"] = "test-token"
        headers["Content-Length"] = str(len(body))
        handler.headers = headers
        handler.rfile = io.BytesIO(body)
        handler._json = lambda data, status=200: responses.append((status, data))
        handler.do_POST()
        assert len(responses) == 1
        return responses[0]

    status, err = invoke_post("/api/remove_watchpoint", {"id": True})
    assert status == 400
    assert "detail" in err

    status, err = invoke_post("/api/remove_watchpoint", {})
    assert status == 400
    assert "detail" in err

    status, err = invoke_post("/api/action", {"action": "remove_watchpoint", "id": "bad"})
    assert status == 400
    assert "detail" in err

    status, ok = invoke_post("/api/remove_watchpoint", {"id": str(wp2["id"])})
    assert status == 200
    assert ok["watchpoints"] == []


def test_program_reset_clears_dynamic_state_and_reruns_script(tmp_path: Path):
    asm_file = tmp_path / "reset_script.asm"
    asm_file.write_text(
        """LDI r0, 7
ADD r0, r1
ST r0, [0]
HLT
""",
        encoding="utf-8",
    )
    script_file = tmp_path / "setup.py"
    script_file.write_text(
        """class ScriptDevice:
    def read(self, addr, now):
        return 0

    def write(self, addr, value, now):
        pass

    def get_state(self):
        return {}

    def set_state(self, state):
        pass


def setup(sim):
    sim.mem_alias("slot0", "data[0]", bit_order="msb_first")
    sim.attach(ScriptDevice(), space="data", lo=4, hi=5)
    sim.at_cycle(1).set("data[5]", 0x33)
""",
        encoding="utf-8",
    )

    class AdHocDevice:
        def read(self, addr, now):
            return 0

        def write(self, addr, value, now):
            pass

        def get_state(self):
            return {}

        def set_state(self, state):
            pass

    session = DebugSession(TOY_A, asm_file, script_path=script_file)
    assert session._program.script == script_file.resolve()
    assert "slot0" in session.sim.mem_aliases
    assert session.sim._device("data", 4) is not None

    session.sim.mem_alias("ad_hoc", "data[1]", bit_order="msb_first")
    session.sim.attach(AdHocDevice(), space="data", lo=6, hi=7)
    session.sim.at_cycle(1).set("data[2]", 0x22)

    session.step_in()
    session.step_in()
    session.step_in()
    st_mid = session.get_state()
    assert st_mid["trace"]
    assert session._history
    slot0_mid = next(m for m in st_mid["mem_aliases"] if m["name"] == "slot0")
    assert slot0_mid["changed"] is True

    st_reset = session.reset()
    assert "ad_hoc" not in session.sim.mem_aliases
    assert "slot0" in session.sim.mem_aliases
    assert session.sim._device("data", 4) is not None
    assert session.sim._device("data", 6) is None
    assert st_reset["trace"] == []
    assert list(session._history) == []
    slot0_reset = next(m for m in st_reset["mem_aliases"] if m["name"] == "slot0")
    assert slot0_reset["initialized"] is False
    assert slot0_reset["changed"] is False

    # Step 2 cycles without writing data[0]: script stimulus fires (data[5] == 0x33), ad-hoc does not.
    session.step_in()
    session.step_in()
    assert session.sim.read("data[5]") == 0x33
    assert session.sim.initialized_ranges("data") == ((5, 6),)


def test_state_aliases_and_mem_aliases_payload_contract(tmp_path: Path):
    mcu_file = tmp_path / "alias_mcu.py"
    mcu_file.write_text(
        """from rpkbin.isasim import MCU

def make_mcu():
    mcu = MCU("alias_toy")
    mcu.space("prog", cell_bits=16, size=32, access="ro", reset_state="uninitialized", init=0, is_program=True)
    mcu.space("ram", cell_bits=8, size=16, access="rw", reset_state="uninitialized", init=0)
    mcu.space("io", cell_bits=8, size=4, access="rw", reset_state="initialized", init=0)
    mcu.reg("pc", bits=8, reset=0, is_pc=True)
    mcu.reg("r0", bits=8, reset=0)
    mcu.reg("status", bits=4, reset=0, fields={"Z": 0, "S": (3, 2)})
    mcu.alias("acc", "r0")
    mcu.alias("zero", "status.Z")
    mcu.on_undefined_opcode("stop")
    mcu.on_out_of_range("stop")
    mcu.on_write_to_readonly("stop")
    mcu.on_uninitialized_read("stop")

    @mcu.instr("0001 0000 iiiiiiii", syntax="LDI {i}", cycles=1)
    def ldi(st, i):
        st.reg["r0"] = i

    @mcu.instr("0010 0000 aaaaaaaa", syntax="ST [{a}]", cycles=1)
    def st_ram(st, a):
        st.mem["ram"][a] = st.reg["r0"]

    @mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
    def hlt(st):
        st.halt("hlt")

    return mcu

mcu = make_mcu()
""",
        encoding="utf-8",
    )
    asm_file = tmp_path / "alias_prog.asm"
    asm_file.write_text(
        """LDI 0x85
ST [0]
LDI 0x34
ST [2]
LDI 0x12
ST [3]
HLT
""",
        encoding="utf-8",
    )
    script_file = tmp_path / "alias_setup.py"
    script_file.write_text(
        """class IoDev:
    def read(self, addr, now):
        return 0x55

    def write(self, addr, value, now):
        pass

    def get_state(self):
        return {}

    def set_state(self, state):
        pass


def setup(sim):
    sim.attach(IoDev(), space="io", lo=0, hi=1)
    sim.mem_alias("single_default", "ram[0]", bit_order="msb_first")
    sim.mem_alias("bit_one", "ram[0][7]")
    sim.mem_alias("word_le", "ram[2:4]", endian="little", bit_order="msb_first")
    sim.mem_alias("dev_cell", "io[0]", bit_order="msb_first")
""",
        encoding="utf-8",
    )

    session = DebugSession(mcu_file, asm_file, script_path=script_file)
    st0 = session.get_state()

    aliases = {a["name"]: a for a in st0["aliases"]}
    assert aliases["acc"] == {
        "name": "acc",
        "target": "r0",
        "bits": 8,
        "value": 0,
        "hex": "0x00",
        "unsigned": 0,
        "unsigned_str": "0",
        "signed": 0,
        "signed_str": "0",
    }
    assert aliases["zero"] == {
        "name": "zero",
        "target": "status.Z",
        "bits": 1,
        "value": 0,
        "hex": "0x0",
        "unsigned": 0,
        "unsigned_str": "0",
        "signed": 0,
        "signed_str": "0",
    }

    ma0 = {m["name"]: m for m in st0["mem_aliases"]}
    assert ma0["single_default"] == {
        "name": "single_default",
        "target": "ram[0]",
        "space": "ram",
        "lo": 0,
        "hi": 1,
        "endian": None,
        "bit_order": "msb_first",
        "bits": 8,
        "initialized": False,
        "value": None,
        "hex": "--",
        "unsigned": None,
        "unsigned_str": None,
        "signed": None,
        "signed_str": None,
        "changed": False,
    }
    assert ma0["bit_one"]["endian"] is None
    assert ma0["bit_one"]["bit_order"] is None
    assert ma0["bit_one"]["bits"] == 1
    assert ma0["word_le"]["endian"] == "little"
    assert ma0["word_le"]["bit_order"] == "msb_first"
    assert ma0["word_le"]["bits"] == 16
    assert ma0["word_le"]["initialized"] is False
    # Device-owned cell must remain unreadable without side effects -> "--"
    assert ma0["dev_cell"]["initialized"] is False
    assert ma0["dev_cell"]["value"] is None
    assert ma0["dev_cell"]["hex"] == "--"

    # Step LDI 0x85 then ST [0]
    session.step_in()
    st1 = session.step_in()
    aliases1 = {a["name"]: a for a in st1["aliases"]}
    assert aliases1["acc"]["value"] == 0x85
    assert aliases1["acc"]["hex"] == "0x85"
    assert aliases1["acc"]["unsigned"] == 0x85
    assert aliases1["acc"]["signed"] == -123

    ma1 = {m["name"]: m for m in st1["mem_aliases"]}
    assert ma1["single_default"]["initialized"] is True
    assert ma1["single_default"]["value"] == 0x85
    assert ma1["single_default"]["hex"] == "0x85"
    assert ma1["single_default"]["unsigned"] == 133
    assert ma1["single_default"]["signed"] == -123
    assert ma1["single_default"]["changed"] is True
    assert ma1["bit_one"]["value"] == 1
    assert ma1["bit_one"]["hex"] == "0x1"
    assert ma1["bit_one"]["unsigned"] == 1
    assert ma1["bit_one"]["signed"] == -1
    assert ma1["bit_one"]["changed"] is True

    # Next step doesn't touch ram[0]; changed resets to False.
    # Also partially initialize ram[2] -> word_le stays uninitialized until ram[3] is written.
    session.step_in()
    st2 = session.step_in()
    ma2 = {m["name"]: m for m in st2["mem_aliases"]}
    assert ma2["single_default"]["changed"] is False
    assert ma2["bit_one"]["changed"] is False
    assert ma2["word_le"]["initialized"] is False
    assert ma2["word_le"]["hex"] == "--"

    session.step_in()
    st3 = session.step_in()
    ma3 = {m["name"]: m for m in st3["mem_aliases"]}
    assert ma3["word_le"]["initialized"] is True
    assert ma3["word_le"]["value"] == 0x1234
    assert ma3["word_le"]["hex"] == "0x1234"
    assert ma3["word_le"]["changed"] is True

    st_back = session.step_back()
    ma_back = {m["name"]: m for m in st_back["mem_aliases"]}
    assert ma_back["word_le"]["initialized"] is False
    assert ma_back["word_le"]["changed"] is True


def test_poke_mem_alias_and_condition_normalization(tmp_path: Path):
    mcu_file = tmp_path / "cond_mcu.py"
    mcu_file.write_text(
        """from rpkbin.isasim import MCU

def make_mcu():
    mcu = MCU("cond_toy")
    mcu.space("prog", cell_bits=16, size=32, access="ro", reset_state="uninitialized", init=0, is_program=True)
    mcu.space("ram", cell_bits=8, size=16, access="rw", reset_state="initialized", init=0)
    mcu.reg("pc", bits=8, reset=0, is_pc=True)
    mcu.reg("r0", bits=8, reset=0)
    mcu.alias("acc", "r0")
    mcu.on_undefined_opcode("stop")
    mcu.on_out_of_range("stop")
    mcu.on_write_to_readonly("stop")
    mcu.on_uninitialized_read("stop")

    @mcu.instr("0001 0000 iiiiiiii", syntax="LDI {i}", cycles=1)
    def ldi(st, i):
        st.reg["r0"] = i

    @mcu.instr("0010 0000 aaaaaaaa", syntax="ST [{a}]", cycles=1)
    def st_ram(st, a):
        st.mem["ram"][a] = st.reg["r0"]

    @mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
    def hlt(st):
        st.halt("hlt")

    return mcu

mcu = make_mcu()
""",
        encoding="utf-8",
    )
    asm_file = tmp_path / "cond_prog.asm"
    asm_file.write_text(
        """LDI 0x12
ST [0]
LDI 0x34
ST [1]
HLT
""",
        encoding="utf-8",
    )
    script_file = tmp_path / "cond_setup.py"
    script_file.write_text(
        """def setup(sim):
    sim.mem_alias("word_be", "ram[0:2]", endian="big", bit_order="msb_first")
    sim.mem_alias("flag_bit", "ram[2][0]")
""",
        encoding="utf-8",
    )

    session = DebugSession(mcu_file, asm_file, script_path=script_file)

    st = session.poke("word_be", "0xfffe")
    word_be = next(m for m in st["mem_aliases"] if m["name"] == "word_be")
    assert word_be["value"] == 0xFFFE
    assert word_be["hex"] == "0xfffe"
    assert word_be["unsigned"] == 65534
    assert word_be["signed"] == -2
    assert word_be["changed"] is True

    st = session.poke("word_be", -1)
    word_be = next(m for m in st["mem_aliases"] if m["name"] == "word_be")
    assert word_be["value"] == 0xFFFF
    assert word_be["signed"] == -1

    with pytest.raises(ValueError):
        session.poke("word_be", 0x10000)
    with pytest.raises(ValueError):
        session.poke("flag_bit", -1)

    assert session.sim.evaluate('m["word_be"] == 0xFFFF and m["flag_bit"] == 0 and r["acc"] == 0') is True

    session.reset()
    cond = "word_be == 0x1234 and acc == 0x34"
    session.toggle_breakpoint(asm_file.resolve().as_posix(), 5, condition=cond)
    assert session.get_state()["files"][asm_file.resolve().as_posix()][4]["bp_cond"] == cond
    st_hit_bp = session.continue_run()
    assert st_hit_bp["status"]["stop_reason"] == "breakpoint"
    session.toggle_breakpoint(asm_file.resolve().as_posix(), 5)

    session.reset()
    session.add_watchpoint("ram[1]", kind="w", condition=cond)
    assert session.get_state()["watchpoints"][0]["condition"] == cond
    st_hit = session.continue_run()
    assert st_hit["status"]["stop_reason"] == "watchpoint"
    word_after = next(m for m in st_hit["mem_aliases"] if m["name"] == "word_be")
    assert word_after["value"] == 0x1234


def test_playground_demo_mem_aliases_smoke():
    demo_dir = ROOT / "playground" / "isasim_demo"
    static_mem_names = {
        "running_sum",
        "final_total",
        "step_low_byte",
        "sum_lsb",
        "result_pair",
        *(f"sram_{i:02d}" for i in range(8)),
    }
    expected_alias_targets = {
        "acc": "r0",
        "loop_cnt": "r1",
        "step_val": "r2",
        "scratch": "r3",
        "zero_flag": "flags.Z",
    }

    # 1) Without --script: static MCU aliases + mem_aliases work out of the box.
    session_bare = DebugSession(
        demo_dir / "demo_mcu.py",
        demo_dir / "demo_loop.asm",
    )
    st_bare0 = session_bare.get_state()
    assert {a["name"]: a["target"] for a in st_bare0["aliases"]} == expected_alias_targets
    ma_bare0 = {m["name"]: m for m in st_bare0["mem_aliases"]}
    assert set(ma_bare0) == static_mem_names
    assert ma_bare0["sram_00"]["initialized"] is False
    assert ma_bare0["sram_00"]["hex"] == "--"

    st_bare_done = session_bare.continue_run()
    assert st_bare_done["status"]["stop_reason"] == "halted"
    aliases_bare_done = {a["name"]: a for a in st_bare_done["aliases"]}
    assert aliases_bare_done["acc"]["value"] == 30
    assert aliases_bare_done["scratch"]["value"] == 0x2AB7
    assert aliases_bare_done["zero_flag"]["value"] == 1
    ma_bare_done = {m["name"]: m for m in st_bare_done["mem_aliases"]}
    assert ma_bare_done["running_sum"]["value"] == 30
    assert ma_bare_done["final_total"]["value"] == 30
    assert ma_bare_done["result_pair"]["value"] == 0x001E001E

    # 2) With --script: static MCU aliases + script-defined aliases compose without collision.
    session = DebugSession(
        demo_dir / "demo_mcu.py",
        demo_dir / "demo_loop.asm",
        script_path=demo_dir / "setup_stim.py",
    )

    st0 = session.get_state()
    assert {a["name"]: a["target"] for a in st0["aliases"]} == expected_alias_targets
    ma0 = {m["name"]: m for m in st0["mem_aliases"]}
    assert set(ma0) == static_mem_names | {"arr_head", "arr_tail"}
    assert ma0["running_sum"]["endian"] is None
    assert ma0["running_sum"]["bit_order"] == "msb_first"
    assert ma0["running_sum"]["initialized"] is False
    assert ma0["running_sum"]["hex"] == "--"
    assert ma0["sum_lsb"]["endian"] is None
    assert ma0["sum_lsb"]["bit_order"] is None
    assert ma0["arr_head"]["endian"] == "little"
    assert ma0["arr_head"]["bit_order"] == "msb_first"
    assert ma0["arr_head"]["initialized"] is True
    assert ma0["arr_head"]["value"] == 0x00C80064
    assert ma0["arr_head"]["hex"] == "0x00c80064"
    assert ma0["arr_tail"]["endian"] == "little"
    assert ma0["arr_tail"]["bit_order"] == "msb_first"
    assert ma0["arr_tail"]["initialized"] is True
    assert ma0["arr_tail"]["value"] == 0x0190012C
    assert ma0["arr_tail"]["hex"] == "0x0190012c"
    assert ma0["sram_00"]["value"] == 0x64
    assert ma0["sram_01"]["value"] == 0x00
    assert ma0["sram_04"]["value"] == 0x2C
    assert ma0["sram_05"]["value"] == 0x01
    assert ma0["sram_06"]["value"] == 0x90
    assert ma0["sram_07"]["value"] == 0x01

    st_done = session.continue_run()
    assert st_done["status"]["stop_reason"] == "halted"
    aliases_done = {a["name"]: a for a in st_done["aliases"]}
    assert aliases_done["acc"]["value"] == 30
    assert aliases_done["loop_cnt"]["value"] == 0
    assert aliases_done["step_val"]["value"] == 1
    assert aliases_done["scratch"]["value"] == 0x2AB7
    assert aliases_done["zero_flag"]["value"] == 1
    ma_done = {m["name"]: m for m in st_done["mem_aliases"]}
    assert ma_done["running_sum"]["value"] == 30
    assert ma_done["running_sum"]["hex"] == "0x001e"
    assert ma_done["final_total"]["value"] == 30
    assert ma_done["sum_lsb"]["value"] == 0
    assert ma_done["step_low_byte"]["value"] == 2
    assert ma_done["step_low_byte"]["hex"] == "0x02"
    assert ma_done["result_pair"]["value"] == 0x001E001E

    st_reset = session.reset()
    assert st_reset["status"]["stop_reason"] == "ready"
    ma_reset = {m["name"]: m for m in st_reset["mem_aliases"]}
    assert ma_reset["running_sum"]["initialized"] is False
    assert ma_reset["final_total"]["initialized"] is False
    assert ma_reset["arr_head"]["value"] == 0x00C80064
    assert ma_reset["arr_tail"]["value"] == 0x0190012C
    assert all(not m["changed"] for m in st_reset["mem_aliases"])


def test_reset_with_script_reloads_cleanly_without_stale_cells_and_handles_errors(
    tmp_path,
):
    mcu_file = tmp_path / "mcu_rst.py"
    mcu_file.write_text(
        """from rpkbin.isasim import MCU
mcu = MCU("rst_demo")
mcu.space("prog", cell_bits=16, size=32, access="ro", reset_state="uninitialized", init=0, is_program=True)
mcu.space("ram", cell_bits=8, size=16, access="rw", reset_state="uninitialized", init=0)
mcu.reg("pc", bits=5, reset=0, is_pc=True)
mcu.reg("r0", bits=8, reset=0)
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")

@mcu.instr("0001 0000 vvvvvvvv", syntax="WR2 {v}", cycles=1)
def wr2(st, v):
    st.mem["ram"][2] = v

@mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
def hlt(st):
    st.halt("done")
""",
        encoding="utf-8",
    )
    asm_file = tmp_path / "prog.asm"
    asm_file.write_text("WR2 0x55\nHLT\n", encoding="utf-8")
    script_file = tmp_path / "setup.py"
    script_file.write_text(
        """def setup(sim):
    sim.load_cells("ram", {0: 0xAA, 1: 0xBB})
    sim.mem_alias("out_alias", "ram[0]", bit_order="msb_first")
""",
        encoding="utf-8",
    )

    session = DebugSession(mcu_file, asm_file, script_path=script_file)
    session.toggle_breakpoint(session._program.main, 2)
    session.add_watchpoint("out_alias", kind="w")
    session.step_in()
    assert len(session._history) == 1

    # Edit setup.py: load ram[2] instead of ram[0..1], and move out_alias to ram[2].
    script_file.write_text(
        """def setup(sim):
    sim.load_cells("ram", {2: 0xCC})
    sim.mem_alias("out_alias", "ram[2]", bit_order="msb_first")
""",
        encoding="utf-8",
    )
    st_reset = session.reset()
    assert st_reset["status"]["error"] is None
    assert st_reset["status"]["stop_reason"] == "ready"
    assert len(session._history) == 0

    cells_by_addr = {c["addr"]: c for c in st_reset["memory"]["cells"]}
    assert cells_by_addr[0]["initialized"] is False
    assert cells_by_addr[1]["initialized"] is False
    assert cells_by_addr[2]["initialized"] is True
    assert cells_by_addr[2]["value"] == 0xCC

    ma = {m["name"]: m for m in st_reset["mem_aliases"]}
    assert ma["out_alias"]["target"] == "ram[2]"
    assert ma["out_alias"]["value"] == 0xCC

    # Existing watchpoint re-bound to moved out_alias -> hits on write to ram[2].
    st_hit = session.continue_run()
    assert st_hit["status"]["stop_reason"] == "watchpoint"
    assert "target=out_alias" in st_hit["status"]["detail"]
    assert session._last_detail["old"] == 0xCC
    assert session._last_detail["new"] == 0x55

    # Broken setup (removed watched alias OR syntax error) leaves current session intact.
    prev_sim = session.sim
    prev_bps = dict(session._breakpoints)
    prev_wps = dict(session._watchpoints)
    script_file.write_text("def setup(sim):\n    pass\n", encoding="utf-8")
    st_err = session.reset()
    assert st_err["status"]["error"] is not None
    assert session.sim is prev_sim
    assert session._breakpoints == prev_bps
    assert session._watchpoints == prev_wps
    assert len(session._history) > 0

    script_file.write_text("def setup(sim):\n    if True print(1)\n", encoding="utf-8")
    st_syn = session.reset()
    assert st_syn["status"]["error"] is not None
    assert session.sim is prev_sim
    assert session._breakpoints == prev_bps
    assert session._watchpoints == prev_wps


def test_reset_without_script_preserves_runtime_devices_stimuli_and_mem_aliases(
    tmp_path,
):
    from rpkbin.isasim import Device

    class TrackingDevice(Device):
        def __init__(self):
            self.resets = 0

        def read(self, addr, now):
            return 0x33

        def write(self, addr, value, now):
            pass

        def reset(self):
            self.resets += 1

        def get_state(self):
            return {"resets": self.resets}

        def set_state(self, state):
            self.resets = state["resets"]

    mcu_file = tmp_path / "mcu_bare.py"
    mcu_file.write_text(
        """from rpkbin.isasim import MCU
mcu = MCU("bare_demo")
mcu.space("prog", cell_bits=16, size=16, access="ro", reset_state="uninitialized", init=0, is_program=True)
mcu.space("ram", cell_bits=8, size=8, access="rw", reset_state="initialized", init=0)
mcu.reg("pc", bits=4, reset=0, is_pc=True)
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")

@mcu.instr("0001 0000 0000 0000", syntax="NOP", cycles=1)
def nop(st):
    pass

@mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
def hlt(st):
    st.halt("done")
""",
        encoding="utf-8",
    )
    asm_file = tmp_path / "bare.asm"
    asm_file.write_text("NOP\nNOP\nHLT\n", encoding="utf-8")

    session = DebugSession(mcu_file, asm_file, script_path=None)
    dev = TrackingDevice()
    session.sim.attach(dev, space="ram", lo=7, hi=8)
    session.sim.mem_alias("dyn_out", "ram[1]", bit_order="msb_first")
    session.sim.at_cycle(1).set("dyn_out", 0x99)

    session.step_in()
    st_reset = session.reset()
    assert st_reset["status"]["error"] is None
    assert st_reset["status"]["stop_reason"] == "ready"
    assert dev.resets == 1
    assert "dyn_out" in session.sim.mem_aliases
    assert len(session.sim.list_stimuli()) == 1

    st_done = session.continue_run()
    assert st_done["status"]["stop_reason"] == "halted"
    ma = {m["name"]: m for m in st_done["mem_aliases"]}
    assert ma["dyn_out"]["value"] == 0x99


def test_wide_64bit_integer_precision_fields_in_state(tmp_path):
    mcu_file = tmp_path / "mcu64.py"
    mcu_file.write_text(
        """from rpkbin.isasim import MCU
mcu = MCU("wide64")
mcu.space("prog", cell_bits=16, size=8, access="ro", reset_state="uninitialized", init=0, is_program=True)
mcu.space("ram", cell_bits=64, size=4, access="rw", reset_state="initialized", init=0)
mcu.reg("pc", bits=3, reset=0, is_pc=True)
mcu.reg("r64", bits=64, reset=0, fields={"hi_part": (63, 32)})
mcu.alias("wide_acc", "r64")
mcu.mem_alias("wide_word", "ram[0]", bit_order="msb_first")
mcu.mem_alias("bit", "ram[1][0]")
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")

@mcu.instr("0001 0000 0000 0000", syntax="NOP", cycles=1)
def nop(st):
    pass

@mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
def hlt(st):
    st.halt("done")
""",
        encoding="utf-8",
    )
    asm_file = tmp_path / "wide64.asm"
    asm_file.write_text("NOP\nHLT\n", encoding="utf-8")

    session = DebugSession(mcu_file, asm_file)
    wide_val = 0x0020000000000001  # 2^53 + 1 == 9007199254740993 (not exactly representable as float64)
    session.poke("r64", wide_val)
    session.poke("wide_word", wide_val)
    st = session.get_state()

    reg = next(r for r in st["registers"] if r["name"] == "r64")
    assert reg["unsigned_str"] == "9007199254740993"
    assert reg["signed_str"] == "9007199254740993"
    assert reg["fields"][0]["value_str"] == str(0x00200000)

    alias = next(a for a in st["aliases"] if a["name"] == "wide_acc")
    assert alias["unsigned_str"] == "9007199254740993"
    assert alias["signed_str"] == "9007199254740993"

    cell0 = st["memory"]["cells"][0]
    assert cell0["unsigned_str"] == "9007199254740993"
    assert cell0["signed_str"] == "9007199254740993"

    ma = next(m for m in st["mem_aliases"] if m["name"] == "wide_word")
    assert ma["unsigned_str"] == "9007199254740993"
    assert ma["signed_str"] == "9007199254740993"

    # Helper functions like bit(...) and to_signed(...) are preserved even if shadowed by a mem_alias name.
    session.toggle_breakpoint(session._program.main, 2, condition="bit(r64, 0) == 1 and bit == 0")
    st_bp = session.continue_run()
    assert st_bp["status"]["stop_reason"] == "breakpoint"
    assert "bit" in st["mem_aliases"][1]["name"]
    session.toggle_breakpoint(session._program.main, 2)

    # Shadowed helper inside conditional callee must also preserve helper semantics across install and Reset.
    # Unchosen parity branch with 2 args is not called at cycle == 0, while dual-branch compatible helpers run cleanly.
    shadow_cond = "(bit if cycle == 0 else parity)(7, 0) == 1"
    session.reset()
    session.toggle_breakpoint(session._program.main, 1, condition=shadow_cond)
    assert session.sim.evaluate(shadow_cond) is True
    session.reset()
    assert session.sim.evaluate(shadow_cond) is True
    session.toggle_breakpoint(session._program.main, 1)

    dual_cond = "(bit if cycle == 0 else to_unsigned)(0x0a, 2) == 2"
    session.toggle_breakpoint(session._program.main, 1, condition=dual_cond)
    assert session.sim.evaluate("(bit if cycle == 0 else to_unsigned)(0x0a, 2)") == 0
    session.step_in()
    assert session.sim.evaluate(dual_cond) is True
    session.toggle_breakpoint(session._program.main, 1)


def test_remove_watchpoint_unified_validation(tmp_path):
    from rpkbin.app.isasim_gui.server import _exec
    from rpkbin.isasim.errors import UsageError

    mcu_file = tmp_path / "mcu_wp.py"
    mcu_file.write_text(
        """from rpkbin.isasim import MCU
mcu = MCU("wp_demo")
mcu.space("prog", cell_bits=16, size=8, access="ro", reset_state="uninitialized", init=0, is_program=True)
mcu.space("ram", cell_bits=8, size=8, access="rw", reset_state="initialized", init=0)
mcu.reg("pc", bits=3, reset=0, is_pc=True)
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")
@mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
def hlt(st):
    st.halt("done")
""",
        encoding="utf-8",
    )
    asm_file = tmp_path / "wp.asm"
    asm_file.write_text("HLT\n", encoding="utf-8")

    session = DebugSession(mcu_file, asm_file)
    st1 = session.add_watchpoint("ram[0]", kind="w")
    wid = st1["watchpoints"][0]["id"]
    st2 = _exec(session, "remove_watchpoint", {"id": f" {wid} "})
    assert st2["watchpoints"] == []

    for bad in (None, True, 1.5, "abc"):
        with pytest.raises(UsageError):
            _exec(session, "remove_watchpoint", {"id": bad})


def test_reset_defers_condition_eval_and_commits_atomically(tmp_path):
    mcu_file = tmp_path / "mcu_cond.py"
    mcu_file.write_text(
        """from rpkbin.isasim import MCU
mcu = MCU("cond_demo")
mcu.space("prog", cell_bits=16, size=16, access="ro", reset_state="uninitialized", init=0, is_program=True)
mcu.space("ram", cell_bits=8, size=16, access="rw", reset_state="uninitialized", init=0)
mcu.reg("pc", bits=4, reset=0, is_pc=True)
mcu.reg("r0", bits=8, reset=0)
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")
@mcu.instr("0001 0000 0000 0000", syntax="NOP", cycles=1)
def nop(st):
    pass
@mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
def hlt(st):
    st.halt("done")
""",
        encoding="utf-8",
    )
    asm_file = tmp_path / "cond.asm"
    asm_file.write_text("NOP\nHLT\n", encoding="utf-8")
    script_file = tmp_path / "setup.py"
    script_file.write_text(
        """def setup(sim):
    sim.mem_alias("out_alias", "ram[0]", bit_order="msb_first")
""",
        encoding="utf-8",
    )

    session = DebugSession(
        mcu_file,
        asm_file,
        script_path=script_file,
        call_mnemonics=("CALL",),
        return_mnemonics=("RET",),
    )
    # Valid target & syntax, even when r0 == 0 or ram[0] is uninitialized!
    session.poke("r0", 4)
    session.toggle_breakpoint(session._program.main, 2, condition="8 // r0 == 2")
    session.add_watchpoint("ram[1]", kind="w", condition="out_alias == 1")
    session.step_in()
    assert len(session._history) == 2

    # Reset succeeds cleanly even though initial candidate r0 == 0 and ram[0] is uninitialized.
    st_rst = session.reset()
    assert st_rst["status"]["error"] is None
    assert st_rst["status"]["stop_reason"] == "ready"
    assert next(r for r in st_rst["registers"] if r["name"] == "r0")["value"] == 0
    assert st_rst["files"][session._program.main][1]["bp_cond"] == "8 // r0 == 2"

    # Runtime division-by-zero triggers cleanly only when evaluated at the breakpoint with r0 == 0,
    # whereas setting r0 = 4 before reaching line 2 matches the re-bound condition cleanly.
    session.step_in()  # advance from line 1 (NOP) to line 2 (HLT) where breakpoint is installed
    st_div0 = session.step_over()
    assert st_div0["status"]["stop_reason"] == "debug_error"
    assert "DebugError" in st_div0["status"]["error"]
    session.reset()
    session.poke("r0", 4)
    st_hit = session.continue_run()
    assert st_hit["status"]["stop_reason"] == "breakpoint"
    assert st_hit["status"]["pc"] == 1

    # Reset with a candidate whose Device.get_state() raises RuntimeError keeps old sim & history intact.
    session.reset()
    session.poke("r0", 9)
    session.step_in()
    assert len(session._history) >= 1
    prev_sim = session.sim
    prev_history_len = len(session._history)
    script_file.write_text(
        """from rpkbin.isasim import Device
class BrokenSnapDevice(Device):
    def read(self, addr, now):
        return 0
    def write(self, addr, value, now):
        pass
    def get_state(self):
        raise RuntimeError("boom")
    def set_state(self, state):
        pass
def setup(sim):
    sim.attach(BrokenSnapDevice(), space="ram", lo=5, hi=6)
    sim.mem_alias("out_alias", "ram[0]", bit_order="msb_first")
""",
        encoding="utf-8",
    )
    st_fail = session.reset()
    assert "boom" in st_fail["status"]["error"]
    assert session.sim is prev_sim
    assert len(session._history) == prev_history_len
    st_back = session.step_back()
    assert st_back["status"]["stop_reason"] == "step_back"

    # Removing referenced alias in condition rejects reset atomically.
    script_file.write_text("def setup(sim):\n    pass\n", encoding="utf-8")
    st_missing = session.reset()
    assert "Invalid condition" in st_missing["status"]["error"]
    assert session.sim is prev_sim

    # Non-snapshotable Device (missing set_state -> SnapshotError) degrades cleanly on candidate commit.
    script_file.write_text(
        """from rpkbin.isasim import Device
class BareDevice(Device):
    def read(self, addr, now):
        return 0
    def write(self, addr, value, now):
        pass
def setup(sim):
    sim.attach(BareDevice(), space="ram", lo=5, hi=6)
    sim.mem_alias("out_alias", "ram[0]", bit_order="msb_first")
""",
        encoding="utf-8",
    )
    st_deg = session.reset()
    assert st_deg["status"]["error"] is None
    assert st_deg["status"]["can_step_back"] is False
    assert "get_state/set_state" in st_deg["status"]["step_back_reason"]


def test_ultra_wide_4kib_alias_constants_and_builtin_conditions(tmp_path):
    import json

    mcu_file = tmp_path / "mcu_4k.py"
    mcu_file.write_text(
        """from rpkbin.isasim import MCU
mcu = MCU("wide4k")
mcu.space("prog", cell_bits=16, size=8, access="ro", reset_state="uninitialized", init=0, is_program=True)
mcu.space("ram", cell_bits=8, size=4096, access="rw", reset_state="initialized", init=0)
mcu.reg("ip", bits=3, reset=0, is_pc=True)
mcu.mem_alias("wide_buf", "ram[0:4096]", endian="big", bit_order="msb_first")
mcu.mem_alias("cycle", "ram[0]", bit_order="msb_first")
mcu.mem_alias("pc", "ram[1]", bit_order="msb_first")
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")
@mcu.instr("0001 0000 0000 0000", syntax="NOP", cycles=1)
def nop(st):
    pass
@mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
def hlt(st):
    st.halt("done")
""",
        encoding="utf-8",
    )
    asm_file = tmp_path / "wide4k.asm"
    asm_file.write_text(
        "BIG = 0x20000000000001\nNEG_BIG = -0x20000000000001\nNOP\nHLT\n",
        encoding="utf-8",
    )

    session = DebugSession(mcu_file, asm_file)
    # Write 0x01 to highest byte of 4 KiB big-endian alias (exceeds Python's 4300-digit decimal limit).
    st = session.poke("ram[0]", 1)
    serialized = json.loads(json.dumps(st, ensure_ascii=False, allow_nan=False))
    buf = next(m for m in serialized["mem_aliases"] if m["name"] == "wide_buf")
    assert buf["initialized"] is True
    assert buf["hex"] == "0x01" + "00" * 4095
    assert buf["value"] is None
    assert buf["unsigned"] is None
    assert buf["unsigned_str"] is None
    assert buf["signed"] is None
    assert buf["signed_str"] is None

    # All-1s negative signed value is -1 (fits in JSON/decimal even when unsigned exceeds 4300 digits).
    st_neg = session.poke("wide_buf", -1)
    ser_neg = json.loads(json.dumps(st_neg, ensure_ascii=False, allow_nan=False))
    buf_neg = next(m for m in ser_neg["mem_aliases"] if m["name"] == "wide_buf")
    assert buf_neg["hex"] == "0x" + "ff" * 4096
    assert buf_neg["unsigned"] is None
    assert buf_neg["unsigned_str"] is None
    assert buf_neg["signed"] == -1
    assert buf_neg["signed_str"] == "-1"

    # Constants display strings preserve precision beyond float64 MAX_SAFE_INTEGER.
    assert ser_neg["constants"] == {
        "BIG": 0x20000000000001,
        "NEG_BIG": -0x20000000000001,
    }
    assert ser_neg["constants_display"] == {
        "BIG": {
            "hex": "0x20000000000001",
            "unsigned_str": "9007199254740993",
            "signed_str": "9007199254740993",
        },
        "NEG_BIG": {
            "hex": "-0x20000000000001",
            "unsigned_str": None,
            "signed_str": "-9007199254740993",
        },
    }

    # Built-in debugger names (cycle, pc, r, m, i) retain priority over shadowed aliases in conditions,
    # while custom PC names like "ip" resolve through public GUI conditions and Core namespaces.
    session.reset()
    session.step_in()  # cycle=1, ip=1 while mem_alias "cycle" (ram[0]) == 0 and mem_alias "pc" (ram[1]) == 0
    assert session.sim.read("cycle") == 0
    session.toggle_breakpoint(
        asm_file.resolve().as_posix(),
        4,
        condition="cycle == 1 and m.cycle == 0 and pc == 1 and ip == 1 and m.pc == 0",
    )
    session.reset()
    st_ip = session.continue_run()
    assert st_ip["status"]["stop_reason"] == "breakpoint"
    assert st_ip["status"]["active_line"] == 4
    assert session.sim.evaluate("cycle == 0") is False
    assert session.sim.evaluate("m.cycle == 0") is True
    assert session.sim.evaluate("pc == 1 and r.ip == 1") is True
    assert session.sim.evaluate("m.pc == 0") is True


def test_ultra_wide_bitslice_and_flag_fields_and_custom_mcu_conditions(tmp_path):
    import json
    import sys
    from rpkbin.isasim.errors import DebugError

    mcu_file = tmp_path / "mcu_custom_wide.py"
    mcu_file.write_text(
        """from rpkbin.isasim import MCU
mcu = MCU("custom_wide")
mcu.space("prog", cell_bits=16, size=8, access="ro", reset_state="initialized", init=0, is_program=True)
mcu.space("i", cell_bits=16, size=4, access="rw", reset_state="initialized", init=0)
mcu.space("cycle", cell_bits=16, size=4, access="rw", reset_state="initialized", init=0)
mcu.space("wide_ram", cell_bits=16000, size=2, access="rw", reset_state="initialized", init=0)
mcu.reg("pc", bits=3, reset=0, is_pc=True)
mcu.reg("r", bits=8, reset=0, fields={"low": (3, 0)})
mcu.reg("r0", bits=8, reset=0)
mcu.reg("sr", bits=16000, reset=0, role="flag", fields={"BIG": (15999, 0)})
mcu.mem_alias("wide_slice", "wide_ram[0][15999:0]", bit_order="msb_first")
for pol in ("on_undefined_opcode", "on_out_of_range", "on_write_to_readonly", "on_uninitialized_read"):
    getattr(mcu, pol)("stop")

@mcu.instr("0001 vvvv vvvv 0000", syntax="SETR {v}", cycles=1)
def setr(st, v):
    st.reg["r"] = v

@mcu.instr("0010 vvvv vvvv 0000", syntax="SET0 {v}", cycles=1)
def set0(st, v):
    st.reg["r0"] = v

@mcu.instr("0011 vvvv vvvv 0000", syntax="STI {v}", cycles=1)
def sti(st, v):
    st.mem["i"][0] = v

@mcu.instr("0100 vvvv vvvv 0000", syntax="STC {v}", cycles=1)
def stc(st, v):
    st.mem["cycle"][0] = v

@mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
def hlt(st):
    st.halt("done")
""",
        encoding="utf-8",
    )
    asm_file = tmp_path / "prog.asm"
    asm_file.write_text(
        "SETR 7\nSTI 9\nSTC 9\nSET0 4\nHLT\n",
        encoding="utf-8",
    )
    asm_key = asm_file.resolve().as_posix()

    session = DebugSession(mcu_file, asm_file)

    # 1) 16,000-bit bit-slice + flag field pokes succeed even with JSONL trace enabled, and record Step Back.
    trace_path = tmp_path / "gui_trace.jsonl"
    session.sim.enable_trace(str(trace_path), fmt="jsonl")
    try:
        big_hex = "0x8" + "0" * 3999
        st1 = session.poke("wide_slice", big_hex)
        assert st1["status"]["can_step_back"] is True
        assert next(m for m in st1["mem_aliases"] if m["name"] == "wide_slice")["hex"] == big_hex

        st2 = session.poke("sr.BIG", big_hex)
        sr_entry = next(r for r in st2["registers"] if r["name"] == "sr")
        assert sr_entry["is_flag"] is True
        assert sr_entry["fields"][0]["value"] is None
        assert sr_entry["fields"][0]["value_str"] is None
        assert sr_entry["fields"][0]["hex"] == big_hex
        serialized = json.loads(json.dumps(st2, ensure_ascii=False, allow_nan=False))
        assert next(r for r in serialized["registers"] if r["name"] == "sr")["fields"][0]["hex"] == big_hex
    finally:
        session.sim.disable_trace()

    trace_records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert [rec["writes"][0]["new"] for rec in trace_records] == [big_hex, big_hex]
    assert all(type(rec["writes"][0]["old"]) is int and rec["writes"][0]["old"] == 0 for rec in trace_records)

    st_back = session.step_back()
    assert next(r for r in st_back["registers"] if r["name"] == "sr")["fields"][0]["value"] == 0
    assert next(m for m in st_back["mem_aliases"] if m["name"] == "wide_slice")["hex"] == big_hex
    st_back2 = session.step_back()
    assert next(m for m in st_back2["mem_aliases"] if m["name"] == "wide_slice")["hex"] == "0x" + "0" * 4000

    # 2) Positional/target-aware disambiguation preserves custom reg fields (r.low), int methods (r.bit_count),
    # custom space subscripts (i[0], cycle[0]), and built-in cycle semantics through public entrypoints.
    assert session.sim.evaluate("r.r == 0 and r.r.low == 0 and r.r.bit_count() == 0") is True

    session.toggle_breakpoint(asm_key, 2, condition="r == 7 and r.r == 7 and r.low == 7 and r.r.low == 7 and r.bit_count() == 3")
    session.toggle_breakpoint(asm_key, 3, condition="i[0] == 9 and m.i[0] == 9")
    session.toggle_breakpoint(asm_key, 4, condition="cycle == 3 and cycle[0] == 9")
    session.toggle_breakpoint(asm_key, 5, condition="r.r0.bit_count() == 1")

    res_bp1 = session.continue_run()
    assert res_bp1["status"]["stop_reason"] == "breakpoint"
    assert res_bp1["status"]["active_line"] == 2

    res_bp2 = session.continue_run()
    assert res_bp2["status"]["stop_reason"] == "breakpoint"
    assert res_bp2["status"]["active_line"] == 3

    res_bp3 = session.continue_run()
    assert res_bp3["status"]["stop_reason"] == "breakpoint"
    assert res_bp3["status"]["active_line"] == 4

    res_bp4 = session.continue_run()
    assert res_bp4["status"]["stop_reason"] == "breakpoint"
    assert res_bp4["status"]["active_line"] == 5

    # Breakpoints with r.low, r.bit_count(), cycle[0], and r.r0.bit_count() survive Reset cleanly.
    st_reset = session.reset()
    assert st_reset["status"]["error"] is None
    assert session.continue_run()["status"]["active_line"] == 2

    # Unknown roots/names still raise DebugError during validation (even when reg "r" exists).
    with pytest.raises(DebugError):
        session.toggle_breakpoint(asm_key, 1, condition="r.missing == 1")
    with pytest.raises(DebugError):
        session.toggle_breakpoint(asm_key, 1, condition="r.missing.bit_count() == 1")
    with pytest.raises(DebugError):
        session.toggle_breakpoint(asm_key, 1, condition="bogus_var == 1")

    # Also verify bare namespace name on an MCU WITHOUT reg "r" is rejected cleanly.
    plain_mcu = tmp_path / "plain_mcu.py"
    plain_mcu.write_text(
        """from rpkbin.isasim import MCU
mcu = MCU("plain")
mcu.space("prog", cell_bits=16, size=4, access="ro", reset_state="initialized", init=0, is_program=True)
mcu.reg("pc", bits=2, reset=0, is_pc=True)
mcu.reg("r0", bits=8, reset=0)
for pol in ("on_undefined_opcode", "on_out_of_range", "on_write_to_readonly", "on_uninitialized_read"):
    getattr(mcu, pol)("stop")
@mcu.instr("0001 0000 0000 0000", syntax="NOP", cycles=1)
def nop(st):
    pass
""",
        encoding="utf-8",
    )
    plain_asm = tmp_path / "plain.asm"
    plain_asm.write_text("NOP\n", encoding="utf-8")
    plain_sess = DebugSession(plain_mcu, plain_asm)
    with pytest.raises(DebugError):
        plain_sess.toggle_breakpoint(plain_asm.resolve().as_posix(), 1, condition="r == 1")

    # Also verify 1,000-digit hex Poke succeeds cleanly with JSONL trace when sys int limit is lowered to 640.
    low_limit_trace = tmp_path / "low_limit.jsonl"
    k1_val = 10**999
    k1_hex = f"0x{k1_val:04000x}"
    prev_limit = sys.get_int_max_str_digits()
    session.sim.enable_trace(str(low_limit_trace), fmt="jsonl")
    sys.set_int_max_str_digits(640)
    try:
        st_k1 = session.poke("wide_slice", f"0x{k1_val:x}")
        assert st_k1["status"]["can_step_back"] is True
        assert next(m for m in st_k1["mem_aliases"] if m["name"] == "wide_slice")["hex"] == k1_hex
        st_k1_back = session.step_back()
        assert next(m for m in st_k1_back["mem_aliases"] if m["name"] == "wide_slice")["hex"] == "0x" + "0" * 4000
    finally:
        sys.set_int_max_str_digits(prev_limit)
        session.sim.disable_trace()
    k1_rec = json.loads(low_limit_trace.read_text(encoding="utf-8").splitlines()[0])
    assert k1_rec["writes"][0]["new"] == k1_hex

    # Ambiguous dotted target when both reg `r` field `low`/`cycle` and standalone reg `low`/`cycle` exist.
    amb_mcu = tmp_path / "amb_mcu.py"
    amb_mcu.write_text(
        """from rpkbin.isasim import MCU
mcu = MCU("amb")
mcu.space("prog", cell_bits=16, size=8, access="ro", reset_state="initialized", init=0, is_program=True)
mcu.reg("pc", bits=3, reset=0, is_pc=True)
mcu.reg("r", bits=8, reset=0, fields={"low": (3, 0), "cycle": (7, 4)})
mcu.reg("low", bits=8, reset=0)
mcu.reg("cycle", bits=8, reset=0)
for pol in ("on_undefined_opcode", "on_out_of_range", "on_write_to_readonly", "on_uninitialized_read"):
    getattr(mcu, pol)("stop")

@mcu.instr("0001 vvvv vvvv 0000", syntax="SETR {v}", cycles=1)
def setr(st, v):
    st.reg["r"] = v

@mcu.instr("0010 vvvv vvvv 0000", syntax="SETL {v}", cycles=1)
def setl(st, v):
    st.reg["low"] = v

@mcu.instr("0011 vvvv vvvv 0000", syntax="SETC {v}", cycles=1)
def setc(st, v):
    st.reg["cycle"] = v

@mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
def hlt(st):
    st.halt("done")
""",
        encoding="utf-8",
    )
    amb_asm = tmp_path / "amb.asm"
    amb_asm.write_text("SETR 0x77\nSETL 9\nSETC 5\nHLT\n", encoding="utf-8")
    amb_key = amb_asm.resolve().as_posix()
    amb_sess = DebugSession(amb_mcu, amb_asm)

    with pytest.raises(DebugError) as low_err:
        amb_sess.toggle_breakpoint(amb_key, 2, condition="r.low == 7")
    assert 'r["r"].low' in str(low_err.value)
    assert 'r["low"]' in str(low_err.value)

    # For `cycle`, bare "cycle" resolves to simulation cycle count, so hint must tell user to write r["cycle"].
    with pytest.raises(DebugError) as cyc_err:
        amb_sess.toggle_breakpoint(amb_key, 4, condition="r.cycle == 5")
    cyc_msg = str(cyc_err.value)
    assert 'r["r"].cycle' in cyc_msg
    assert 'r["cycle"]' in cyc_msg
    assert "'cycle' for 'cycle'" not in cyc_msg

    # Explicit forms unambiguously address r's fields, standalone reg `low`, and standalone reg `cycle`.
    amb_sess.toggle_breakpoint(amb_key, 2, condition="r.r.low == 7 and r.r.cycle == 7")
    amb_sess.toggle_breakpoint(amb_key, 3, condition="low == 9")
    amb_sess.toggle_breakpoint(amb_key, 4, condition='r["cycle"] == 5 and cycle == 3')
    assert amb_sess.continue_run()["status"]["active_line"] == 2
    assert amb_sess.continue_run()["status"]["active_line"] == 3
    assert amb_sess.continue_run()["status"]["active_line"] == 4

    with pytest.raises(DebugError):
        amb_sess.toggle_breakpoint(amb_key, 1, condition='r["missing"] == 1')


_WIDE_16K_HEX = "0x8" + "0" * 3999


def _build_unified_condition_fixture(tmp_path: Path, *, cycle_kind: str = "space"):
    mcu_file = tmp_path / "unified_cond_mcu.py"
    if cycle_kind == "space":
        cycle_decl = '    mcu.space("cycle", cell_bits=8, size=4, access="rw", reset_state="initialized", init=0)'
        cycle_set = '        st.mem["cycle"][0] = 9'
    elif cycle_kind == "reg":
        cycle_decl = '    mcu.reg("cycle", bits=8, reset=0)'
        cycle_set = '        st.reg["cycle"] = 5'
    else:
        cycle_decl = '    mcu.irq_line("cycle")'
        cycle_set = '        st.irq["cycle"] = 1'

    mcu_file.write_text(
        f"""from rpkbin.isasim import MCU

def make_mcu():
    mcu = MCU("unified_cond_toy")
    mcu.space("prog", cell_bits=16, size=32, access="ro", reset_state="uninitialized", init=0, is_program=True)
    mcu.space("_ram", cell_bits=8, size=8, access="rw", reset_state="initialized", init=0)
    mcu.reg("ip", bits=8, reset=0, is_pc=True)
    mcu.reg("reg_a", bits=8, reset=0, fields={{"low": (3, 0)}})
    mcu.reg("_scratch", bits=8, reset=0)
    mcu.reg("wide_reg", bits=16000, reset=0)
    mcu.alias("r_alias", "reg_a")
    mcu.alias("r_field_alias", "reg_a.low")
    mcu.irq_line("_wake")
{cycle_decl}
    mcu.on_undefined_opcode("stop")
    mcu.on_out_of_range("stop")
    mcu.on_write_to_readonly("stop")
    mcu.on_uninitialized_read("stop")

    @mcu.instr("0001 0000 0000 0000", syntax="STEP1", cycles=1)
    def step1(st):
        st.reg["reg_a"] = 0x27
        st.reg["_scratch"] = 7
        st.reg["wide_reg"] = 1 << 15999
        st.mem["_ram"][0] = 13
        st.irq["_wake"] = 1
{cycle_set}

    @mcu.instr("0010 0000 0000 0000", syntax="STEP2", cycles=1)
    def step2(st):
        st.mem["_ram"][0] = 13

    @mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
    def hlt(st):
        st.halt("done")

    return mcu

mcu = make_mcu()
""",
        encoding="utf-8",
    )
    asm_file = tmp_path / "unified_cond_prog.asm"
    asm_file.write_text("STEP1\nSTEP2\nHLT\n", encoding="utf-8")
    script_file = tmp_path / "unified_cond_setup.py"
    script_file.write_text(
        """def setup(sim):
    sim.mem_alias("m_word", "_ram[0:2]", endian="little", bit_order="msb_first")
""",
        encoding="utf-8",
    )
    return mcu_file, asm_file, script_file


@pytest.mark.parametrize(
    ("cycle_kind", "expr", "core_ok"),
    [
        ("space", "r.reg_a == 0x27", True),
        ("space", 'r["reg_a"] == 0x27', True),
        ("space", "reg_a == 0x27", False),
        ("space", "reg_a.low == 7", False),
        ("space", "r.reg_a.low == 7", True),
        ("space", 'r["reg_a"].low == 7', True),
        ("space", "r_alias == 0x27", False),
        ("space", "r.r_alias == 0x27", True),
        ("space", 'r["r_alias"] == 0x27', True),
        ("space", "r_field_alias == 7", False),
        ("space", "r.r_field_alias == 7", True),
        ("space", 'r["r_field_alias"] == 7', True),
        ("space", "_scratch == 7", False),
        ("space", "r._scratch == 7", True),
        ("space", 'r["_scratch"] == 7', True),
        ("space", "_scratch.bit_count() == 3", False),
        ("space", "r._scratch.bit_count() == 3", True),
        ("space", 'r["_scratch"].bit_count() == 3', True),
        ("space", "r.reg_a.bit_count() == 4", True),
        ("space", "(reg_a.bit_count)() == 4", False),
        ("space", "(r.reg_a.bit_count)() == 4", True),
        ("space", "r.reg_a.bit_length() == 6", True),
        ("space", "reg_a.low in (5, 7)", False),
        ("space", 'r["reg_a"].low in {5, 7}', True),
        ("space", "0 < reg_a.low < 8", False),
        ("space", "_ram[0] == 13", False),
        ("space", "m._ram[0] == 13", True),
        ("space", 'm["_ram"][0] == 13', True),
        ("space", "_ram[_scratch - 7] == 13", False),
        ("space", "m_word == 13", False),
        ("space", "m.m_word == 13", True),
        ("space", 'm["m_word"] == 13', True),
        ("space", "_wake == 1", False),
        ("space", "i._wake == 1", True),
        ("space", 'i["_wake"] == 1', True),
        ("space", "cycle[0] == 9", False),
        ("space", "(cycle)[0] == 9", False),
        ("space", 'm["cycle"][0] == 9', True),
        ("reg", 'r["cycle"] == 5', True),
        ("reg", "r.cycle == 5", True),
        ("irq", 'i["cycle"] == 1', True),
        ("irq", "i.cycle == 1", True),
        ("space", "pc >= 1 and cycle >= 1 and cycle.bit_count() == 1", True),
        ("space", f"wide_reg == {_WIDE_16K_HEX}", False),
        ("space", f"r.wide_reg == {_WIDE_16K_HEX}", True),
        ("space", f'r["wide_reg"] == {_WIDE_16K_HEX}', True),
        ("space", "(bit if cycle >= 1 else parity)(reg_a, 0) == 1", False),
        ("space", "(bit if cycle >= 1 else parity)(r.reg_a, 0) == 1", True),
        ("space", "(bit if cycle >= 1 else to_unsigned)(reg_a, 5) == 1", False),
        ("space", "(bit if cycle >= 1 else to_unsigned)(r.reg_a, 5) == 1", True),
    ],
)
def test_unified_condition_matrix_gui_core_and_script_reset(
    tmp_path: Path,
    cycle_kind: str,
    expr: str,
    core_ok: bool,
):
    from rpkbin.isasim.debug import DebugError

    mcu_file, asm_file, script_file = _build_unified_condition_fixture(tmp_path, cycle_kind=cycle_kind)
    sess = DebugSession(mcu_file, asm_file, script_path=script_file)

    if cycle_kind == "space":
        helper_expr = "(bit if cycle == 0 else parity)(7, 0) == 1"
        assert sess.sim.evaluate(helper_expr) is True
        sess.toggle_breakpoint(sess._program.main, 1, condition=helper_expr)
        assert sess.get_state()["files"][sess._program.main][0]["bp_cond"] == helper_expr
        sess.reset()
        assert sess.sim.evaluate(helper_expr) is True
        sess.toggle_breakpoint(sess._program.main, 1)

    sess.toggle_breakpoint(sess._program.main, 2, condition=expr)
    sess.add_watchpoint("_ram[0]", kind="w", condition=expr)

    # Original user condition text is preserved (no private normalized rewrite string leaked).
    assert sess.get_state()["files"][sess._program.main][1]["bp_cond"] == expr
    assert sess.get_state()["watchpoints"][0]["condition"] == expr
    assert any(expr in line for line in sess.sim.list_breakpoints())

    if not core_ok:
        with pytest.raises(DebugError):
            sess.sim.evaluate(expr)
        with pytest.raises(DebugError):
            sess.sim.add_breakpoint(2, condition=expr)

    st_bp = sess.continue_run()
    assert st_bp["status"]["active_line"] == 2
    if core_ok:
        assert sess.sim.evaluate(expr) is True

    st_wp = sess.continue_run()
    assert st_wp["status"]["stop_reason"] == "watchpoint"

    # --script Reset must keep both breakpoint and watchpoint conditions valid after replay.
    sess.reset()
    assert sess.get_state()["status"]["error"] is None
    st_bp_reloaded = sess.continue_run()
    assert st_bp_reloaded["status"]["active_line"] == 2
    st_wp_reloaded = sess.continue_run()
    assert st_wp_reloaded["status"]["stop_reason"] == "watchpoint"

    # Negative guard check in the same setup.
    with pytest.raises(DebugError):
        sess.toggle_breakpoint(sess._program.main, 3, condition="r._scratch.bogus == 1")


@pytest.mark.parametrize(
    "bad_expr",
    [
        "r.reg_a.bogus == 1",
        'r["reg_a"].bogus == 1',
        "r._missing == 1",
        'r["_missing"] == 1',
        "m._missing[0] == 1",
        "i._missing == 1",
        "m._ram == 0",
        "r.reg_a._fields == 1",
        "r._scratch._fields == 1",
        "_fields == 1",
        "r.cycle == 1",
        "cycle == 1 and cycle.bogus == 1",
        "r.reg_a / 2 == 1",
        "'中' and _scratch == 0x07",
        'f"{reg_a:x}" == "27"',
        "(lambda: 1)()",
        "(lambda: (low := 1))()",
        "[r.reg_a for _ in (1,)]",
        "(1, 2)",
        "0 in (1, 2) < 3",
        "1 is 1",
    ],
)
def test_unified_condition_static_rejections(tmp_path: Path, bad_expr: str):
    from rpkbin.isasim.debug import DebugError

    mcu_file, asm_file, script_file = _build_unified_condition_fixture(tmp_path)
    sess = DebugSession(mcu_file, asm_file, script_path=script_file)
    with pytest.raises(DebugError):
        sess.toggle_breakpoint(sess._program.main, 2, condition=bad_expr)


def test_unified_ambiguity_suggestions_round_trip_and_deferred_reset_eval(tmp_path: Path):
    from rpkbin.isasim.debug import DebugError

    mcu_file = tmp_path / "ambig_roundtrip_mcu.py"
    mcu_file.write_text(
        """from rpkbin.isasim import MCU

def make_mcu():
    mcu = MCU("ambig_roundtrip")
    mcu.space("prog", cell_bits=16, size=32, access="ro", reset_state="uninitialized", init=0, is_program=True)
    mcu.space("ram", cell_bits=8, size=8, access="rw", reset_state="initialized", init=0)
    mcu.reg("pc", bits=8, reset=0, is_pc=True)
    mcu.reg("r", bits=8, reset=0, fields={"r": (1, 0), "low": (3, 2), "cycle": (5, 4)})
    mcu.reg("low", bits=8, reset=0)
    mcu.reg("cycle", bits=8, reset=0)
    mcu.reg("m", bits=8, reset=0, fields={"buf": (3, 0)})
    mcu.reg("i", bits=8, reset=0, fields={"wake": (0, 0)})
    mcu.irq_line("wake")
    mcu.on_undefined_opcode("stop")
    mcu.on_out_of_range("stop")
    mcu.on_write_to_readonly("stop")
    mcu.on_uninitialized_read("stop")

    @mcu.instr("0001 0000 0000 0000", syntax="S1", cycles=1)
    def s1(st):
        st.reg["r"] = 0x39  # r.cycle=3, r.low=2, r.r=1, full r=57
        st.reg["m"] = 4     # m.buf=4
        st.reg["i"] = 0     # i.wake=0
        st.irq["wake"] = 1  # irq wake=1
        st.mem["ram"][0] = 30

    @mcu.instr("0010 0000 0000 0000", syntax="S2", cycles=1)
    def s2(st):
        st.reg["low"] = 10
        st.reg["cycle"] = 20
        st.reg["i"] = 1     # i.wake=1
        st.irq["wake"] = 0  # irq wake=0
        st.mem["ram"][1] = 2

    @mcu.instr("0011 0000 0000 0000", syntax="S3", cycles=1)
    def s3(st):
        st.reg["r"] = 0     # distinct on step 3
        st.reg["low"] = 0
        st.reg["cycle"] = 0
        st.reg["m"] = 0
        st.reg["i"] = 0
        st.mem["ram"][0] = 0

    @mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
    def hlt(st):
        st.halt("done")

    return mcu

mcu = make_mcu()
""",
        encoding="utf-8",
    )
    asm_file = tmp_path / "ambig_roundtrip.asm"
    asm_file.write_text("S1\nS2\nS3\nHLT\n", encoding="utf-8")
    script_file = tmp_path / "ambig_roundtrip_setup.py"
    script_file.write_text(
        """def setup(sim):
    sim.mem_alias("buf", "ram[0]", bit_order="msb_first")
""",
        encoding="utf-8",
    )

    sess = DebugSession(mcu_file, asm_file, script_path=script_file)
    key = sess._program.main

    # Core nested dot disambiguation still works when int field `r` has no `.low`.
    sess.toggle_breakpoint(key, 2, condition="r.r.low == 2")
    assert sess.continue_run()["status"]["active_line"] == 2
    assert sess.sim.evaluate("r.r.low == 2") is True
    sess.toggle_breakpoint(key, 2)

    # Extract BOTH suggested hints directly from error messages and prove each targets its own runtime value.
    cases = [
        ("r.low == 2", 2, 10, 2, 3),
        ("r.cycle == 3", 3, 20, 2, 3),
        ("r.r == 1", 1, 57, 2, 2),
        ("m.buf == 4", 4, 30, 2, 2),
        ("i.wake == 1", 0, 1, 2, 2),
    ]
    for amb_expr, val_a, val_b, line_a, line_b in cases:
        with pytest.raises(DebugError) as exc_info:
            sess.toggle_breakpoint(key, 2, condition=amb_expr)
        m = re.search(r"use '([^']+)' or '([^']+)'\)", str(exc_info.value))
        assert m is not None, str(exc_info.value)
        opt_a, opt_b = m.group(1), m.group(2)

        # Verify opt_a matches val_a at line_a and does NOT false-match val_b at line_a.
        sess.reset()
        sess.toggle_breakpoint(key, line_a, condition=f"{opt_a} == {val_a}")
        st_a = sess.continue_run()
        assert st_a["status"]["active_line"] == line_a, (amb_expr, opt_a, st_a["status"])
        assert sess.sim.evaluate(f"{opt_a} == {val_b}") is False
        sess.toggle_breakpoint(key, line_a)

        sess.reset()
        sess.toggle_breakpoint(key, line_a, condition=f"{opt_a} == {val_b}")
        st_a_neg = sess.continue_run()
        assert st_a_neg["status"]["stop_reason"] == "halted", (amb_expr, opt_a, st_a_neg["status"])
        sess.toggle_breakpoint(key, line_a)

        # Verify opt_b matches val_b at line_b and does NOT false-match val_a at line_b.
        sess.reset()
        sess.toggle_breakpoint(key, line_b, condition=f"{opt_b} == {val_b}")
        st_b = sess.continue_run()
        assert st_b["status"]["active_line"] == line_b, (amb_expr, opt_b, st_b["status"])
        assert sess.sim.evaluate(f"{opt_b} == {val_a}") is False
        sess.toggle_breakpoint(key, line_b)

        sess.reset()
        sess.toggle_breakpoint(key, line_b, condition=f"{opt_b} == {val_a}")
        st_b_neg = sess.continue_run()
        assert st_b_neg["status"]["stop_reason"] == "halted", (amb_expr, opt_b, st_b_neg["status"])
        sess.toggle_breakpoint(key, line_b)

    # Also verify watchpoint accepts both disambiguated suggestions (`r["m"].buf` vs `m["buf"]`) against distinct values.
    sess.reset()
    sess.add_watchpoint("ram[0]", kind="w", condition='r["m"].buf == 4')
    assert sess.continue_run()["status"]["stop_reason"] == "watchpoint"
    sess.reset()
    sess.clear_watchpoints()
    sess.add_watchpoint("ram[0]", kind="w", condition='m["buf"] == 30')
    assert sess.continue_run()["status"]["stop_reason"] == "watchpoint"
    sess.clear_watchpoints()

    # Deferred evaluation across --script Reset: initial low == 0 -> division by zero deferred until hit.
    sess.reset()
    sess.toggle_breakpoint(key, 3, condition="20 // low == 2")
    sess.reset()  # Re-installs condition while initial low == 0; must not fail Reset.
    st_div = sess.continue_run()
    assert st_div["status"]["active_line"] == 3

    # Hitting line 2 where low == 0 raises DebugError only when breakpoint is reached.
    sess.reset()
    sess.toggle_breakpoint(key, 3)  # remove line 3 bp
    sess.toggle_breakpoint(key, 2, condition="20 // low == 2")
    sess.reset()
    with pytest.raises(DebugError):
        sess.continue_run()


def test_gui_condition_language_rebind_bounds_lazy_and_rejections(tmp_path: Path):
    mcu_file = tmp_path / "cond_mcu.py"
    mcu_file.write_text(
        '''from rpkbin.isasim import MCU

mcu = MCU("gui_cond_lang")
mcu.space("prog", cell_bits=16, size=16, access="ro", reset_state="initialized", init=0, is_program=True)
mcu.space("data", cell_bits=8, size=16, access="rw", reset_state="initialized", init=0)
mcu.reg("pc", bits=4, reset=0, is_pc=True)
mcu.reg("r0", bits=16, reset=0)
mcu.reg("wide", bits=16000, reset=0)
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")

@mcu.instr("0001 aaaa vvvvvvvv", syntax="ST {a}, {v}", cycles=1)
def st(st, a, v):
    st.mem["data"][a] = v

@mcu.instr("0010 0000 0000 0000", syntax="NOP", cycles=1)
def nop(st):
    pass

@mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
def hlt(st):
    st.halt("done")
''',
        encoding="utf-8",
    )
    asm_file = tmp_path / "prog.asm"
    asm_file.write_text("NOP\nNOP\nNOP\nST 4, 0x55\nHLT\n", encoding="utf-8")

    script_file = tmp_path / "setup.py"
    script_file.write_text(
        '''from rpkbin.isasim import Device

class UnpeekableDev(Device):
    def read(self, addr, now):
        return 0x77
    def write(self, addr, value, now):
        pass

def setup(sim):
    sim.attach(UnpeekableDev(), space="data", lo=6, hi=7)
    sim.poke("data[2]", 0x11)
    sim.poke("data[3]", 0x22)
    sim.mem_alias("dyn_alias", "data[2]", bit_order="msb_first")
''',
        encoding="utf-8",
    )

    sess = DebugSession(mcu_file, asm_file, script_path=script_file)
    key = sess._program.main

    # 1. Install-time grammar rejections on toggle_breakpoint and add_watchpoint.
    for bad in (
        'f"{r0=}"',
        "5 / 2 == 2",
        "1.5",
        "(lambda: 1)()",
        "(x := 1)",
        "(1, 2)",
        "bit(value=0, value=1, n=0)",
        "(bit if cycle == 0 else parity)(value=0, value=1, n=0)",
    ):
        with pytest.raises(DebugError):
            sess.toggle_breakpoint(key, 4, condition=bad)
        with pytest.raises(DebugError):
            sess.add_watchpoint("data[4]", kind="w", condition=bad)

    # 2. Lazy short-circuit avoids peeking unpeekable Device cell, handles 65,536-bit ops and 16,000-bit hex.
    sess.toggle_breakpoint(key, 4, condition="m.data[6] == 0")
    with pytest.raises(DebugError):
        sess.continue_run()

    sess.reset()
    huge_val = (1 << 15999) | 0xABCD
    raw_cond = (
        "((1 or m.data[6]) and (bit if cycle == 3 else to_unsigned)(0x85, 7) == 1) "
        f"and (1 << 65535).bit_length() == 65536 and r.wide == {hex(huge_val)}"
    )
    st_set = sess.toggle_breakpoint(key, 4, condition=raw_cond)
    row = next(r for r in st_set["files"][key] if r["line"] == 4)
    assert row["bp_cond"] == raw_cond

    sess.poke("wide", huge_val)
    st_hit = sess.continue_run()
    assert st_hit["status"]["stop_reason"] == "breakpoint"
    assert st_hit["status"]["active_line"] == 4
    sess.toggle_breakpoint(key, 4)

    # 3. Runtime domain / allocation bounds raise DebugError without overflowing memory.
    for expr in ("(1 << 65536) > 0", "(2 ** -1) == 0"):
        sess.reset()
        sess.toggle_breakpoint(key, 4, condition=expr)
        with pytest.raises(DebugError):
            sess.continue_run()
        sess.toggle_breakpoint(key, 4)

        sess.reset()
        sess.clear_watchpoints()
        sess.add_watchpoint("data[4]", kind="w", condition=expr)
        with pytest.raises(DebugError):
            sess.continue_run()
    sess.clear_watchpoints()

    # 4. Dynamic mem_alias rebinding via --script across reset() and reload().
    sess.reset()
    bp_cond = "dyn_alias == 0x22"
    wp_cond = "m.dyn_alias == 0x22"
    sess.toggle_breakpoint(key, 4, condition=bp_cond)
    sess.add_watchpoint("data[4]", kind="w", condition=wp_cond)

    # With dyn_alias -> data[2] (0x11), neither condition triggers.
    st_miss = sess.continue_run()
    assert st_miss["status"]["stop_reason"] == "halted"

    # Rebind dyn_alias -> data[3] (0x22) in --script and reset().
    script_file.write_text(
        '''from rpkbin.isasim import Device

class UnpeekableDev(Device):
    def read(self, addr, now):
        return 0x77
    def write(self, addr, value, now):
        pass

def setup(sim):
    sim.attach(UnpeekableDev(), space="data", lo=6, hi=7)
    sim.poke("data[2]", 0x11)
    sim.poke("data[3]", 0x22)
    sim.mem_alias("dyn_alias", "data[3]", bit_order="msb_first")
''',
        encoding="utf-8",
    )
    st_rst = sess.reset()
    assert next(r for r in st_rst["files"][key] if r["line"] == 4)["bp_cond"] == bp_cond
    assert st_rst["watchpoints"][0]["condition"] == wp_cond

    st_bp = sess.continue_run()
    assert st_bp["status"]["stop_reason"] == "breakpoint"
    assert st_bp["status"]["active_line"] == 4
    st_wp = sess.continue_run()
    assert st_wp["status"]["stop_reason"] == "watchpoint"

    # Update asm with a leading comment and reload(); raw condition text & bindings survive relocation.
    asm_file.write_text("; relocated\nNOP\nNOP\nNOP\nST 4, 0x55\nHLT\n", encoding="utf-8")
    st_rel = sess.reload()
    assert next(r for r in st_rel["files"][key] if r["line"] == 5)["bp_cond"] == bp_cond
    assert st_rel["watchpoints"][0]["condition"] == wp_cond
    st_rel_bp = sess.continue_run()
    assert st_rel_bp["status"]["stop_reason"] == "breakpoint"
    assert st_rel_bp["status"]["active_line"] == 5
    assert sess.continue_run()["status"]["stop_reason"] == "watchpoint"


def test_gui_oversized_mem_alias_rejected_before_packing(tmp_path: Path, monkeypatch):
    mcu_file = tmp_path / "big_alias_mcu.py"
    mcu_file.write_text(
        '''from rpkbin.isasim import MCU

mcu = MCU("gui_big_alias")
mcu.space("prog", cell_bits=16, size=8, access="ro", reset_state="initialized", init=0, is_program=True)
mcu.space("ram", cell_bits=8, size=8200, access="rw", reset_state="initialized", init=0)
mcu.reg("pc", bits=3, reset=0, is_pc=True)
mcu.mem_alias("over_8193", "ram[0:8193]", endian="big", bit_order="msb_first")
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")

@mcu.instr("0001 0000 0000 0000", syntax="NOP", cycles=1)
def nop(st):
    pass

@mcu.instr("1111 1111 1111 1111", syntax="HLT", cycles=1)
def hlt(st):
    st.halt("done")
''',
        encoding="utf-8",
    )
    asm_file = tmp_path / "prog.asm"
    asm_file.write_text("NOP\nHLT\n", encoding="utf-8")

    sess = DebugSession(mcu_file, asm_file)
    key = sess._program.main
    sess.toggle_breakpoint(key, 2, condition="m.over_8193 == 0")

    run_read_calls = 0
    run_pack_calls = 0
    snapshot_pack_calls = 0
    in_run = False
    orig_read = sess.sim._mem_alias_read
    orig_pack = sess.sim._pack_mem_alias_cells
    orig_run = sess.sim.run

    def spy_read(info):
        nonlocal run_read_calls
        if in_run:
            run_read_calls += 1
        return orig_read(info)

    def spy_pack(info, cells):
        nonlocal run_pack_calls, snapshot_pack_calls
        if in_run:
            run_pack_calls += 1
        else:
            snapshot_pack_calls += 1
        return orig_pack(info, cells)

    def spy_run(*args, **kwargs):
        nonlocal in_run
        in_run = True
        try:
            return orig_run(*args, **kwargs)
        finally:
            in_run = False

    monkeypatch.setattr(sess.sim, "_mem_alias_read", spy_read)
    monkeypatch.setattr(sess.sim, "_pack_mem_alias_cells", spy_pack)
    monkeypatch.setattr(sess.sim, "run", spy_run)
    with pytest.raises(DebugError, match="exceeds condition limit"):
        sess.continue_run()
    assert snapshot_pack_calls >= 1
    assert run_read_calls == 0
    assert run_pack_calls == 0

    # Lazy short-circuit keeps evaluation working without touching m.over_8193,
    # and explicit reset / non-condition sim.read remains unaffected.
    sess.reset()
    sess.toggle_breakpoint(key, 2, condition="1 or m.over_8193 == 0")
    st_lazy = sess.continue_run()
    assert st_lazy["status"]["stop_reason"] == "breakpoint"
    assert run_read_calls == 0
    assert run_pack_calls == 0
    assert sess.sim.read("over_8193") == 0


def test_gui_static_html_short_placeholders_and_hover_tooltips():
    from html.parser import HTMLParser

    html_path = ROOT / "rpkbin" / "app" / "isasim_gui" / "static" / "index.html"
    html = html_path.read_text(encoding="utf-8")

    inputs_by_id: dict[str, dict[str, str | None]] = {}
    has_ui_tooltip = False

    class _Inspector(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            nonlocal has_ui_tooltip
            attr_map = dict(attrs)
            if tag == "div" and attr_map.get("id") == "uiHoverTooltip":
                has_ui_tooltip = True
            if tag == "input" and attr_map.get("id"):
                inputs_by_id[attr_map["id"]] = attr_map

    _Inspector().feed(html)
    assert has_ui_tooltip

    tooltip_input_ids = (
        "watchTargetInput",
        "watchCondInput",
        "condPopoverInput",
        "regFilterInput",
        "constFilterInput",
        "regAliasFilterInput",
        "memFilter",
        "memAliasModalSearch",
    )
    for elem_id in tooltip_input_ids:
        attrs = inputs_by_id[elem_id]
        assert attrs.get("data-tooltip"), f"{elem_id} missing data-tooltip"
        assert "title" not in attrs, f"{elem_id} should not mix title with data-tooltip"

    assert inputs_by_id["watchTargetInput"]["placeholder"] == "Target: reg, space[0x10]"
    assert inputs_by_id["watchCondInput"]["placeholder"] == "Cond (opt): reg == 5"
    assert "width: min(240px, 42vw);" in html
    assert "Condition (optional):" not in html
    assert "regFilterEl.title =" not in html
    assert "positionFloatingTooltip" in html
    assert "showUiHoverTooltip" in html
    assert "pop.offsetWidth" in html
    assert 'data-tooltip="${escapeAttr(point.target)}"' in html
    assert 'data-tooltip="${escapeAttr(point.condition)}"' in html
    assert 'data-tooltip="Click to edit condition: ${escapeAttr(r.bp_cond)}"' in html
