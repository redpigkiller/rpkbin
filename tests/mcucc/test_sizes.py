import json
from pathlib import Path
import pytest

from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.errors import UnsupportedError
from test_compile import source_for
from test_formcheck import toy


@pytest.mark.parametrize("target_name,width", (("t0",8),("t1",8),("t2",8),("t2",16)))
def test_corpus_does_not_grow(target_name,width):
    baseline=json.loads(Path(__file__).with_name("sizes.json").read_text())
    target=toy(target_name,width)[0]
    config=CompileConfig({"sram":(0x20,0x2f) if target_name=="t0" else (0x60,0x6f)},False)
    for name,length in baseline[f"{target_name}-{width}"].items():
        if length is None:
            with pytest.raises(UnsupportedError):
                compile_source(source_for(name),target,config)
        else:
            function=compile_source(source_for(name),target,config).functions[name]
            assert function.bytes<=length,(target_name,width,name,function.bytes,length)
            assert not function.findings
