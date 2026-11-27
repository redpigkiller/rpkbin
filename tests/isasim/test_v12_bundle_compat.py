"""Byte-level compatibility with bundles produced before v1.2."""

from pathlib import Path

import pytest

from rpkbin import isasim as isa

from .test_assembler import machine

_FIXTURES = Path(__file__).with_name("fixtures")


@pytest.mark.parametrize(
    "version,source,cells",
    [
        (
            1,
            "COUNT = 2\nstart: ADD COUNT\nMIX 10, 5, 3\n.db 7, 8\nEND\n",
            {0: 0x12, 1: 0x59, 2: 0x39, 3: 7, 4: 8, 5: 0xF0},
        ),
        (
            2,
            'COUNT EQU 2\n.org 8\n.include "part.inc"\n.org 0\n'
            'start: ADD COUNT\n.include "part.inc"\nEND\n',
            {
                0: 0x12,
                1: 0x59,
                2: 0x39,
                3: 7,
                4: 8,
                5: 0xF0,
                8: 0x59,
                9: 0x39,
                10: 7,
                11: 8,
            },
        ),
    ],
)
def test_v11_bundle_bytes_and_load(version, source, cells, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    frozen = _FIXTURES / f"v11-assembly-{version}.json"
    include = tmp_path / "part.inc"
    if version == 2:
        include.write_bytes(b"MIX 10, 5, 3\n.db 7, 8\n")
    actual = tmp_path / "fresh.json"
    isa.assemble(machine(), source, filename="main.asm").save(actual)
    assert actual.read_bytes() == frozen.read_bytes()

    if version == 2:
        include.unlink()
    loaded = isa.Assembly.load(machine(), frozen)
    assert loaded.cells == cells
    assert loaded.symbols == {"start": 0}
    assert list(loaded.sources) == (
        ["main.asm"] if version == 1 else ["main.asm", "part.inc"]
    )
    loaded.save(actual)
    assert actual.read_bytes() == frozen.read_bytes()
