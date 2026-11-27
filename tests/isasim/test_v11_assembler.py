"""Assembler directives and operand selection for the extended language."""

import pytest

from rpkbin import isasim as isa

from .test_assembler import machine
from .toys import toy_a


def test_org_still_resolves_an_already_defined_label():
    image = isa.assemble(machine(), "Start: .cell 1\n.org Start + 2\n.cell 2")
    assert image.cells == {0: 1, 2: 2}


@pytest.mark.parametrize(
    "source",
    [
        "Start: .cell 1\n.space Start + 2",
        "Size = Start + 2\nStart: .cell 1\n.space Size",
        ".space Tail + 1\nTail: .cell 1",
    ],
)
def test_space_rejects_direct_and_indirect_label_dependencies(source):
    with pytest.raises(isa.AssemblyError, match=r"space\.asm:\d+:.*label"):
        isa.assemble(machine(), source, filename="space.asm")


def test_equ_aliases_share_forward_label_and_constant_resolution():
    image = isa.assemble(
        machine(),
        "One eQu Two - 1\n.EqU Two, End + 1\nThree = Two\n"
        ".cell One, Two, Three\nEnd: END",
    )
    assert image.cells == {0: 3, 1: 4, 2: 4, 3: 240}
    assert image.constants == {"One": 3, "Two": 4, "Three": 4}


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("N EQU 1\n.equ N, 2\nEND", "duplicate"),
        ("N EQU M\n.equ M, N\nEND", "cyclic"),
        (".db -129", "range"),
        (".db 256", "range"),
        (".dw -32769", "range"),
        (".dw 65536", "range"),
        (".cell -1", "range"),
        (".space 0, 256\nEND", "range"),
        (".space 0", "no emitted cells"),
        (".space -1\nEND", "range"),
        (".space 64\nTail:", "label"),
    ],
)
def test_directives_reject_invalid_inputs(source, message):
    with pytest.raises(isa.AssemblyError, match=message):
        isa.assemble(machine(), source, endian="big")


def test_db_and_space_emit_cells_with_twos_complement_and_fill():
    image = isa.assemble(machine(), ".DB -128, 255\n.DS 2, 9\n.space 1\nTail: .space 0")
    assert image.cells == {0: 128, 1: 255, 2: 9, 3: 9, 4: 0}
    assert image.symbols == {"Tail": 5}


@pytest.mark.parametrize(
    ("endian", "cells"),
    [
        ("little", [0, 128, 255, 255, 52, 18]),
        ("big", [128, 0, 255, 255, 18, 52]),
    ],
)
def test_dw_byte_order(endian, cells):
    image = isa.assemble(machine(), ".DW -32768, 65535, 0x1234", endian=endian)
    assert list(image.cells.values()) == cells


def test_dw_uses_one_cell_on_wide_program_space():
    image = isa.assemble(toy_a.make_mcu(), ".dw -32768, 65535")
    assert image.cells == {0: 32768, 1: 65535}
    with pytest.raises(isa.AssemblyError, match=r"\.cell"):
        isa.assemble(toy_a.make_mcu(), ".db 1")
    with pytest.raises(isa.AssemblyError, match="endian"):
        isa.assemble(machine(), ".dw 1")


def test_casefold_lookups_preserve_spelling():
    image = isa.assemble(
        machine(),
        "Begin: END\nAmount EQU begin + 1\n.cell amount",
        case_sensitive_names=False,
    )
    assert image.symbols == {"Begin": 0}
    assert image.constants == {"Amount": 1}
    assert image.cells == {0: 240, 1: 1}
    sim = isa.Simulator(machine())
    sim.load_assembly(image)
    assert sim.disassemble("bEGIN", 1) == [(0, "END", 1)]


def test_casefold_detects_duplicates_and_default_is_case_sensitive():
    source = "Foo = 1\nfoo: .cell Foo"
    assert isa.assemble(machine(), source).symbols == {"foo": 0}
    with pytest.raises(isa.AssemblyError, match="duplicate"):
        isa.assemble(machine(), source, case_sensitive_names=False)


def _same_syntax_machine(short_operand, long_operand):
    mcu = machine()

    @mcu.instr("1000 iiii", syntax="TAKE {i}", cycles=1, operands={"i": short_operand})
    def short(st, i):
        st.reg["value"] = i

    @mcu.instr(
        "1001 0000 iiii iiii", syntax="TAKE {i}", cycles=1, operands={"i": long_operand}
    )
    def long(st, i):
        st.reg["value"] = i

    return mcu


def test_names_select_length_copy_mapping_and_render_signed_name():
    names = {"Minus": -1}
    mcu = _same_syntax_machine(
        isa.Operand(signed=True, names=names, range=(-1, 0)),
        isa.Operand(names={"Larger": 123}),
    )
    names["Minus"] = 4
    image = isa.assemble(mcu, "TAKE minus\nTAKE LARGER", case_sensitive_names=False)
    assert image.cells == {0: 143, 1: 144, 2: 123}
    sim = isa.Simulator(mcu)
    sim.load_assembly(image)
    assert sim.disassemble(0, 2) == [(0, "TAKE Minus", 1), (1, "TAKE Larger", 2)]


def test_names_reject_folded_collisions_and_unmapped_operands():
    mcu = _same_syntax_machine(
        isa.Operand(names={"One": 1, "one": 2}),
        isa.Operand(names={"Long": 1}),
    )
    assert isa.assemble(mcu, "TAKE one").cells == {0: 130}
    with pytest.raises(isa.AssemblyError, match="One|one"):
        isa.assemble(mcu, "TAKE One", case_sensitive_names=False)
    with pytest.raises(isa.AssemblyError, match="found 0"):
        isa.assemble(mcu, "TAKE 1")


def test_range_selects_length_and_layout_addresses():
    mcu = _same_syntax_machine(isa.Operand(range=(0, 3)), isa.Operand(range=(3, 100)))
    image = isa.assemble(mcu, "TAKE 2\nTAKE 99\nTail: END")
    assert image.cells == {0: 130, 1: 144, 2: 99, 3: 240}
    assert image.symbols == {"Tail": 3}
    with pytest.raises(isa.AssemblyError, match="found 2"):
        isa.assemble(mcu, "TAKE 3")
    with pytest.raises(isa.AssemblyError, match="found 0"):
        isa.assemble(mcu, "TAKE 101")
    with pytest.raises(isa.AssemblyError, match="label"):
        isa.assemble(mcu, "N = Tail\nTAKE N\nTail: END")
    with pytest.raises(isa.AssemblyError, match="division by zero"):
        isa.assemble(mcu, "TAKE 1 // 0")


@pytest.mark.parametrize(
    "options",
    [
        {"names": {}},
        {"names": {"One": 1, "Two": 1}},
        {"names": {"One": 1}, "relative": "pc"},
        {"range": (0, 1), "relative": "pc"},
        {"range": (2, 1)},
        {"range": (0, 16)},
        {"names": {"Large": 16}},
    ],
)
def test_operand_rejects_invalid_metadata(options):
    with pytest.raises(isa.MCUDefinitionError):
        mcu = _same_syntax_machine(isa.Operand(**options), isa.Operand())
        isa.Simulator(mcu)


def test_single_file_assembly_options_survive_save_load_and_install(tmp_path):
    image = isa.assemble(
        machine(),
        "Begin: .dw 0x1234\nAmount EQU begin + 1\n.cell amount",
        filename="main.asm",
        endian="little",
        case_sensitive_names=False,
    )
    path = tmp_path / "options.json"
    image.save(path)
    restored = isa.Assembly.load(machine(), path)
    assert restored.cells == {0: 52, 1: 18, 2: 1}
    assert restored.symbols == {"Begin": 0}
    assert restored.constants == {"Amount": 1}
    sim = isa.Simulator(machine())
    sim.load_assembly(restored)
    assert sim.source_context("BEGIN", context=0) == [(1, "Begin: .dw 0x1234", True)]


def test_legacy_bundle_is_loaded_and_saved_byte_for_byte(tmp_path):
    golden = b"""{
  "format": "isasim-assembly-1",
  "isa_hash": "05d59fea489d14d376f228dd4c7c1afda4e2821f95d069c5b21a9bd629e9bdec",
  "source": "END",
  "filename": "legacy.asm",
  "source_sha256": "b891f9e2a69626266100a5fa3161423408bdf539816e578cc6464a315b138668",
  "cell_bits": 8,
  "cells": [
    [
      0,
      240
    ]
  ],
  "symbols": {},
  "lines": [
    {
      "addr": 0,
      "cells": [
        240
      ],
      "line": 1,
      "instruction": true
    }
  ]
}
"""
    legacy = tmp_path / "legacy.json"
    new = tmp_path / "output.json"
    legacy.write_bytes(golden)
    image = isa.Assembly.load(machine(), legacy)
    assert image.cells == {0: 240}
    image.save(new)
    assert new.read_bytes() == golden
    isa.assemble(machine(), "END", filename="legacy.asm").save(new)
    assert new.read_bytes() == golden
