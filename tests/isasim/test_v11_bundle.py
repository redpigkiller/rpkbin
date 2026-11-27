"""Consistency boundaries for self-contained assembly images."""

import hashlib
import json

import pytest

from rpkbin import isasim as isa

from .test_assembler import machine


def _assemble_bundle(path, source):
    isa.assemble(machine(), source, filename="main.asm", endian="little").save(path)
    return json.loads(path.read_bytes())


def _write_payload(path, payload):
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


@pytest.mark.parametrize("change", ["source", "sources_sha256", "lines"])
def test_bundle_rejects_inconsistent_source_metadata(tmp_path, change):
    path = tmp_path / "inconsistent.isa.json"
    payload = _assemble_bundle(path, "; original\nEND")
    if change == "source":
        payload["source"] = payload["sources"]["main.asm"] = "; edited\nEND"
    elif change == "sources_sha256":
        payload["sources_sha256"]["main.asm"] = "0" * 64
    else:
        payload["lines"][0]["line"] = 1
    _write_payload(path, payload)
    with pytest.raises(isa.LoadError):
        isa.Assembly.load(machine(), path)


def test_bundle_accepts_coordinated_source_changes_matching_fresh_assembly(tmp_path):
    path = tmp_path / "edited.isa.json"
    fresh = tmp_path / "fresh.isa.json"
    payload = _assemble_bundle(path, "; original\nEND")
    edited = "; edited\nEND"
    payload["source"] = payload["sources"]["main.asm"] = edited
    digest = hashlib.sha256(edited.encode("utf-8")).hexdigest()
    payload["source_sha256"] = payload["sources_sha256"]["main.asm"] = digest
    _write_payload(path, payload)
    _assemble_bundle(fresh, edited)
    assert path.read_bytes() == fresh.read_bytes()
    assert isa.Assembly.load(machine(), path).cells == {0: 240}


def test_bundle_accepts_option_changes_with_identical_assembly_products(tmp_path):
    path = tmp_path / "options.isa.json"
    payload = _assemble_bundle(path, "END")
    payload["options"]["endian"] = "big"
    _write_payload(path, payload)
    assert isa.Assembly.load(machine(), path).cells == {0: 240}
