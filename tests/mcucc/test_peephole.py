from rpkbin.mcucc.expand import MInstr
from rpkbin.mcucc.peephole import _peephole
from rpkbin.mcucc.lint_q import _findings
from rpkbin.mcucc.target import MemLoc
from test_formcheck import toy


def fixture(name="t1"):
    target = toy(name,8)[0]
    forms = {f.id:f for f in target.forms}
    abi = {"args":(),"ret":"r0","preserves":()}
    def make(form, **values):
        return MInstr(forms[form],values,_target=target,_abi=abi,_block=0)
    return target, make


def test_individual_redundancies_and_q_share_conditions():
    target, m = fixture()
    end = m("ret")
    examples = (
        (m("mov",d="r0",s="r0"),end),
        (m("ldi",d="r3",k=7),end),
        (m("st",m=MemLoc("sram",0x30),s="r0"),m("ld",d="r0",m=MemLoc("sram",0x30)),end),
        (m("ldi",d="r0",k=7),m("ldi",d="r0",k=7),end),
        (end,m("ldi",d="r0",k=7)),
    )
    for mir in examples:
        assert _findings(mir,target,"f")
        result = _peephole(mir,target,"f")
        assert len(result) < len(mir)
        assert not _findings(result,target,"f")


def test_copy_forwarding_changes_only_read_operands():
    target, m = fixture()
    mir = (m("mov",d="r2",s="r1"),m("add_rr",d="r0",s="r2"),m("ret"))
    result = _peephole(mir,target,"f")
    assert result[0].op == "add" and result[0].values == {"d":"r0","s":"r1"}
    # A tied input cannot be replaced independently of its output.
    mir = (m("mov",d="r0",s="r1"),m("sub_rr",d="r0",s="r2"),m("add_rr",d="r0",s="r1"),m("ret"))
    result = _peephole(mir,target,"f")
    assert result[0].op == "mov"


def test_jump_to_return_and_fallthrough():
    target, m = fixture()
    label = MInstr(None,{},_label="end",_target=target,_block=1)
    result = _peephole((m("jmp",t="end"),label,m("ret")),target,"f")
    assert not any(i.op=="jmp" for i in result)


def test_zero_comparison_requires_matching_producer_and_flag():
    target, m = fixture()
    label = MInstr(None,{},_label="end",_target=target,_block=1)
    mir = (m("sub_ri",d="r0",k=1),m("cmp_ri",d="r0",k=0),m("jnz",t="end"),m("ret"),label,m("ret"))
    result = _peephole(mir,target,"f")
    assert not any(i.op=="cmp" for i in result)
    other = (m("sub_ri",d="r1",k=1),*mir[1:])
    assert any(i.op=="cmp" for i in _peephole(other,target,"f"))


def test_stack_pairs_and_memory_forwarding():
    target,m = fixture("t2")
    result = _peephole((m("push",s="x1"),m("pop",d="x0"),m("ret")),target,"f")
    assert result[0].form.id=="movw" and not any(i.op in ("push","pop") for i in result)
    target,m = fixture()
    mir = (m("st",m=MemLoc("sram",0x30),s="r1"),m("ld",d="r0",m=MemLoc("sram",0x30)),m("ret"))
    result = _peephole(mir,target,"f")
    assert result[1].op=="mov" and result[1].values=={"d":"r0","s":"r1"}
    changed = (mir[0],m("ldi",d="r1",k=3),mir[1],m("add_rr",d="r0",s="r1"),mir[2])
    assert any(i.op=="ld" for i in _peephole(changed,target,"f"))


def test_forwarded_operands_are_verified_against_ir_values():
    from types import SimpleNamespace
    from rpkbin.mcucc import CompileConfig
    from rpkbin.mcucc.ir import Function,Block,Instr,VReg
    from rpkbin.mcucc.target import RegLoc
    from rpkbin.mcucc.verify import _verify
    target,m=fixture()
    a,b,c,d=[VReg(i,8) for i in range(4)]
    abi={"args":("r0","r1"),"ret":"r0","preserves":(),"clobbers":()}
    block=Block(0,[Instr("entry",None,(a,b)),Instr("mov",c,(b,)),Instr("add",d,(a,c))],Instr("ret",None,(d,)))
    function=Function("f",[a,b],8,[block],abi)
    mir=(m("mov",d="r2",s="r1"),m("add_rr",d="r0",s="r2"),m("ret"))
    mir=tuple(__import__('dataclasses').replace(i,_origin=(0,n+1),_abi=abi) for n,i in enumerate(mir))
    transformed=_peephole(mir,target,"f")
    result=SimpleNamespace(name="f",_ir=function,_locations={a:RegLoc("r0"),b:RegLoc("r1"),c:RegLoc("r2"),d:RegLoc("r0")},
        mir=transformed,bytes=sum(i.bytes for i in transformed),slots=(),clobbers=("r0","r1"))
    _verify(result,target,CompileConfig({},False),{"f":abi})
