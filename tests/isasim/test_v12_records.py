"""Structured source records follow assembly expansion and cell mappings."""

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from rpkbin import isasim as isa

from .test_assembler import machine


@pytest.mark.parametrize("version", [1, 2])
def test_records_match_frozen_bundle_listing_and_mounted_source(
    version, tmp_path, monkeypatch
):
    bundle = Path(__file__).with_name("fixtures") / f"v11-assembly-{version}.json"
    payload = json.loads(bundle.read_bytes())
    main = payload["filename"]
    sources = payload.get("sources", {main: payload["source"]})
    expected = [
        (
            row["addr"],
            row.get("file", main),
            row["line"],
            tuple(row["cells"]),
            row["instruction"],
        )
        for row in payload["lines"]
    ]
    mcu = machine()
    monkeypatch.chdir(tmp_path)
    for key, text in sources.items():
        if key != main:
            Path(key).write_bytes(text.encode("utf-8"))
    direct = isa.assemble(mcu, sources[main], filename=main)
    for key in sources:
        if key != main:
            Path(key).unlink()
    loaded = isa.Assembly.load(mcu, bundle)

    assert direct.main_key == loaded.main_key == main
    assert (
        direct.records()
        == loaded.records()
        == tuple(isa.SourceRecord(*row) for row in expected)
    )
    assert direct.listing() == loaded.listing()
    sim = isa.Simulator(mcu)
    sim.load_assembly(loaded)
    for record, listing_row in zip(
        loaded.records(), loaded.listing().splitlines(), strict=True
    ):
        raw = sources[record.file].splitlines()[record.line - 1]
        encoded = " ".join(f"{cell:02x}" for cell in record.cells)
        assert (
            listing_row
            == f"{record.addr:04x}  {encoded}  {record.file}:{record.line}  {raw}"
        )
        for offset in range(len(record.cells)):
            assert sim.source_line(record.addr + offset) == (record.file, record.line)

        location = f"{record.file}:{record.line}"
        if record.is_instruction:
            sim.delete(sim.add_breakpoint(location))
        else:
            with pytest.raises(isa.TargetError, match="no instruction"):
                sim.add_breakpoint(location)


def test_records_skip_statements_without_output_and_are_immutable():
    image = isa.assemble(
        machine(),
        "COUNT EQU 7\n.if 0\nEND\n.endif\n.space 0\n.org 5\n"
        "entry:\nADD COUNT\n.space 2, 3\nEND\n",
    )
    assert image.main_key == "<string>"
    rows = image.records()
    assert rows == (
        isa.SourceRecord(5, "<string>", 8, (0x17,), True),
        isa.SourceRecord(6, "<string>", 9, (3, 3), False),
        isa.SourceRecord(8, "<string>", 10, (0xF0,), True),
    )
    with pytest.raises(FrozenInstanceError):
        rows[0].addr = 0
    with pytest.raises(AttributeError):
        image.main_key = "other.asm"
