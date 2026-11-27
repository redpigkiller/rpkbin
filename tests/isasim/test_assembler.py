"""Independent encodings and source journeys for the native toolchain."""

import inspect
import json
from io import StringIO

import pytest

from rpkbin import isasim as isa
from rpkbin.isasim.cli import main
from rpkbin.isasim.repl import run_repl


def machine():
    m = isa.MCU("source_demo")
    m.space(
        "prog",
        cell_bits=8,
        size=64,
        access="ro",
        reset_state="uninitialized",
        init=0,
        is_program=True,
    )
    m.reg("cursor", bits=8, reset=0, is_pc=True)
    m.reg("value", bits=8, reset=0)
    for name in (
        "on_undefined_opcode",
        "on_out_of_range",
        "on_write_to_readonly",
        "on_uninitialized_read",
    ):
        getattr(m, name)("stop")

    @m.instr("0001 iiii", syntax="ADD {i}", cycles=1)
    def add(st, i):
        st.reg["value"] += i

    @m.instr(
        "0010 rrrr",
        syntax="BR {r:#x}",
        cycles=2,
        operands={"r": isa.Operand(signed=True, relative="next_pc")},
    )
    def branch(st, r):
        st.next_pc += isa.helpers.to_signed(r, 4)

    @m.instr("0011 aaaa", syntax="GO {a}", cycles=1)
    def jump(st, a):
        st.next_pc = a

    @m.instr("0101 aabb cccc aabb", syntax="MIX {a}, {b}, {c}", cycles=1)
    def mix(st, a, b, c):
        st.reg["value"] = a + b + c

    @m.instr(
        "0110 iiii",
        syntax="NEG {i}",
        cycles=1,
        operands={"i": isa.Operand(signed=True)},
    )
    def negative(st, i):
        st.reg["value"] = isa.helpers.to_signed(i, 4)

    @m.instr("1111 0000", syntax="END", cycles=1)
    def end(st):
        st.halt("done")

    return m


def test_forward_labels_constants_data_and_execution():
    source = "COUNT = 2\nstart: ADD COUNT\nBR finish\n.cell 7, start\nfinish: END\n"
    image = isa.assemble(machine(), source, filename="main.asm")
    assert image.cells == {0: 0x12, 1: 0x22, 2: 7, 3: 0, 4: 0xF0}
    assert image.symbols == {"start": 0, "finish": 4}
    sim = isa.Simulator(machine())
    sim.load_assembly(image)
    result = sim.run(max_steps=10)
    assert (result.stop_reason, result.steps, result.cycles, result.pc) == (
        "halted",
        3,
        4,
        5,
    )
    assert sim.read("value") == 2
    assert sim.source_line(4) == ("main.asm", 5)


@pytest.mark.parametrize(
    "source,cells",
    [
        ("MIX 10, 5, 3", {0: 0x59, 1: 0x39}),
        (".org 4\nloop: ADD 1\nBR loop", {4: 0x11, 5: 0x2E}),
        ("NEG -8\nNEG 7", {0: 0x68, 1: 0x67}),
        ("add (1 << 2) | 3 ; comment\nEND", {0: 0x17, 1: 0xF0}),
        ("A = B + 1\nB = 2\nADD A", {0: 0x13}),
        (".cell 0b101, 0o7, (9 // 2) ^ 1, ~(-1)", {0: 5, 1: 7, 2: 5, 3: 0}),
    ],
)
def test_hand_calculated_encodings(source, cells):
    assert isa.assemble(machine(), source).cells == cells


@pytest.mark.parametrize(
    "source,reason",
    [
        ("ADD 16", "range"),
        ("ADD -1", "range"),
        ("NEG -9", "range"),
        ("NEG 8", "range"),
        ("BR 20", "range"),
        ("GO missing", "unknown"),
        ("A = B\nB = A\nADD A", "cyclic"),
        ("same: END\nsame: END", "duplicate"),
        ("A = 1\nA: END", "duplicate"),
        ("ADD 1\n.org 0\nEND", "overlap"),
        (".cell -1", "range"),
        (".org 64\nEND", "range"),
        (".org later\nlater: END", "layout"),
        ("ADD __import__('os')", "expression"),
        ("ADD 1 / 2", "expression"),
        ("ADD 1 // 0", "zero"),
        ("ADD True", "expression"),
        ("ADD 1 << -1", "shift"),
        ("ADD", "instruction"),
        ("NO_SUCH 1", "instruction"),
        (".unknown 1", "directive"),
        ("ADD 1, 2", "expression"),
    ],
)
def test_source_errors_have_file_line_and_reason(source, reason):
    with pytest.raises(isa.AssemblyError) as raised:
        isa.assemble(machine(), source, filename="bad.asm")
    message = str(raised.value)
    assert "bad.asm:" in message and reason in message.lower()


def test_non_byte_cells_and_dont_care_bits():
    m = isa.MCU("odd_cells")
    m.space(
        "prog",
        cell_bits=5,
        size=12,
        access="ro",
        reset_state="initialized",
        init=0,
        is_program=True,
    )
    m.reg("cursor", bits=4, reset=0, is_pc=True)
    for name in (
        "on_undefined_opcode",
        "on_out_of_range",
        "on_write_to_readonly",
        "on_uninitialized_read",
    ):
        getattr(m, name)("stop")

    @m.instr("1-aaa aa-00", syntax="TAKE {a}", cycles=1)
    def take(st, a):
        st.halt(str(a))

    image = isa.assemble(m, "TAKE 19")
    assert image.cells == {0: 20, 1: 24}
    sim = isa.Simulator(m)
    sim.load_assembly(image)
    assert sim.disassemble(0)[0] == (0, "TAKE 19", 2)
    assert sim.step().stop_reason == "halted"


def test_bundle_round_trip_and_listing(tmp_path):
    m = machine()
    image = isa.assemble(m, "MIX 10, 5, 3\nEND", filename="main.asm")
    path = tmp_path / "main.isa.json"
    image.save(path)
    restored = isa.Assembly.load(machine(), path)
    assert restored.cells == image.cells
    assert "MIX 10, 5, 3" in restored.listing()
    assert "59 39" in restored.listing()
    assert path.read_bytes()[:3] != b"\xef\xbb\xbf"
    assert b"\r" not in path.read_bytes()
    exposed = image.cells
    exposed[0] = 0
    assert image.cells[0] == 0x59


@pytest.mark.parametrize("field", ["cells", "source", "isa_hash"])
def test_bundle_mismatch_is_rejected(tmp_path, field):
    path = tmp_path / "bad.isa.json"
    isa.assemble(machine(), "END").save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data[field] = "corrupted"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(isa.LoadError):
        isa.Assembly.load(machine(), path)


def test_wrong_machine_load_is_atomic():
    sim = isa.Simulator(machine())
    sim.load_cells("prog", {0: 0x11})
    other = machine()
    other._spaces["prog"]["cell_bits"] = 4
    with pytest.raises((isa.LoadError, isa.MCUDefinitionError)):
        sim.load_assembly(isa.assemble(other, "END"))
    assert sim.read("prog[0]") == 0x11


def test_source_context_breakpoints_reset_and_stale_program():
    sim = isa.Simulator(machine())
    image = isa.assemble(
        machine(), "; heading\nstart: ADD 3\nADD 4\nEND\n", filename="main.asm"
    )
    sim.load_assembly(image)
    assert sim.source_context(context=0) == [(2, "start: ADD 3", True)]
    sim.add_breakpoint("main.asm:3")
    assert sim.run(max_steps=10).stop_reason == "breakpoint"
    assert sim.state.pc == 1 and sim.read("value") == 3
    sim.reset()
    assert sim.source_line(0) == ("main.asm", 2)
    with pytest.raises(isa.TargetError):
        sim.add_breakpoint(":1")
    sim.load_cells("prog", {0: 0xF0})
    assert sim.source_line(0) is None


def test_repl_shows_source_and_resolves_line_breakpoints():
    sim = isa.Simulator(machine())
    sim.load_assembly(isa.assemble(machine(), "ADD 3\nADD 4\nEND", filename="main.asm"))
    out = StringIO()
    run_repl(sim, StringIO("source\nbreak :2\nrun\nwhere\nstep\nquit\n"), out)
    text = out.getvalue()
    assert "Stopped: breakpoint" in text and "main.asm:2" in text
    assert "ADD 4" in text and "ADD 3" in text
    assert sim.read("value") == 7


def test_relative_from_current_pc_is_explicit():
    m = machine()
    m._instructions[1]["operands"] = {"r": isa.Operand(signed=True, relative="pc")}
    assert isa.assemble(m, ".org 3\nBR 1").cells == {3: 0x2E}


def test_assembler_never_calls_semantics():
    m = machine()

    def forbidden(st, i):
        pytest.fail("assembler executed instruction callback")

    m._instructions[0]["fn"] = forbidden
    assert isa.assemble(m, "ADD 1").cells == {0: 0x11}


def test_cli_assemble_and_direct_source(tmp_path, capsys):
    path = tmp_path / "device.py"
    # The descriptor is standalone and importable by the installed CLI.
    path.write_text(
        "from rpkbin.isasim import MCU\nmcu=MCU('cli_source')\n"
        "mcu.space('prog',cell_bits=5,size=16,access='ro',reset_state='uninitialized',init=0,is_program=True)\n"
        "mcu.reg('cursor',bits=5,reset=0,is_pc=True)\n"
        "mcu.on_undefined_opcode('stop')\nmcu.on_out_of_range('stop')\n"
        "mcu.on_write_to_readonly('stop')\nmcu.on_uninitialized_read('stop')\n"
        "@mcu.instr('10000',syntax='END',cycles=1)\ndef finish(st): st.halt('done')\n",
        encoding="utf-8",
    )
    source = tmp_path / "main.asm"
    source.write_text("start: END", encoding="utf-8")
    output = tmp_path / "main.isa.json"
    listing = tmp_path / "main.lst"
    assert (
        main(
            [
                "assemble",
                str(path),
                str(source),
                "-o",
                str(output),
                "--listing",
                str(listing),
            ]
        )
        == 0
    )
    assert output.exists() and "END" in listing.read_text(encoding="utf-8")
    for program in (source, output):
        assert main(["run", str(path), str(program), "--max-steps", "2"]) == 0
        assert "RESULT stop_reason=halted" in capsys.readouterr().out


def test_adjacent_placeholders_need_explicit_delimiters():
    m = machine()
    m._instructions[3]["syntax"] = "MIX {a}{b}{c}"
    with pytest.raises(isa.AssemblyError, match="delimiter"):
        isa.assemble(m, "MIX 123")


def test_non_utf8_source_reports_a_source_error():
    with pytest.raises(isa.AssemblyError, match="UTF-8"):
        isa.assemble(machine(), ";\ud800\nEND")


def test_legacy_symbol_with_a_colon_keeps_precedence(tmp_path):
    path = tmp_path / "symbols.json"
    path.write_text(json.dumps({"symbols": {"place:3": 0}}), encoding="utf-8")
    sim = isa.Simulator(machine())
    sim.load_symbols(path)
    assert sim.add_breakpoint("place:3") == 1


@pytest.mark.parametrize(
    "declaration",
    [
        "A = 1\nB = A + A\nC = B + B\nADD C",
        ".org\t2\n.cell\t3\nEND",
    ],
)
def test_constant_dependencies_and_directive_whitespace(declaration):
    image = isa.assemble(machine(), declaration)
    assert image.cells in ({0: 0x14}, {2: 3, 3: 0xF0})


def test_utf8_bom_is_accepted_at_file_boundary(tmp_path):
    from rpkbin.isasim.assembler import _assemble_file

    path = tmp_path / "bom.asm"
    path.write_bytes(b"\xef\xbb\xbfEND\n")
    assert _assemble_file(machine(), path).cells == {0: 0xF0}


def test_disassembly_uses_declared_signed_and_relative_operands():
    sim = isa.Simulator(machine())
    sim.load_assembly(isa.assemble(machine(), "NEG -1\nBR 0"))
    assert sim.disassemble(0, 2) == [(0, "NEG -1", 1), (1, "BR 0x0", 1)]


def test_multicell_relative_uses_full_instruction_length():
    m = machine()
    m._instructions[1]["pattern"] = "0010 rrrr 0000 0000"
    image = isa.assemble(m, "BR end\n.cell 7\nend: END")
    assert image.cells == {0: 0x21, 1: 0, 2: 7, 3: 0xF0}
    sim = isa.Simulator(m)
    sim.load_assembly(image)
    assert (sim.step().pc, sim.cycle) == (3, 2)


def test_program_replacement_and_stale_source_after_poke():
    sim = isa.Simulator(machine())
    sim.load_cells("prog", {0: 0x11, 3: 0xF0})
    sim.load_assembly(isa.assemble(machine(), "ADD 2\nEND"))
    assert sim.read("prog[3]") == 0
    sim.poke("prog[0]", 0xF0)
    with pytest.raises(isa.DebugError, match="stale"):
        sim.source_context()
    sim.reset()
    assert sim.source_context(context=0) == [(1, "ADD 2", True)]


def test_source_on_second_cell_and_windows_location():
    sim = isa.Simulator(machine())
    image = isa.assemble(machine(), "MIX 1, 2, 3\nEND", filename="C:\\work\\main.asm")
    sim.load_assembly(image)
    assert sim.source_line(1) == ("C:\\work\\main.asm", 1)
    assert sim.source_context(1, context=0) == [(1, "MIX 1, 2, 3", True)]
    sim.add_breakpoint("C:\\work\\main.asm:2")
    assert sim.run(max_steps=5).stop_reason == "breakpoint"
    assert sim.state.pc == 2


@pytest.mark.parametrize("signed,relative", [(1, None), (False, "unknown"), (True, 7)])
def test_invalid_operand_declarations(signed, relative):
    with pytest.raises(isa.MCUDefinitionError):
        isa.Operand(signed=signed, relative=relative)


def test_ambiguous_instruction_syntax_does_not_choose_by_range():
    m = machine()
    m._instructions[4]["syntax"] = "ADD {i}"
    with pytest.raises(isa.AssemblyError, match="found 2"):
        isa.assemble(m, "ADD 2")


def test_constant_diamond_dependencies():
    source = "A = 1\n" + "\n".join(
        f"N{i} = {('A' if i == 0 else 'N' + str(i - 1))} + {('A' if i == 0 else 'N' + str(i - 1))}"
        for i in range(40)
    )
    source += "\n.cell N39 & 7\nEND"
    assert isa.assemble(machine(), source).cells == {0: 0, 1: 0xF0}


@pytest.mark.parametrize(
    "field", ["format", "source_sha256", "cell_bits", "symbols", "lines", "filename"]
)
def test_every_bundle_component_is_checked(tmp_path, field):
    path = tmp_path / "changed.isa.json"
    isa.assemble(machine(), "END").save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data[field] = None
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(isa.LoadError):
        isa.Assembly.load(machine(), path)


@pytest.mark.parametrize("context", [-1, True, "2"])
def test_context_validation(context):
    sim = isa.Simulator(machine())
    sim.load_assembly(isa.assemble(machine(), "END"))
    with pytest.raises(isa.DebugError):
        sim.source_context(context=context)


def test_independent_split_field_encoding_samples():
    m = machine()
    for high in range(16):
        for low in (0, 3, 5, 12, 15):
            other = (high ^ low) & 15
            image = isa.assemble(m, f"MIX {high}, {low}, {other}")
            assert image.cells == {
                0: 0x50 | (high >> 2) << 2 | (low >> 2),
                1: other << 4 | (high & 3) << 2 | (low & 3),
            }


@pytest.fixture
def cli_source(tmp_path):
    descriptor = tmp_path / "model.py"
    descriptor.write_text(
        "from rpkbin import isasim as isa\n"
        + inspect.getsource(machine)
        + "\nmcu=machine()\n",
        encoding="utf-8",
    )
    source = tmp_path / "program.asm"
    source.write_text("ADD 2\nEND", encoding="utf-8")
    return descriptor, source


def test_cli_protects_input_and_colliding_outputs(cli_source, tmp_path, capsys):
    descriptor, source = cli_source
    for protected in (descriptor, source):
        original = protected.read_bytes()
        assert (
            main(["assemble", str(descriptor), str(source), "-o", str(protected)]) == 2
        )
        assert protected.read_bytes() == original
        assert "paths must differ" in capsys.readouterr().err
    output = tmp_path / "image.json"
    assert (
        main(
            [
                "assemble",
                str(descriptor),
                str(source),
                "-o",
                str(output),
                "--listing",
                str(output),
            ]
        )
        == 2
    )
    assert not output.exists()


def test_cli_default_output_and_repl_source(cli_source, monkeypatch, capsys):
    descriptor, source = cli_source
    assert main(["assemble", str(descriptor), str(source)]) == 0
    assert source.with_suffix(".isa.json").exists()
    monkeypatch.setattr("sys.stdin", StringIO("break :2\nrun\nsource\nquit\n"))
    assert main(["repl", str(descriptor), str(source)]) == 0
    text = capsys.readouterr().out
    assert "Stopped: breakpoint" in text and "> 2: END" in text


@pytest.mark.parametrize(
    "option,value",
    [("--symbols", "x.json"), ("--space", "prog"), ("--order", "big"), ("--base", "0")],
)
def test_cli_rejects_ignored_assembly_options(cli_source, option, value):
    descriptor, source = cli_source
    with pytest.raises(SystemExit) as raised:
        main(["run", str(descriptor), str(source), "--max-steps", "4", option, value])
    assert raised.value.code == 2


def test_bundle_relocation_uses_captured_source(cli_source, tmp_path):
    descriptor, source = cli_source
    m = isa.load_mcu(descriptor)
    image = isa.assemble(m, source.read_text(encoding="utf-8"), filename=str(source))
    bundle = tmp_path / "saved.isa.json"
    image.save(bundle)
    source.write_text("END", encoding="utf-8")
    sim = isa.Simulator(isa.load_mcu(descriptor))
    sim.load_assembly(isa.Assembly.load(isa.load_mcu(descriptor), bundle))
    assert sim.source_context(context=0)[0][1] == "ADD 2"
    descriptor.write_text(
        descriptor.read_text(encoding="utf-8") + "\n# changed model\n", encoding="utf-8"
    )
    with pytest.raises(isa.LoadError, match="mismatch"):
        isa.Assembly.load(isa.load_mcu(descriptor), bundle)


def test_bundle_invalid_json_and_failed_load_preserves_image(tmp_path):
    sim = isa.Simulator(machine())
    sim.load_assembly(isa.assemble(machine(), "ADD 2\nEND"))
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    sim.snapshot().save(before)
    corrupted = isa.assemble(machine(), "END")
    corrupted._cells[0] = 3
    with pytest.raises(isa.LoadError, match="mismatch"):
        sim.load_assembly(corrupted)
    sim.snapshot().save(after)
    assert before.read_bytes() == after.read_bytes()
    invalid = tmp_path / "invalid.isa.json"
    invalid.write_text("[" * 1500 + "]" * 1500, encoding="utf-8")
    with pytest.raises(isa.LoadError):
        isa.Assembly.load(machine(), invalid)


def test_repl_quoted_source_path_with_spaces_and_condition():
    sim = isa.Simulator(machine())
    sim.load_assembly(
        isa.assemble(machine(), "ADD 3\nEND", filename="C:\\work area\\main.asm")
    )
    out = StringIO()
    run_repl(
        sim,
        StringIO('break "C:\\work area\\main.asm:2" if r.value == 3\nrun\nquit\n'),
        out,
    )
    assert "Stopped: breakpoint" in out.getvalue()
    assert sim.state.pc == 1
