from types import SimpleNamespace
import pytest

from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.assemble import assemble
from rpkbin.mcucc.expand import MInstr
from rpkbin.mcucc.layout import _relax
from test_compile import source_for, execute
from test_formcheck import toy


@pytest.mark.parametrize("name,expected", (
    ("maxu", "maxu:\n    cmp r0, r1\n    jnc .Lmaxu_1\n    mov r0, r1\n.Lmaxu_1:\n    ret\n"),
))
def test_layout_golden(name, expected):
    result = compile_source(source_for(name), toy("t1",8)[0], CompileConfig({"sram":(0x60,0x6f)},False))
    assert result.asm == expected
    assert result.functions[name].bytes == 5


@pytest.mark.parametrize("helper,name,clobbers,expected,length", (
    ("helper_a", "f", '("r2","r3")', "f:\n    call helper_a\n    add r0, r1\n    ret\n", 5),
    ("helper_b", "fb", '("r1","r2","r3")', "fb:\n    st [0x60], r1\n    call helper_b\n    ld r1, [0x60]\n    add r0, r1\n    ret\n", 9),
))
def test_call_goldens(helper, name, clobbers, expected, length):
    source = f'''from rpkbin.mcucc.lang import u8, extern
@extern(args=("r0",),ret="r0",clobbers={clobbers})
def {helper}(x:u8)->u8:
    return x ^ 0x5A
def {name}(a:u8,b:u8)->u8:
    return {helper}(a)+b
'''
    result = compile_source(source,toy("t1",8)[0],CompileConfig({"sram":(0x60,0x6f)},False))
    assert result.asm == expected
    assert result.functions[name].bytes == length


@pytest.mark.parametrize("target_name,name,limit", (
    ("t1","abs_diff",8), ("t1","clamp",5), ("t1","count_down",13),
    ("t2","hi_of",2), ("t2","widen",4),
))
def test_corpus_size_caps(target_name, name, limit):
    result = compile_source(source_for(name),toy(target_name,8)[0],CompileConfig({"sram":(0x60,0x6f)},False))
    assert result.functions[name].bytes <= limit
    assert not result.functions[name].findings


@pytest.mark.parametrize("target_name", ("t0","t1"))
def test_invert_over_long_jump(target_name):
    target = toy(target_name,8)[0]
    forms = {f.id:f for f in target.forms}
    branch = forms["jz"]
    abi = {"args":(),"ret":None,"preserves":()}
    def instruction(form, values):
        return MInstr(form,values,_target=target,_abi=abi)
    register = target.regs[0].name
    padding = [instruction(forms["ldi"],{"d":register,"k":i}) for i in range(80)]
    original = (MInstr(None,{},_label="long",_target=target), instruction(branch,{"t":"destination"}),
        *padding, instruction(forms["ret"],{}), MInstr(None,{},_label="destination",_target=target), instruction(forms["ret"],{}))
    relaxed = _relax(original,target,"long")
    assert any(m.form and m.form.id=="jmpf" for m in relaxed)
    assert next(m.form.id for m in relaxed if m.op=="br")=="jnz"
    function = SimpleNamespace(mir=relaxed,bytes=sum(m.bytes for m in relaxed))
    result = SimpleNamespace(functions={"long":function})
    program = assemble(result,target,16)
    assert program.sizes["long"] == function.bytes
    binding=toy(target_name,8)[1]
    for flag_value,expected in ((0,79),(1,23)):
        sim=binding.make_sim()
        sim.load_cells(binding.program_space,program.cells)
        call=forms["call"]
        driver=(*call.encode({"t":program.symbols["long"]}),*binding.halt_cells)
        sim.load_cells(binding.program_space,dict(enumerate(driver)))
        native=binding.names or {}
        sim.poke(native.get(register,register),23)
        sim.poke(native.get("Z","Z"),flag_value)
        assert sim.run(max_steps=200000).stop_reason=="halted"
        assert sim.read(native.get(register,register))==expected


@pytest.mark.parametrize("target_name", ("t0","t1"))
def test_large_loop_executes_after_relaxation(target_name):
    body = "\n".join("        mem.sram[0x30] = a" for _ in range(80))
    source = "from rpkbin.mcucc.lang import u8,mem\ndef large(a:u8)->u8:\n    while a != 0:\n"+body+"\n        a = a - 1\n    return a\n"
    actual, result = execute(target_name,8,source,"large",(2,))
    assert actual == 0
    assert any(m.form and m.form.id=="jmpf" for m in result.functions["large"].mir)


def test_greedy_layout_with_more_than_eight_blocks():
    source="from rpkbin.mcucc.lang import u8\ndef chain(a:u8)->u8:\n"
    source+="".join(f"    if a == {i}:\n        return {i+1}\n" for i in range(9))
    source+="    return 0\n"
    actual,result=execute("t1",8,source,"chain",(7,))
    assert actual==8
    assert len(result.functions["chain"]._ir.blocks)>8
    assert not result.functions["chain"].findings


def test_zero_compare_elimination_still_passes_final_verifier():
    source="from rpkbin.mcucc.lang import u8\ndef f(a:u8,b:u8)->u8:\n    x=a+b\n    if x:\n        return x\n    return a\n"
    actual,result=execute("t1",8,source,"f",(7,9))
    assert actual==16
    assert not any(m.op=="cmp" for m in result.functions["f"].mir)


@pytest.mark.parametrize("target_name", ("t0","t1"))
def test_compiled_long_conditional_executes_both_paths(target_name):
    left="\n".join("        mem.sram[0x30]=a" for _ in range(80))
    right="\n".join("        mem.sram[0x31]=b" for _ in range(80))
    source="from rpkbin.mcucc.lang import u8,mem\ndef both(a:u8,b:u8)->u8:\n    if a<b:\n"+left+"\n        x=a+1\n    else:\n"+right+"\n        x=b+1\n    return x+2\n"
    for args,expected in (((1,5),4),((9,5),8)):
        actual,result=execute(target_name,8,source,"both",args)
        assert actual==expected
        assert any(m.form and m.form.id=="jmpf" for m in result.functions["both"].mir)
