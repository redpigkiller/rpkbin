import json
from pathlib import Path

import pytest

from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.diff import _module
from rpkbin.mcucc.errors import AllocationError, UnsupportedError
from test_compile import source_for
from test_formcheck import toy


HERE = Path(__file__).parent
BASELINE = json.loads((HERE / "corpus" / "v43-outputs.json").read_text(encoding="utf-8"))
V5_SOURCE = (HERE / "corpus" / "v5.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("target_key", tuple(BASELINE["outputs"]))
def test_v43_corpus_bytes_and_diagnostics(target_key):
    name, bits = target_key.split("-")
    target, _, _ = toy(name, int(bits))
    pool = (0x20, 0x2F) if name == "t0" else (0x60, 0x6F)
    sizes = json.loads((HERE / "sizes.json").read_text(encoding="utf-8"))
    assert sizes[target_key] == BASELINE["sizes"][target_key]
    for function, expected in BASELINE["outputs"][target_key].items():
        source = (source_for(function) if function in sizes[target_key]
                  else _module(V5_SOURCE, function))
        for comments in (False, True):
            config = CompileConfig({"sram": pool}, comments)
            if "unsupported" in expected or "allocation_error" in expected:
                error, field = ((UnsupportedError, "unsupported") if "unsupported" in expected
                                else (AllocationError, "allocation_error"))
                with pytest.raises(error) as failure:
                    compile_source(source, target, config)
                assert str(failure.value) == expected[field]
            else:
                actual = compile_source(source, target, config)
                key = "commented_asm" if comments else "asm"
                assert actual.asm.encode("utf-8") == expected[key].encode("utf-8")
                assert json.loads(json.dumps(actual.report())) == expected["report"]
                if function in sizes[target_key]:
                    assert actual.functions[function].bytes == sizes[target_key][function]
