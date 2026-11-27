"""Target declaration diagnostics at load, compile, assemble and verify boundaries."""

from dataclasses import replace
from pathlib import Path

import pytest

from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.assemble import assemble
from rpkbin.mcucc.diff import run_differential
from rpkbin.mcucc.errors import TargetError
from rpkbin.mcucc.formcheck import check_forms
from rpkbin.mcucc.target import I, M, P, R, load_target_file
from test_formcheck import toy


EXAMPLES = Path(__file__).resolve().parents[2] / "rpkbin/mcucc/examples"
CONFIG = CompileConfig({"data": (96, 111)}, False)


@pytest.mark.parametrize(("op", "kind", "wrong_kind"), (
    ("ld", M, P), ("st", M, P), ("ld_ind", P, M), ("st_ind", P, M),
))
@pytest.mark.parametrize("replacement", ("register", "immediate", "other-address"))
def test_address_kind_is_checked_in_target(op, kind, wrong_kind, replacement):
    target, _, _ = toy("t2", 8)
    form = next(f for f in target.forms if f.op == op)
    address = next(o for o in form.operands if o.name == form.bind["a"])
    assert isinstance(address, kind)
    choices = {"register": R(address.name, "b"), "immediate": I(address.name, 8),
               "other-address": M(address.name, "sram") if wrong_kind is M else
               P(address.name, "ptr", "sram")}
    invalid = replace(form, operands=tuple(choices[replacement] if o is address else o
                                           for o in form.operands))
    with pytest.raises(TargetError, match=rf"{form.id}.*expected {kind.__name__} address operand"):
        replace(target, forms=tuple(invalid if f is form else f for f in target.forms))


def _wrong_length(target, op):
    return replace(target, forms=tuple(replace(f, encode=lambda values: ()) if f.op == op else f
                                       for f in target.forms))


@pytest.mark.parametrize(("path", "op", "expected"), (
    ("compile", "ret", 1), ("assemble", "ret", 1),
    ("verify-function", "ret", 1), ("verify-driver", "call", 3),
))
def test_disagreeing_encoder_length_has_one_diagnostic(path, op, expected):
    target, sim, _ = load_target_file(EXAMPLES / "target.py.txt")
    source = (EXAMPLES / "program.py.txt").read_text(encoding="utf-8")
    bad = _wrong_length(target, op)
    if path == "assemble":
        result = compile_source(source, target, CONFIG)
        fn = result.functions["add2"]
        mutated = next(form for form in bad.forms if form.op == op)
        fn = replace(fn, mir=tuple(replace(m, form=mutated) if m.op == op else m for m in fn.mir))
        result = replace(result, functions={"add2": fn})
    with pytest.raises(TargetError) as failure:
        if path == "compile":
            compile_source(source, bad, CONFIG)
        elif path == "assemble":
            assemble(result, bad, 0)
        else:
            run_differential(source, bad, sim, CONFIG, stubs={}, seeds=(0,), inputs_per_function=1,
                             window=256)
    assert str(failure.value) == (f"form '{op}': encode length/cells disagree with form "
                                  f"(expected {expected}, got 0)")


def test_form_checker_returns_failure_for_wrong_length():
    target, sim, _ = load_target_file(EXAMPLES / "target.py.txt")
    failures = check_forms(_wrong_length(target, "ret"), sim, [0])
    assert any(f.form_id == "ret" and f.check == "C10"
               and "encoded cells (); expected length 1 and in-range cells" in f.detail
               for f in failures)
