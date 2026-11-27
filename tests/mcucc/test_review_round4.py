"""Fourth review regressions at the verifier, language and driver boundaries."""

from dataclasses import replace
from pathlib import Path
import re

import pytest

from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.diff import run_differential
from rpkbin.mcucc.errors import InternalError, McuccError, SourceError
from rpkbin.mcucc.expand import MInstr
from rpkbin.mcucc.formcheck import check_forms
from rpkbin.mcucc.frontend import _parse
from rpkbin.mcucc.ir import ir_eval
from rpkbin.mcucc.lang import run_reference
from rpkbin.mcucc.lower import _lower
from rpkbin.mcucc.opt import _optimize
from rpkbin.mcucc.target import MemLoc, load_target_file
from rpkbin.mcucc.verify import _verify
from test_asm_cli import command
from test_formcheck import toy
from test_review import _execute
from test_review_round4_supplement import _replace_mir


POOL = CompileConfig({"sram": (96, 111)}, False)
CONFIG = CompileConfig({}, False)
HEADER = 'from rpkbin.mcucc.lang import u8, u16, Ptr, mem\n'
RETURN_SEVEN = HEADER + 'def f()->u8:\n    return 7\n'


@pytest.mark.parametrize("label", (False, True))
def test_cr21_return_move_after_ret_is_rejected(label):
    target, binding, _ = toy("t1", 8)
    result = compile_source(RETURN_SEVEN, target, POOL)
    function = result.functions["f"]
    entry, load, ret = function.mir
    assert load.op == "const" and ret.op == "ret" and load._origin == ret._origin
    separator = (MInstr(None, {}, _label="unreachable_return_move"),) if label else ()
    broken = _replace_mir(function, (entry, ret, *separator, load))
    assert _execute(result, target, binding, ())[0] == 7
    altered = replace(result, functions={"f": broken})
    assert _execute(altered, target, binding, ())[0] == 0
    with pytest.raises(InternalError, match="V7"):
        _verify(broken, target, POOL, {"f": function._ir.abi})


@pytest.mark.parametrize("origin", (None, (0, 0)))
def test_cr22_reachable_store_needs_an_authorized_origin(origin):
    target, binding, _ = toy("t1", 8)
    result = compile_source(RETURN_SEVEN, target, POOL)
    function = result.functions["f"]
    store = next(f for f in target.forms if f.op == "st")
    orphan = MInstr(store, {"m": MemLoc("sram", 48), "s": "r0"},
                    _origin=origin, _block=0, _target=target, _abi=function._ir.abi)
    broken = _replace_mir(function, (*function.mir[:-1], orphan, function.mir[-1]))
    altered = replace(result, functions={"f": broken})
    value, sim = _execute(altered, target, binding, ())
    assert value == 7 and sim.read("sram[48]") == 7
    with pytest.raises(InternalError, match="V6"):
        _verify(broken, target, POOL, {"f": function._ir.abi})


@pytest.mark.parametrize("fault", ("swap_low_bits", "address_48"))
def test_cr23_bad_load_address_encoding_is_detected(fault):
    target, binding, _ = toy("t1", 8)
    load = next(f for f in target.forms if f.op == "ld")
    def encode(values):
        address = values["m"]
        if fault == "swap_low_bits":
            address = (address & ~3) | ((address & 1) << 1) | ((address & 2) >> 1)
        elif address == 48:
            address = 0
        return load.encode({**values, "m": address})
    forms = tuple(replace(f, encode=encode) if f is load else f
                  for f in target.forms if f.op in ("ld", "jmp", "ret"))
    broken = replace(target, forms=forms, long_branch="none")
    failures = check_forms(broken, binding, (0, 7, 71))
    assert any(f.form_id == load.id and f.check in ("C2", "C5") for f in failures)


@pytest.mark.parametrize("form_id,key,signed", (("ld", "m", False), ("ldi", "k", False), ("jmp", "t", True)))
def test_cr23_encoder_sees_each_single_bit_and_complement(form_id, key, signed):
    target, binding, _ = toy("t1", 8)
    form = next(f for f in target.forms if f.id == form_id)
    samples = []
    def encode(values):
        samples.append(values[key])
        return form.encode(values)
    forms = tuple(replace(f, encode=encode) if f is form else f
                  for f in target.forms if f is form or f.id in ("jmp", "ret"))
    assert check_forms(replace(target, forms=forms, long_branch="none"), binding, (0,)) == []
    patterns = {0, 255, *(1 << i for i in range(8)), *(255 ^ (1 << i) for i in range(8))}
    if signed:
        patterns = {v if v < 128 else v - 256 for v in patterns} | {127}
    assert patterns <= set(samples)
    assert len(samples) >= len(patterns) + 4


@pytest.mark.parametrize("bits", (8, 16))
@pytest.mark.parametrize("expression,expected8,expected16", (
    ("(255+1)>>1", 128, 128), ("(~0)>>1", 255, 65535),
    ("(K*3)//2+(-7)%4", 129, 385),
))
def test_cr24_untyped_pointer_argument_is_folded_before_lowering(bits, expression, expected8, expected16):
    target, binding, _ = toy("t2", bits)
    expected = expected8 if bits == 8 else expected16
    source = HEADER + f'P = Ptr("sram")\nK = 256\ndef f()->P:\n    return P({expression})\n'
    module = _lower(_parse(source, target, "constant.py"))
    function = module["f"]
    assert run_reference(source, "f", (), {}, addr_bits={"sram": bits})[0] == expected
    assert ir_eval(function, (), {}, module)[0] == expected
    assert {i.op for b in function.blocks for i in b.instrs} <= {"entry", "const"}
    module["f"] = _optimize(function)
    assert ir_eval(module["f"], (), {}, module)[0] == expected
    assert _execute(compile_source(source, target, CONFIG), target, binding, ())[0] == expected


@pytest.mark.parametrize("condition", ("K", "1", "not K"))
@pytest.mark.parametrize("entry", ("compile", "reference"))
def test_untyped_integer_truth_is_rejected(condition, entry):
    target = toy("t1", 8)[0]
    source = HEADER + f'K = 256\ndef f()->u8:\n    if {condition}:\n        return 1\n    return 2\n'
    with pytest.raises(SourceError, match=r":4:.*condition"):
        if entry == "compile":
            compile_source(source, target, CONFIG)
        else:
            run_reference(source, "f", (), {}, addr_bits={"sram": 8})


def test_v9_rejects_an_underreported_clobber_list():
    target = toy("t1", 8)[0]
    source = HEADER + 'def f(a:u8)->u8:\n    return a+1\n'
    function = compile_source(source, target, POOL).functions["f"]
    assert function.clobbers == ("r0",)
    with pytest.raises(InternalError, match="V9.*clobbers"):
        _verify(replace(function, clobbers=()), target, POOL, {"f": function._ir.abi})


@pytest.mark.parametrize("variant,detail", (
    ("no_call", "call form"), ("no_encoder", "missing encode"), ("raising", "driver encoder marker"),
))
def test_cr25_verify_driver_errors_are_reported_without_traceback(tmp_path, variant, detail):
    readme = (Path(__file__).resolve().parents[2] / "docs/zh/mcucc/README.md").read_text(encoding="utf-8")
    examples = dict(re.findall(r'```python\n# ([\w.]+\.py)\n(.*?)```', readme, re.S))
    source, target_path = tmp_path / "program.py", tmp_path / "target.py"
    source.write_text(examples["program.py"], encoding="utf-8")
    mutations = {
        "no_call": 'TARGET = replace(TARGET, forms=tuple(f for f in TARGET.forms if f.op != "call"))\n',
        "no_encoder": 'TARGET = replace(TARGET, forms=tuple(replace(f, encode=None) if f.op == "call" else f for f in TARGET.forms))\n',
        "raising": 'def broken(values):\n    raise RuntimeError("driver encoder marker")\n'
                   'TARGET = replace(TARGET, forms=tuple(replace(f, encode=broken) if f.op == "call" else f for f in TARGET.forms))\n',
    }
    target_path.write_text(examples["small_target.py"] + '\nfrom dataclasses import replace\n' + mutations[variant], encoding="utf-8")
    result = command("verify", source, "--target", target_path, "--spill-pool", "data:96-111", "--window", 256)
    assert result.returncode == 2, result.stderr
    assert result.stderr.startswith("error: ") and detail in result.stderr and "Traceback" not in result.stderr
    target, binding, _ = load_target_file(target_path)
    with pytest.raises(McuccError, match=detail) as exc:
        run_differential(examples["program.py"], target, binding, CompileConfig({"data": (96, 111)}, False),
                         stubs={}, seeds=(0,), inputs_per_function=1, window=256)
    if variant == "raising":
        assert isinstance(exc.value.__cause__, RuntimeError)
        assert str(exc.value.__cause__) == "driver encoder marker"
