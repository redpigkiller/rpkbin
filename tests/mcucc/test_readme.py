import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest


@pytest.mark.parametrize("language", ["en", "zh"])
def test_readme_files_and_every_command_are_executable(tmp_path, language):
    root=Path(__file__).resolve().parents[2]
    readme=(root / "docs" / language / "mcucc" / "README.md").read_text(encoding='utf-8')
    blocks=re.findall(r'```python\n(.*?)```',readme,re.S)
    files={}
    for block in blocks:
        first,_,body=block.partition('\n')
        if first.startswith('# ') and first.endswith('.py'):
            name=first[2:]
            assert Path(name).name==name
            (tmp_path/name).write_text(body,encoding='utf-8')
            files[name]=body
    assert {'small_target.py','program.py','existing.py','inline.py','api_check.py'}<=files.keys()
    commands=[line.strip() for block in re.findall(r'```sh\n(.*?)```',readme,re.S) for line in block.splitlines() if line.strip() and not line.startswith('#')]
    assert len(commands)>=8
    results=[]
    for line in commands:
        arguments=shlex.split(line)
        assert arguments[0]=='python'
        if 'verify' in arguments:
            assert '--window' in arguments
        result=subprocess.run([sys.executable,*arguments[1:]],cwd=tmp_path,
            env=dict(os.environ,PYTHONPATH=str(root)),capture_output=True,text=True)
        assert result.returncode==0,(line,result.stdout,result.stderr)
        if 'check-forms' in arguments:
            assert 'c9 skipped: out_of_space=0 ret_not_fit=0' in result.stdout.splitlines()
            assert result.stdout.strip() in readme
        results.append(result.stdout)
    assert (tmp_path/'program.asm').read_text()=='add2:\n    add a, b\n    ret\n'
    assert any('verified: forms' in result for result in results)
    assert any('add2: verified' in result for result in results)
    assert any('use_existing: verified' in result for result in results)
    assert any('inline: unverified: asm' in result for result in results)
