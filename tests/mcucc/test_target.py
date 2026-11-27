from dataclasses import replace
import importlib.util

import pytest

from rpkbin.mcucc.errors import TargetError
from rpkbin.mcucc.target import (
    Abi, Alias, Cls, Flag, Form, I, M, MemLoc, P, R, Reg, Space, T,
    Target, load_target, load_target_file, part, units,
)


def basic():
    return Target(
        "sample", 8, (Space("code", 12, "program"), Space("data", 8, "data")),
        (Reg("a", 8), Reg("b", 8)), (Alias("pair", ("b", "a")),),
        (Cls("byte", ("a", "b")), Cls("wide", ("pair",))), (Flag("z"),),
        (Form("copy", "mov", 8, (R("d", "byte"), R("s", "byte")),
              {"dst": "d", "a": "s"}, 1, "copy {d},{s}"),
         Form("jump", "jmp", 0, (T("t", rel_bits=8),), {}, 2, "jump {t}"),
         Form("return", "ret", 0, (), {}, 1, "return")),
        Abi({8: ("a", "b"), 16: ("pair",)}, {8: "a", 16: "pair"}, ()),
        "none", ";", ".L{fn}_{n}", "{name}:",
    )


def test_operand_class_can_be_supplied_as_keyword_or_replaced():
    assert R(name='d',cls='byte')==R('d','byte')
    assert P(name='p',cls='byte',space='data')==P('p','byte','data')
    assert replace(R('d','byte'),cls='other')==R('d','other')


def test_locations():
    target = basic()
    assert units(target, "pair") == frozenset(("a", "b"))
    assert part(target, "pair", "hi") == "b"
    assert part(target, "pair", "lo") == "a"
    assert units(target, MemLoc("data", 4)) == frozenset((("data", 4),))
    from rpkbin.mcucc.cli import _availability_matrix
    assert _availability_matrix(target) == {("mov", 8): (1, 1), ("jmp", 0): (1, 2), ("ret", 0): (1, 1)}


@pytest.mark.parametrize("case", range(1, 11))
def test_load_validation_each_rule(case):
    target = basic()
    with pytest.raises(TargetError, match=r".+:.*"):
        if case == 1:
            replace(target, flags=(Flag("a"),))
        elif case == 2:
            replace(target, cell_bits=16)
        elif case == 3:
            replace(target, classes=(Cls("byte", ()),))
        elif case == 4:
            replace(target, forms=(replace(target.forms[0], asm="copy"), *target.forms[1:]))
        elif case == 5:
            replace(target, forms=(replace(target.forms[0], flags_read={"z": 1}), *target.forms[1:]))
        elif case == 6:
            replace(target, forms=(replace(target.forms[0], clobbers=("missing",)), *target.forms[1:]))
        elif case == 7:
            replace(target, abi=Abi({8: ("missing",)}, {8: "a"}, ()))
        elif case == 8:
            replace(target, forms=(*target.forms, Form("push", "push", 16, (R("s", "wide"),), {"a": "s"}, 1, "push {s}")))
        elif case == 9:
            replace(target, long_branch="invert_over_jump")
        else:
            replace(target, forms=target.forms[:2])


def test_loader_roundtrip(tmp_path):
    path = tmp_path / "device.py"
    path.write_text("from test_target import basic\nTARGET = basic()\n", encoding="utf-8")
    assert load_target(path) == basic()
    loaded, sim, stubs = load_target_file(path)
    assert loaded == basic() and sim is None and stubs is None


@pytest.mark.parametrize("name,bits", [("t0", 8), ("t1", 8), ("t2", 8), ("t2", 16)])
def test_toy_target(name, bits):
    from pathlib import Path
    path = Path(__file__).parent / "toys" / (name + ".py")
    spec = importlib.util.spec_from_file_location("toy_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = module.make_target(bits) if name == "t2" else module.make_target()
    assert target.cell_bits == 8
    assert target.spaces[1].addr_bits == bits


def test_variant_declarations_differ_only_where_allowed():
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("toy_variant", Path(__file__).parent / "toys" / "t2.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    narrow, wide = module.make_target(8), module.make_target(16)
    wide = replace(wide, spaces=narrow.spaces,
                   classes=tuple(c if c.name != "ptr" else narrow.classes[-1] for c in wide.classes),
                   forms=tuple(replace(f, bytes=2) if f.id in ("ld", "st") else f for f in wide.forms))
    assert narrow == wide


@pytest.mark.parametrize("change", ["cycle", "duplicate_units", "wide_immediate", "memory_bits", "pointer_width"])
def test_nested_target_constraints(change):
    target = basic()
    with pytest.raises(TargetError):
        if change == "cycle":
            replace(target, aliases=(Alias("pair", ("pair", "a")),))
        elif change == "duplicate_units":
            replace(target, aliases=(Alias("pair", ("a", "a")),))
        elif change == "wide_immediate":
            replace(target, forms=(*target.forms, Form("value", "const", 8, (R("d", "byte"), I("k", 16)), {"dst": "d", "a": "k"}, 3, "value {d},{k}")))
        elif change == "memory_bits":
            replace(target, forms=(*target.forms, Form("load", "ld", 8, (R("d", "byte"), M("m", "data", 16)), {"dst": "d", "a": "m"}, 3, "load {d},{m}")))
        else:
            replace(target, forms=(*target.forms, Form("load", "ld_ind", 8, (R("d", "byte"), P("p", "wide", "data")), {"dst": "d", "a": "p"}, 1, "load {d},{p}")))
