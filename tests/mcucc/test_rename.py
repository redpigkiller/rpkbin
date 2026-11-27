import ast
from pathlib import Path
import re
import pytest

from rpkbin.mcucc import CompileConfig,compile_source
from rpkbin.mcucc.diff import run_differential
from rpkbin.mcucc.errors import UnsupportedError
from rename_target import renamed,source_names
from test_compile import source_for
from test_formcheck import toy
from test_asm_cli import SOURCE


@pytest.mark.parametrize('label,width',(('t0',8),('t1',8),('t2',8),('t2',16)))
def test_all_corpus_with_renamed_target(label,width):
    target,binding,stubs=toy(label,width)
    modified,native,names=renamed(target,binding)
    reverse={value:key for key,value in names.items()}
    pattern=re.compile(r'\b('+ '|'.join(map(re.escape,sorted(reverse,key=len,reverse=True)))+r')\b')
    pool=(0x20,0x2f) if label=='t0' else (0x60,0x6f)
    before=CompileConfig({'sram':pool},False)
    after=CompileConfig({names['sram']:pool},False)
    functions=[node.name for node in ast.parse((Path(__file__).parent/'corpus'/'programs.py').read_text()).body if isinstance(node,ast.FunctionDef)]
    for name in functions:
        source=source_for(name)
        try:
            original=compile_source(source,target,before)
        except UnsupportedError:
            with pytest.raises(UnsupportedError):
                compile_source(source_names(source,names),modified,after)
            continue
        result=compile_source(source_names(source,names),modified,after)
        restored=pattern.sub(lambda match:reverse[match[0]],result.asm)
        assert restored==original.asm,(label,width,name,original.asm,result.asm)
    report=run_differential(source_names(source_for('add2'),names),modified,native,after,
        stubs=stubs,seeds=(19,),inputs_per_function=120,window=256)
    assert not report.failures and report.checked>=120


def test_decorators_asm_hints_and_pointer_spaces_are_all_renamed():
    target,binding,_=toy('t1',8)
    modified,_,names=renamed(target,binding)
    source=SOURCE.replace('def f','@fn(args=("r0","r1"),ret="r0",preserves=("r3",))\ndef f').replace('u8,asm','u8,asm,fn')
    changed=source_names(source,names)
    assert '"r0"' not in changed and "'r0'" not in changed
    result=compile_source(changed,modified,CompileConfig({names['sram']:(0x60,0x6f)},False))
    assert result.functions['f'].verified=='unverified: asm'
    assert f'opaque {names["r0"]}, {names["r1"]}' in result.asm
