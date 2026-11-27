from dataclasses import replace
import pytest

from rpkbin.mcucc import CompileConfig,compile_source
from rpkbin.mcucc.expand import _Context
from rpkbin.mcucc.frames import _Builder,_active
from rpkbin.mcucc.frontend import _parse
from rpkbin.mcucc.lower import _lower
from rpkbin.mcucc.opt import _optimize
from rpkbin.mcucc.target import RegLoc,SlotLoc
from test_compile import source_for,execute
from test_formcheck import toy


H16='''from rpkbin.mcucc.lang import u8,u16,extern
@extern(args=("x1",),ret="x1",clobbers=("x0","x1","r4","r5"))
def h16(b:u16)->u16:
    return b+0x0101
def g(a:u16,b:u16)->u16:
    return a+h16(b)
'''


@pytest.mark.parametrize("width",(8,16))
def test_g13_push_group_golden(width):
    target,binding,stubs=toy("t2",width)
    result=compile_source(H16,target,CompileConfig({"sram":(0x60,0x6f)},False))
    assert result.asm=="g:\n    push x0\n    call h16\n    pop x0\n    addw x0, x1\n    ret\n"
    assert result.functions["g"].bytes==7
    from test_fuzz import run_case
    actual,_,_=run_case(result,target,binding,stubs,"g",(0x0102,0x0304),31)
    assert actual==0x0507


@pytest.mark.parametrize("width",(8,16))
def test_pointer_variant_goldens(width):
    target=toy("t2",width)[0]
    config=CompileConfig({"sram":(0x60,0x6f)},False)
    expected="load:\n    ld r0, ["+("r0" if width==8 else "x0")+"]\n    ret\n"
    assert compile_source(source_for("load"),target,config).asm==expected
    expected="getc:\n    ld r0, ["+("0x30" if width==8 else "0x0030")+"]\n    ret\n"
    result=compile_source(source_for("getc"),target,config)
    assert result.asm==expected and result.functions["getc"].bytes==(3 if width==8 else 4)


def test_push_group_deduplication_and_result_overlap_fallback():
    target=toy("t2",8)[0]
    source='''from rpkbin.mcucc.lang import u8,extern
@extern(args=("r0",),ret="r0",clobbers=("r1","r2","r3","r4","r5"))
def helper(x:u8)->u8:
    return x ^ 0x5A
def f(a:u8,b:u8)->u8:
    c=helper(a)
    return c+a+b
'''
    functions=_lower(_parse(source,target,"group.py"))
    function=functions["f"]
    _optimize(function)
    block,index,call=next((b,i,n) for b in function.blocks for i,n in enumerate(b.instrs) if n.op=="call")
    locations={v:RegLoc("r0") for v in _active(function)}
    locations[function.params[0]],locations[function.params[1]]=RegLoc("r2"),RegLoc("r3")
    ctx=_Context(target,{"sram":(0x60,0x6f)})
    interfaces={name:fn.abi for name,fn in functions.items()}
    sequence=_Builder(function,ctx,locations,interfaces)._call(call,block.id,index)
    assert [n.values["s"] for n in sequence if n.op=="push"]==["x1"]
    assert [n.values["d"] for n in sequence if n.op=="pop"]==["x1"]
    locations[function.params[1]],locations[call.dst]=RegLoc("r4"),RegLoc("r3")
    sequence=_Builder(function,ctx,locations,interfaces)._call(call,block.id,index)
    assert not any(n.op in ("push","pop") for n in sequence)
    assert any(isinstance(v,SlotLoc) and v.kind=="callsave" for n in sequence for v in n.values.values())
    # A cheaper legal pop must be considered before falling back to slots.
    forms=tuple(item for form in target.forms for item in ((replace(form,id="pop_unsafe",clobbers=("x1",)),form) if form.op=="pop" else (form,)))
    modified=replace(target,forms=forms)
    locations[function.params[0]],locations[function.params[1]],locations[call.dst]=RegLoc("r0"),RegLoc("r2"),RegLoc("r4")
    sequence=_Builder(function,_Context(modified,{"sram":(0x60,0x6f)}),locations,interfaces)._call(call,block.id,index)
    assert sum(n.op=="push" for n in sequence)==2
    assert all(n.form.id=="pop" for n in sequence if n.op=="pop" and n.values["d"]=="x0")


def test_preserves_input_alias_is_saved_before_entry_and_restored_after_result():
    source='''from rpkbin.mcucc.lang import u16,fn
@fn(args=("x1",),ret="x0",preserves=("x1",))
def f(a:u16)->u16:
    return a-1
'''
    actual,result=execute("t2",8,source,"f",(0x1234,))
    assert actual==0x1233
    mir=result.functions["f"].mir
    assert any(m.op=="push" and m._role=="preserve-save" for m in mir)
    assert any(m.op=="pop" and m._role=="preserve-restore" for m in mir)


def test_reference_memory_beyond_random_window_uses_binding_initial_state():
    from rpkbin.mcucc.diff import run_differential
    target,binding,stubs=toy("t2",16)
    binding=replace(binding,init=lambda sim:sim.poke("sram[256]",0x77))
    source="from rpkbin.mcucc.lang import u8,mem\ndef beyond()->u8:\n    return mem.sram[0x100]\n"
    report=run_differential(source,target,binding,CompileConfig({"sram":(0x60,0x6f)},False),
        stubs=stubs,seeds=(17,),inputs_per_function=31,window=256)
    assert not report.failures
    assert report.checked==31
