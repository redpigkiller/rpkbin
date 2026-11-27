from http.client import HTTPConnection
from pathlib import Path
import re
from threading import Thread

import pytest

from rpkbin.app.isasim_gui.session import DebugSession, relocate_breakpoints
from rpkbin.isasim import assemble, load_mcu


@pytest.fixture
def model(tmp_path: Path) -> Path:
    path = tmp_path / "opt_in.py"
    path.write_text(
        """from rpkbin.isasim import MCU

mcu = MCU("debug GUI fixture")
mcu.space("code", cell_bits=8, size=64, access="ro",
          reset_state="uninitialized", init=0, is_program=True)
mcu.space("ram", cell_bits=8, size=4, access="rw",
          reset_state="uninitialized", init=0)
mcu.reg("IP", bits=6, reset=0, is_pc=True)
mcu.reg("R", bits=8, reset=0, fields={"low": (3, 0)})
mcu.reg("F", bits=4, reset=0, role="flag", fields={"mode": (1, 0)})
mcu.reg("ordinary_bit", bits=1, reset=0)
mcu.reg("saved", bits=6, reset=0)
mcu.alias("first", "R.low")
mcu.alias("whole", "R")
mcu.alias("other_low", "R.low")
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")

@mcu.instr("00000000", syntax="CALL", cycles=1)
def ordinary(st):
    pass

@mcu.instr("10aaaaaa", syntax="ENTER {a}", cycles=1, kind="call")
def enter(st, a):
    st.reg["saved"] = st.next_pc
    st.next_pc = a

@mcu.instr("00000010", syntax="LEAVE", cycles=1, kind="return")
def leave(st):
    st.next_pc = st.reg["saved"]

@mcu.instr("00000011", syntax="INC", cycles=1)
def increment(st):
    st.reg["R.low"] += 1

@mcu.instr("00000001", syntax="END", cycles=1)
def end(st):
    st.halt("end")
""",
        encoding="utf-8",
    )
    return path


def program(tmp_path, source):
    path = tmp_path / "main.asm"
    path.write_text(source, encoding="utf-8")
    return path


def test_declared_instruction_kinds_and_breakpoints(model, tmp_path):
    path = program(tmp_path, "CALL\nENTER sub\nEND\nsub: INC\nLEAVE\n")
    session = DebugSession(model, path, call_mnemonics=("CALL",))
    assert session.kind_of(0) is None  # Metadata takes precedence over fallback.
    assert session.kind_of(1) == "call"
    assert session.kind_of(4) == "return"
    assert session.step_over()["status"]["pc"] == 1
    session.toggle_breakpoint("", 4)
    assert session.step_over()["status"]["stop_reason"] == "breakpoint"
    assert session.sim.state.pc == 3
    assert session.step_out()["status"]["pc"] == 2
    assert session.step_out()["status"]["stop_reason"] == "halted"
    session.reset()
    assert session.step_out(max_steps=1)["status"]["stop_reason"] == "max_steps"


def test_legacy_model_disables_depth_stepping_without_explicit_fallback(
    model, tmp_path
):
    legacy = model.with_name("legacy.py")
    source = model.read_text(encoding="utf-8")
    for argument in (', kind="call"', ', kind="return"', ', role="flag"'):
        source = source.replace(argument, "")
    source = "\n".join(
        line for line in source.splitlines() if not line.startswith("mcu.alias(")
    )
    legacy.write_text(source, encoding="utf-8")
    path = program(tmp_path, "ENTER sub\nEND\nsub: LEAVE\n")
    session = DebugSession(legacy, path)
    state = session.step_over()
    assert state["status"]["pc"] == 0
    assert not state["status"]["can_step_depth"]
    assert "mnemonics" in state["status"]["step_depth_reason"]
    assert not any(reg["is_flag"] for reg in state["registers"])
    assert state["aliases"] == []
    assert session.step_in()["status"]["pc"] == 2

    session = DebugSession(
        legacy, path, call_mnemonics=("enter",), return_mnemonics=("leave",)
    )
    assert session.get_state()["status"]["can_step_depth"]
    assert session.step_over()["status"]["pc"] == 1


def test_missing_instruction_kind_api_uses_only_explicit_mnemonics(
    model, tmp_path, monkeypatch
):
    from rpkbin.isasim import Simulator

    monkeypatch.delattr(Simulator, "instruction_kind")
    path = program(tmp_path, "ENTER sub\nEND\nsub: LEAVE\n")
    assert not DebugSession(model, path).get_state()["status"]["can_step_depth"]
    session = DebugSession(
        model, path, call_mnemonics=("ENTER",), return_mnemonics=("LEAVE",)
    )
    assert session.step_over()["status"]["pc"] == 1


def test_flag_roles_and_aliases_for_poke_conditions_watch_and_trace(model, tmp_path):
    path = program(tmp_path, "CALL\nINC\nEND\n")
    session = DebugSession(model, path)
    state = session.poke("first", 5)
    assert [(row["name"], row["target"]) for row in state["aliases"]] == [
        ("first", "R.low"),
        ("whole", "R"),
        ("other_low", "R.low"),
    ]
    assert [reg["name"] for reg in state["registers"] if reg["is_flag"]] == ["F"]
    assert session.sim.read("R.low") == 5
    assert session.sim.recent_events(1)[0].writes[0].name == "R.low"
    assert "first" in state["trace"][0]["changes"]

    session.toggle_breakpoint("", 2, condition="first == 5")
    assert session.continue_run()["status"]["stop_reason"] == "breakpoint"
    session.add_watchpoint("first", kind="w", condition="other_low == 6")
    state = session.step_in()
    assert state["status"]["stop_reason"] == "watchpoint"
    assert "first" in state["status"]["detail"]


def test_snapshot_ring_and_device_without_snapshots(model, tmp_path):
    path = program(tmp_path, "INC\nINC\nEND\n")
    session = DebugSession(model, path, max_history=1)
    session.step_in()
    session.step_in()
    assert session.step_back()["status"]["pc"] == 1
    assert not session.get_state()["status"]["can_step_back"]
    assert DebugSession(model, path).max_history == 200

    script = tmp_path / "setup.py"
    script.write_text(
        "from rpkbin.isasim import Device\n"
        'def setup(sim):\n    sim.attach(Device(), space="ram", lo=0, hi=1)\n',
        encoding="utf-8",
    )
    session = DebugSession(model, path, script_path=script)
    assert "get_state/set_state" in session.get_state()["status"]["step_back_reason"]
    state = session.step_in()
    assert state["status"]["pc"] == 1
    assert not state["status"]["can_step_back"]
    assert "get_state/set_state" in state["status"]["step_back_reason"]
    assert session.step_in()["status"]["pc"] == 2


@pytest.mark.parametrize("expanded", [False, True])
def test_load_bundles_and_group_repeated_include(model, tmp_path, expanded):
    main = tmp_path / "archived.asm"
    included = tmp_path / "part.inc"
    included.write_text("INC\n", encoding="utf-8")
    text = (
        'K = 7\nCALL\n.include "part.inc"\n.include "part.inc"\nEND\n'
        if expanded
        else "K = 7\nCALL\nEND\n"
    )
    image = assemble(load_mcu(str(model)), text, filename=main.as_posix())
    bundle = tmp_path / "program.isa.json"
    image.save(str(bundle))
    included.unlink()
    session = DebugSession(model, bundle)
    assert session.get_state()["constants"] == {"K": 7}
    assert session.get_state()["status"]["active_file"] == main.as_posix()
    if expanded:
        state = session.toggle_breakpoint("part.inc", 1)
        assert state["files"]["part.inc"][0]["addresses"] == [1, 2]
        assert len(session.sim.list_breakpoints()) == 1
        assert session.continue_run()["status"]["active_file"] == "part.inc"
        assert session.continue_run()["status"]["pc"] == 2
        assert session.get_state()["files"]["part.inc"][0]["is_pc"]
        assert session.reload()["status"]["error"] is None
        assert len(session.sim.list_breakpoints()) == 1
        session.toggle_breakpoint("part.inc", 1)
        assert not session.sim.list_breakpoints()
    bundle.write_text("{}", encoding="utf-8")
    state = session.reload()
    assert state["status"]["error"]
    assert state["constants"] == {"K": 7}


def test_relocate_breakpoints_at_file_edges():
    source = ["CALL", "INC", "INC", "END"]
    points = {1: None, 4: "r.R == 2"}
    assert relocate_breakpoints(source, source, points) == points
    assert relocate_breakpoints(source, [*source, "INC"], points) == {1: None}
    assert relocate_breakpoints(source, ["INC", *source], points) == {5: "r.R == 2"}


def test_cli_declares_fallbacks_and_default_history():
    from rpkbin.app.isasim_gui.cli import build_parser

    parser = build_parser()
    assert parser.parse_args(["cpu.py", "test.asm"]).max_history == 200
    args = parser.parse_args(
        [
            "cpu.py",
            "test.isa.json",
            "--call-mnemonics",
            "CALL,JSR",
            "--return-mnemonics",
            "RET",
        ]
    )
    assert args.call_mnemonics == ("CALL", "JSR")
    assert args.return_mnemonics == ("RET",)


def test_mutating_routes_require_same_origin_host_and_token(model, tmp_path):
    from rpkbin.app.isasim_gui.server import create_server

    session = DebugSession(model, program(tmp_path, "CALL\nEND\n"))
    with pytest.raises(ValueError, match="127.0.0.1"):
        create_server(session, host="localhost", port=0)
    with create_server(session, port=0) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"

        def request(method, path, body=None, **headers):
            with_headers = {"Content-Type": "application/json", **headers}
            conn = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                conn.request(method, path, body, with_headers)
                response = conn.getresponse()
                return response.status, response.read().decode("utf-8")
            finally:
                conn.close()

        try:
            status, homepage = request("GET", "/")
            assert status == 200
            token = re.search(
                r'<meta name="isasim-token" content="([^"]+)">', homepage
            )[1]
            headers = {"Origin": origin, "X-Isasim-Token": token}
            assert len(token) >= 32
            assert request("POST", "/api/reload", "{}")[0] == 403
            assert request("POST", "/api/reload", "{}", Origin=origin)[0] == 403
            assert (
                request(
                    "POST", "/api/set_pc", '{"line":2}', **{"X-Isasim-Token": token}
                )[0]
                == 403
            )
            assert (
                request(
                    "POST",
                    "/api/poke",
                    '{"target":"first","value":7}',
                    **{**headers, "X-Isasim-Token": "wrong"},
                )[0]
                == 403
            )
            assert (
                request("POST", "/api/reload", "{}", **{**headers, "Origin": "null"})[0]
                == 403
            )
            assert (
                request(
                    "POST", "/api/reload", "{}", **{**headers, "Host": "evil.example"}
                )[0]
                == 403
            )
            assert (
                request(
                    "POST",
                    "/api/reload",
                    "{}",
                    **{**headers, "Host": f"localhost:{server.server_port}"},
                )[0]
                == 403
            )
            assert session.sim.state.pc == 0
            assert session.sim.read("first") == 0
            assert (
                request("POST", "/api/poke", '{"target":"first","value":7}', **headers)[
                    0
                ]
                == 200
            )
            assert session.sim.read("first") == 7
            assert request("POST", "/api/set_pc", '{"line":2}', **headers)[0] == 200
            assert session.sim.state.pc == 1
        finally:
            server.shutdown()
            thread.join(timeout=2)


def test_v12_loads_prebuilt_mcu(model, tmp_path, monkeypatch):
    import sys
    from types import ModuleType

    module = ModuleType("gui_v12_prebuilt_fixture")
    module.mcu = load_mcu(str(model))
    monkeypatch.setitem(sys.modules, module.__name__, module)
    model.write_text(f"from {module.__name__} import mcu\n", encoding="utf-8")
    state = DebugSession(model, program(tmp_path, "END\n")).get_state()
    registers = {row["name"]: row for row in state["registers"]}
    assert registers["R"]["fields"][0]["target"] == "R.low"
    assert registers["F"]["is_flag"]
    assert state["aliases"][0]["target"] == "R.low"
    assert state["memory"]["spaces"] == ["code", "ram"]


def test_v12_reports_unvalidated_mcu_after_setup(model, tmp_path, capsys, monkeypatch):
    import sys
    from types import ModuleType

    from rpkbin.app.isasim_gui.cli import main

    path = program(tmp_path, "END\n")
    bridge = ModuleType("gui_v12_setup_fixture")
    monkeypatch.setitem(sys.modules, bridge.__name__, bridge)
    setup = tmp_path / "setup.py"
    setup.write_text("def setup(sim):\n    pass\n", encoding="utf-8")
    session = DebugSession(model, path, script_path=setup)
    model.write_text(
        model.read_text(encoding="utf-8")
        + "\nimport gui_v12_setup_fixture\ngui_v12_setup_fixture.mcu = mcu\n",
        encoding="utf-8",
    )
    setup.write_text(
        "from gui_v12_setup_fixture import mcu\ndef setup(sim):\n"
        '    mcu.reg("extra", bits=1, reset=0)\n',
        encoding="utf-8",
    )
    error = session.reload()["status"]["error"]
    assert "MCUDefinitionError" in error
    assert "validation" in error
    assert main([str(model), str(path), "--script", str(setup), "--port", "0"]) == 2
    assert "MCUDefinitionError" in capsys.readouterr().err


@pytest.mark.parametrize("action", ["step_over", "step_out", "run_to_cursor"])
def test_v12_prechecks_setup_breakpoints(model, tmp_path, action):
    path = program(tmp_path, "ENTER sub\nEND\nsub: INC\nLEAVE\n")
    setup = tmp_path / "setup.py"
    setup.write_text(
        "def setup(sim):\n    sim.add_breakpoint(2, temporary=True)\n",
        encoding="utf-8",
    )
    session = DebugSession(model, path, script_path=setup)
    if action == "step_out":
        session.step_in()  # The setup breakpoint is already at the current PC.
    args = ("", 2) if action == "run_to_cursor" else ()
    state = getattr(session, action)(*args)
    assert state["status"]["stop_reason"] == "breakpoint"
    assert "id=1" in state["status"]["detail"]
    assert state["status"]["pc"] == 2
    assert state["status"]["steps"] == 1
    assert session.sim.next_breakpoint() == 1
    assert len(session.sim.list_breakpoints()) == 1


def test_v12_reports_breakpoint_condition_error(model, tmp_path):
    session = DebugSession(model, program(tmp_path, "CALL\nEND\n"))
    session.sim.add_breakpoint(0, condition="1 // 0")
    state = session.step_over()
    assert "DebugError" in state["status"]["error"]
    assert "division" in state["status"]["error"]
    assert state["status"]["steps"] == 0


@pytest.mark.parametrize("bundle_version", [None, 1, 2])
def test_v12_source_rows_follow_records_in_expansion_order(
    model, tmp_path, bundle_version, monkeypatch
):
    from rpkbin.isasim import Assembly

    def unavailable_listing(self):
        pytest.fail("GUI source rows must use Assembly.records()")

    monkeypatch.setattr(Assembly, "listing", unavailable_listing)
    include = tmp_path / "part.inc"
    include.write_text("INC\n", encoding="utf-8")
    repeated = bundle_version != 1
    instruction = '.include "part.inc"\n' if repeated else "INC\n"
    path = program(
        tmp_path,
        ".org 8\n" + instruction + ".org 1\n" + instruction + "data: .cell 7\nEND\n",
    )
    main_key = path.resolve().as_posix()
    if bundle_version is not None:
        image = assemble(
            load_mcu(str(model)), path.read_text(encoding="utf-8"), filename=main_key
        )
        bundle = tmp_path / "program.isa.json"
        image.save(str(bundle))
        assert f'"format": "isasim-assembly-{bundle_version}"' in bundle.read_text(
            encoding="utf-8"
        )
        path.unlink()
        include.unlink()
        path = bundle
    session = DebugSession(model, path)
    state = session.get_state()
    assert state["status"]["active_file"] == main_key
    source_key, line = ("part.inc", 1) if repeated else (main_key, 2)
    assert state["files"][source_key][line - 1]["addresses"] == (
        [8, 1] if repeated else [8]
    )
    assert state["files"][source_key][line - 1]["cells_hex"] == "03"
    assert state["files"][main_key][4]["addr"] == 2
    assert not state["files"][main_key][4]["is_instr"]
    assert session.set_pc(source_key, line)["status"]["pc"] == 8
    if repeated:
        session.poke("IP", 1)
        assert session.get_state()["files"][source_key][0]["is_pc"]


def test_memory_shows_cells_loaded_by_setup(model, tmp_path, monkeypatch):
    setup = tmp_path / "setup.py"
    setup.write_text(
        'def setup(sim):\n    sim.load_cells("ram", {0: 17, 2: 34})\n',
        encoding="utf-8",
    )
    session = DebugSession(model, program(tmp_path, "END\n"), script_path=setup)
    queries = []
    initialized_ranges = session.sim.initialized_ranges

    def record_query(space):
        queries.append(space)
        return initialized_ranges(space)

    monkeypatch.setattr(session.sim, "initialized_ranges", record_query)
    state = session.get_state(
        memory_space="ram", memory_filter="3, 0, 2", hide_uninit=True
    )
    cells = state["memory"]["cells"]
    assert [(cell["addr"], cell["value"], cell["hex"]) for cell in cells] == [
        (0, 17, "0x11"),
        (2, 34, "0x22"),
    ]
    assert queries == ["ram"]


@pytest.fixture
def memory_writer(model, tmp_path):
    with model.open("a", encoding="utf-8") as stream:
        stream.write(
            '\n@mcu.instr("00000100", syntax="STORE", cycles=1)\n'
            'def store(st):\n    st.mem["ram"][2] = 7\n'
        )
    return DebugSession(model, program(tmp_path, "STORE\nEND\n"))


def test_memory_initializes_cells_on_poke_and_instruction_write(memory_writer):
    memory_writer.set_memory_view(memory_space="ram", hide_uninit=True)
    cells = memory_writer.poke("ram[1]", 0)["memory"]["cells"]
    assert [(cell["addr"], cell["hex"]) for cell in cells] == [(1, "0x00")]
    cells = memory_writer.step_in()["memory"]["cells"]
    assert [(cell["addr"], cell["hex"]) for cell in cells] == [(1, "0x00"), (2, "0x07")]


def test_memory_step_back_restores_uninitialized_cells(memory_writer):
    memory_writer.set_memory_view(memory_space="ram")
    memory_writer.poke("ram[1]", 5)
    memory_writer.step_in()
    cells = memory_writer.step_back()["memory"]["cells"]
    assert cells[1]["hex"] == "0x05"
    assert (cells[2]["initialized"], cells[2]["hex"]) == (False, "--")
    cells = memory_writer.step_back()["memory"]["cells"]
    assert (cells[1]["initialized"], cells[1]["hex"]) == (False, "--")


@pytest.fixture
def preset_model(model):
    with model.open("a", encoding="utf-8") as stream:
        stream.write(
            '\nmcu.space("preset", cell_bits=8, size=4, access="rw", '
            'reset_state="initialized", init=42)\n'
        )
    return model


def test_memory_device_cell_keeps_unavailable_display(preset_model, tmp_path):
    setup = tmp_path / "setup.py"
    setup.write_text(
        'from rpkbin.isasim import Device\ndef setup(sim):\n'
        '    sim.attach(Device(), space="preset", lo=1, hi=2)\n',
        encoding="utf-8",
    )
    session = DebugSession(preset_model, program(tmp_path, "END\n"), script_path=setup)
    cells = session.get_state(memory_space="preset")["memory"]["cells"]
    assert (cells[1]["hex"], cells[1]["value"]) == ("--", None)
    assert not cells[1]["initialized"]
    cells = session.get_state(hide_uninit=True)["memory"]["cells"]
    assert [cell["addr"] for cell in cells] == [0, 2, 3]


def test_memory_reset_initialized_space_shows_all_cells(preset_model, tmp_path):
    session = DebugSession(preset_model, program(tmp_path, "END\n"))
    cells = session.get_state(memory_space="preset", hide_uninit=True)["memory"]["cells"]
    assert [(cell["addr"], cell["initialized"], cell["hex"]) for cell in cells] == [
        (addr, True, "0x2a") for addr in range(4)
    ]
