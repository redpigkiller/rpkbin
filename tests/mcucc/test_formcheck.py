from dataclasses import replace
import importlib.util
from pathlib import Path

import pytest

from rpkbin.mcucc.formcheck import check_forms


def toy(name, width):
    spec = importlib.util.spec_from_file_location("form_toy_" + name, Path(__file__).parent / "toys" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.make_verified(width) if name == "t2" else module.make_verified()


@pytest.mark.parametrize("name,width", [("t0", 8), ("t1", 8), ("t2", 8), ("t2", 16)])
def test_all_forms(name, width):
    target, sim, stubs = toy(name, width)
    assert stubs
    assert check_forms(target, sim, (71,)) == []


@pytest.mark.parametrize("broken", ("bytes", "flags_write", "encode"))
def test_inconsistent_form_is_rejected(broken):
    target, sim, _ = toy("t1", 8)
    index = next(i for i, f in enumerate(target.forms) if f.id == "add_rr")
    form = target.forms[index]
    if broken == "bytes":
        form = replace(form, bytes=2)
    elif broken == "flags_write":
        form = replace(form, flags_write={"Z": "undef", "C": "borrow"})
    else:
        form = replace(form, encode=lambda values: (0, 0))
    target = replace(target, forms=(*target.forms[:index], form, *target.forms[index + 1:]))
    failures = check_forms(target, sim, (71,))
    assert any(f.form_id == form.id for f in failures)


@pytest.mark.parametrize("name,width", [("t0", 8), ("t1", 8), ("t2", 8), ("t2", 16)])
def test_extern_stubs_and_stack_balance(name, width):
    target, binding, stubs = toy(name, width)
    forms = {f.id: f for f in target.forms}
    for symbol, stub in stubs.items():
        sim = binding.make_sim()
        entry = forms["call"].encode({"t": 16})
        sim.load_cells(binding.program_space, {i: c for i, c in enumerate((*entry, *binding.halt_cells))})
        sim.load_cells(binding.program_space, {16 + i: c for i, c in enumerate(stub)})
        native = binding.names or {}
        wide = symbol == "h16"
        arg = "x1" if wide else "w" if name == "t0" else "r0"
        value = 0x0304 if wide else 3
        sim.poke(native.get(arg, arg), value)
        before = sim.read("sp")
        result = sim.run(max_steps=100)
        assert result.stop_reason == "halted"
        assert sim.read(native.get(arg, arg)) == (0x0405 if wide else 0x59)
        assert sim.read("sp") == before
        if symbol in ("helper_a", "helper_b"):
            assert sim.read("r2") == sim.read("r3") == 0xa5
        if symbol == "helper_b":
            assert sim.read("r1") == 0xa5
        if name == "t2":
            assert sim.read("r4") == sim.read("r5") == 0xa5
