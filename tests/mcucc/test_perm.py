"""Structural target variations must execute correctly or reject explicitly."""

from dataclasses import replace
import json
from pathlib import Path
import random

import pytest

from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.errors import AllocationError, UnsupportedError
from rpkbin.mcucc.frontend import _parse
from rpkbin.mcucc.lang import run_reference
from rpkbin.mcucc.target import Space
from test_compile import source_for
from test_formcheck import toy
from test_fuzz import run_case
from test_review import _shift_target


@pytest.mark.parametrize("name,width", (("t0", 8), ("t1", 8), ("t2", 8), ("t2", 16)))
@pytest.mark.parametrize("variant", (0, 17, 71, "loads-first", "control-clobbers", "aux", "shift3"))
def test_target_permutations_and_structure(name, width, variant):
    target, binding, _ = toy(name, width)
    if isinstance(variant, int):
        forms = list(target.forms)
        random.Random(variant).shuffle(forms)
        target = replace(target, forms=tuple(forms))
    elif variant == "loads-first":
        target = replace(target, forms=tuple(sorted(target.forms, key=lambda f: f.op not in ("ld", "ld_ind"))))
    elif variant == "control-clobbers":
        target = replace(target, forms=tuple(replace(f, clobbers=(target.regs[-1].name,))
                         if f.op in ("br", "jmp") else f for f in target.forms))
    else:
        from machine import verified
        target = replace(target, spaces=(*target.spaces, Space("aux", 8, "data"))) if variant == "aux" else _shift_target(target)
        target, binding, _ = verified(target, ())
    pool = (0x20, 0x2f) if name == "t0" else (0x60, 0x6f)
    config = CompileConfig({"sram": pool}, False)
    names = tuple(json.loads(Path(__file__).with_name("sizes.json").read_text())[f"{name}-{width}"])
    checked, rejected = [], []
    for function_name in names:
        source = source_for(function_name)
        try:
            result = compile_source(source, target, config)
        except (UnsupportedError, AllocationError) as exc:
            assert function_name in str(exc) and len(str(exc)) > len(function_name) + 10
            rejected.append(function_name)
            continue
        parameters = _parse(source, target, "permuted.py").functions[function_name].params
        for value in (0, 255, 17):
            args = tuple(128 if ty.space else value for _, ty in parameters)
            actual, after, memory = run_case(result, target, binding, {}, function_name, args, 73)
            expected, reference = run_reference(source, function_name, args, memory,
                addr_bits={s.name: s.addr_bits for s in target.spaces if s.kind == "data"})
            assert actual == expected, (name, width, variant, function_name, args, result.asm)
            for space, cells in reference.items():
                for address, cell in enumerate(cells):
                    if space != "sram" or not pool[0] <= address <= pool[1]:
                        assert after[space][address] == cell, (variant, function_name, space, address)
        checked.append(function_name)
    assert checked and len(checked) + len(rejected) == 22
    if variant == "aux":
        prefix = 'from rpkbin.mcucc.lang import u8, Ptr, mem\nA = Ptr("aux")\n'
        for body, args, annotation in (("return mem.aux[p]", "p:A", "->u8"),
                                      ("mem.aux[p] = v", "p:A,v:u8", ""),
                                      ("return mem.aux[3]", "", "->u8"),
                                      ("mem.aux[3] = v", "v:u8", "")):
            with pytest.raises(UnsupportedError, match="f.*(load|store|aux)"):
                compile_source(prefix + f"def f({args}){annotation}:\n    {body}\n", target, config)
