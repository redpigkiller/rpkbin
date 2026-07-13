import sys
from .hir import (
    HFunction, HParam, HAssign, HIf, HFor, HWhile, HBreak, HReturn, HInlineAsm,
    HCall, u8, hconst, simple_function, HVar, UInt, HBinOp, HCmp
)
from .pipeline import run_codegen_from_hir
from .patterns import load_patterns_from_dicts
from .toy_target import ToyTarget
from .lir import format_function

# Define the mul2 pattern locally
BUILTIN_PATTERNS_DICT = [
    {
        "name": "mul2_to_shl1",
        "match": {"op": "mul", "args": [{"capture": "x"}, {"const": 2}]},
        "replace": {"op": "shl", "args": [{"ref": "x"}, {"const": 1}]},
    }
]


def build_add():
    a = u8("a")
    b = u8("b")
    result = u8("result")
    return simple_function(
        "add_demo",
        [HParam("a", UInt(8)), HParam("b", UInt(8))],
        [
            HAssign(result, HBinOp("add", a, b, UInt(8))),
            HReturn((result,))
        ],
        UInt(8)
    )

def build_if_else():
    x = u8("x")
    res = u8("res")
    return simple_function(
        "if_else_demo",
        [HParam("x", UInt(8))],
        [
            HIf(
                HCmp("eq", x, hconst(0)),
                (HAssign(res, hconst(1)),),
                (),
                (HAssign(res, hconst(2)),)
            ),
            HReturn((res,))
        ],
        UInt(8)
    )

def build_pattern_mul2():
    x = u8("x")
    res = u8("res")
    return simple_function(
        "pattern_mul2_demo",
        [HParam("x", UInt(8))],
        [
            HAssign(res, HBinOp("mul", x, hconst(2), UInt(8))),
            HReturn((res,))
        ],
        UInt(8)
    )

def build_for_break():
    i = u8("i")
    res = u8("res")
    return simple_function(
        "for_break_demo",
        [],
        [
            HAssign(res, hconst(0)),
            HFor(i, hconst(0), hconst(5), (
                HIf(HCmp("eq", res, hconst(2)), (HBreak(),), (), ()),
                HAssign(res, HBinOp("add", res, hconst(1), UInt(8)))
            )),
            HReturn((res,))
        ],
        UInt(8)
    )

def build_inline_asm():
    return simple_function(
        "inline_asm_demo",
        [],
        [
            HInlineAsm("nop"),
            HReturn((hconst(0),))
        ],
        UInt(8)
    )

def build_while_experimental():
    x = u8("x")
    return simple_function(
        "while_experimental_demo",
        [HParam("x", UInt(8))],
        [
            HWhile(HCmp("lt", x, hconst(10)), (
                HAssign(x, HBinOp("add", x, hconst(1), UInt(8))),
            )),
            HReturn((x,))
        ],
        UInt(8)
    )

CASES = {
    "add": build_add,
    "if_else": build_if_else,
    "pattern_mul2": build_pattern_mul2,
    "for_break": build_for_break,
    "inline_asm": build_inline_asm,
    "while_experimental": build_while_experimental,
}

def list_cases():
    return list(CASES.keys())

def build_case(name: str):
    if name not in CASES:
        raise ValueError(f"Unknown case {name}")
    return CASES[name]()

def run_case(name: str):
    print(f"=== Running case: {name} ===")
    hfunc = build_case(name)
    print("--- HIR summary ---")
    print(f"Function {hfunc.name}")
    
    try:
        patterns_list = load_patterns_from_dicts(BUILTIN_PATTERNS_DICT)
        res = run_codegen_from_hir(hfunc, ToyTarget(), patterns_list)
        
        lowered_text = format_function(res.input_lir)
        rewritten_text = format_function(res.rewritten_lir)
        asm_text = res.asm_text
        patterns = ", ".join(res.applied_patterns)
        
        print("--- lowered LIR text ---")
        print(lowered_text)
        print("--- rewritten LIR text ---")
        print(rewritten_text)
        print("--- pseudo ASM ---")
        print(asm_text)
        print("--- applied patterns ---")
        print(patterns)
        
        return {
            "hir_summary": hfunc.name,
            "lowered_lir": lowered_text,
            "rewritten_lir": rewritten_text,
            "pseudo_asm": asm_text,
            "applied_patterns": res.applied_patterns
        }
    except NotImplementedError as e:
        print("--- Error ---")
        print(f"NotImplementedError: {e}")
        return None
