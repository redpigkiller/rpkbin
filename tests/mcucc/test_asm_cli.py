import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from rpkbin.mcucc import CompileConfig,compile_source
from rpkbin.mcucc.assemble import assemble
from rpkbin.mcucc.diff import run_differential
from rpkbin.mcucc.errors import AllocationError,SourceError,UnsupportedError
from rpkbin.mcucc.lang import run_reference
from test_compile import source_for
from test_formcheck import toy


ROOT=Path(__file__).resolve().parents[2]
TARGET=Path(__file__).parent/'toys'/'t1.py'
SOURCE='''from rpkbin.mcucc.lang import u8,asm
def f(a:u8,b:u8)->u8:
    asm("opaque {0}, {1}",ins=((a,"r0"),(b,"r1")),outs=((a,"r0"),),clobbers=("r2",),nbytes=1,ref=lambda a,b:(a+b)&0xFF)
    return a
'''


def command(*args):
    return subprocess.run(
        [sys.executable, "-m", "rpkbin.mcucc", *map(str, args)],
        cwd=ROOT,
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        capture_output=True,
        encoding="utf-8",
    )


@pytest.mark.parametrize('with_ref',(True,False))
def test_opaque_asm_output_and_machine_verification_policy(with_ref):
    source=SOURCE if with_ref else SOURCE.replace(',ref=lambda a,b:(a+b)&0xFF','')
    target,binding,stubs=toy('t1',8)
    config=CompileConfig({'sram':(0x60,0x6f)},False)
    result=compile_source(source,target,config)
    assert result.asm=='f:\n    opaque r0, r1\n    ret\n'
    assert result.functions['f'].verified=='unverified: asm'
    assert result.report()['functions']['f']['verified']=='unverified: asm'
    with pytest.raises(UnsupportedError,match='f.*raw asm'):
        assemble(result,target,16)
    report=run_differential(source,target,binding,config,stubs=stubs,seeds=(1,),inputs_per_function=120,window=256)
    assert not report.failures and report.checked==0
    if with_ref:
        assert run_reference(source,'f',(250,9),{'sram':[0]*256},addr_bits={'sram':8})[0]==3
    else:
        with pytest.raises(SourceError,match='reference'):
            run_reference(source,'f',(1,2),{'sram':[0]*256},addr_bits={'sram':8})


def test_asm_constant_input_is_not_replaced_by_an_immediate():
    source='''from rpkbin.mcucc.lang import u8,asm
def f()->u8:
    a:u8=7
    asm("opaque {0}",ins=((a,"r2"),),outs=((a,"r2"),),nbytes=1,ref=lambda a:a+1)
    return a
'''
    result=compile_source(source,toy('t1',8)[0],CompileConfig({'sram':(0x60,0x6f)},False))
    assert '    ldi r2, #7\n    opaque r2\n' in result.asm
    assert run_reference(source,'f',(),{'sram':[0]*256},addr_bits={'sram':8})[0]==8


@pytest.mark.parametrize('name,width,hint',(('t1',8,'r0'),('t2',8,'r0'),('t2',16,'r0')))
def test_asm_clobber_saves_live_input(name,width,hint):
    source=f'''from rpkbin.mcucc.lang import u8,asm
def f(a:u8)->u8:
    asm("opaque {{0}}",ins=((a,"{hint}"),),clobbers=("{hint}",),nbytes=1,flags_ok=True,ref=lambda a:None)
    return a
'''
    result=compile_source(source,toy(name,width)[0],CompileConfig({'sram':(0x60,0x6f)},False))
    mir=result.functions['f'].mir
    opaque=next(i for i,m in enumerate(mir) if m._asm)
    assert any(m._role=='asm-save' for m in mir[:opaque])
    assert any(m._role=='asm-restore' for m in mir[opaque+1:])
    assert result.functions['f'].verified=='unverified: asm'


def test_asm_operand_order_deduplication_and_flags():
    source='''from rpkbin.mcucc.lang import u8,asm
def f(a:u8,b:u8)->u8:
    c:u8=0
    asm("opaque {0}, {1}, {2}",ins=((b,"r1"),(a,"r0"),(b,"r1")),outs=((c,"r2"),(a,"r0")),nbytes=3,ref=lambda b,a,b2:(b+a,a))
    return c+a
'''
    result=compile_source(source,toy('t1',8)[0],CompileConfig({'sram':(0x60,0x6f)},False))
    assert '    opaque r1, r0, r2\n' in result.asm
    from rpkbin.mcucc.machine import defs_uses
    raw=next(m for m in result.functions['f'].mir if m._asm)
    assert defs_uses(raw)[2]=={'Z','C'}


@pytest.mark.parametrize('replacement',('"opaque {9}"','"opaque {bad}"'))
def test_invalid_asm_placeholders_are_source_errors(replacement):
    with pytest.raises(SourceError):
        compile_source(SOURCE.replace('"opaque {0}, {1}"',replacement),toy('t1',8)[0],CompileConfig({},False))


def test_asm_hint_width_and_conflicts_are_explicit_errors():
    with pytest.raises(SourceError,match='width'):
        compile_source(SOURCE.replace('"r0"','"x0"'),toy('t2',8)[0],CompileConfig({},False))
    conflict=SOURCE.replace('outs=((a,"r0"),)','outs=((a,"r3"),)')
    with pytest.raises(AllocationError,match='f.*(hint|register|class)'):
        compile_source(conflict,toy('t1',8)[0],CompileConfig({},False))


def test_cli_compile_report_output_and_validation(tmp_path):
    source=tmp_path/'source.py'
    source.write_text(source_for('add2'),encoding='utf-8')
    base=('compile',source,'--target',TARGET,'--spill-pool','sram:0x60-0x6f','--no-comments')
    result=command(*base,'--report')
    assert result.returncode==0,result.stderr
    assert result.stdout=='add2:\n    add r0, r1\n    ret\n'
    assert json.loads(result.stderr)['functions']['add2']['bytes']==2
    output=tmp_path/'program.asm'
    result=command(*base,'-o',output)
    assert result.returncode==0 and not result.stdout
    assert output.read_text()== 'add2:\n    add r0, r1\n    ret\n'
    missing=command('verify',source,'--target',TARGET,'--spill-pool','sram:96-111')
    assert missing.returncode==2 and missing.stderr.strip()=='error: --window is required'
    invalid=command(*base,'--spill-pool','sram:0x70-0x7f')
    assert invalid.returncode==2 and 'error:' in invalid.stderr and 'Traceback' not in invalid.stderr


def test_cli_verify_asm_and_real_execution(tmp_path):
    source=tmp_path/'asm.py'
    source.write_text(SOURCE,encoding='utf-8')
    options=('--target',TARGET,'--spill-pool','sram:0x60-0x6f','--window','256')
    result=command('verify',source,*options)
    assert result.returncode==0 and 'f: unverified: asm' in result.stdout,(result.stdout,result.stderr)
    source.write_text(source_for('add2'),encoding='utf-8')
    result=command('verify',source,*options)
    assert result.returncode==0 and 'add2:' in result.stdout and 'verified' in result.stdout,(result.stdout,result.stderr)
    result=command('lint-target',TARGET)
    assert result.returncode==0 and 'add' in result.stdout and '16' in result.stdout


def test_cli_check_forms_and_missing_files(tmp_path):
    target=Path(__file__).parent/'toys'/'t0.py'
    result=command('check-forms',target)
    assert result.returncode==0,result.stderr
    result=command('compile',tmp_path/'missing.py','--target',TARGET,'--spill-pool','sram:96-111')
    assert result.returncode==2 and 'error:' in result.stderr and 'Traceback' not in result.stderr


def test_cli_verify_isolates_unsupported_functions(tmp_path):
    source=tmp_path/'mixed.py'
    source.write_text('from rpkbin.mcucc.lang import u8,u16\ndef add2(a:u8,b:u8)->u8:\n    return a+b\ndef wide(a:u16,b:u16)->u16:\n    return a+b\n',encoding='utf-8')
    result=command('verify',source,'--target',TARGET,'--spill-pool','sram:96-111','--window','256')
    assert result.returncode==0,(result.stdout,result.stderr)
    assert 'add2: verified' in result.stdout and 'wide: unsupported:' in result.stdout
