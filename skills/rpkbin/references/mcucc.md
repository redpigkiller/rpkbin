# `rpkbin.mcucc` Reference

Compiles a narrowed, static-typed Python subset (`rpkbin.mcucc.lang`) into custom MCU assembly, verifies `V1`–`V11` safety properties on every compile, and differentially tests behavior against an `isasim` `SIM`. Requires **Python >= 3.11** (uses AST `feature_version=(3, 11)`).

## Public API

- `rpkbin.mcucc`:
  - `CompileConfig(spill_pool: Mapping, comments: bool, *, park_depth: int = 0, park_policy: str = 'smallest')` (`park_policy`: `'smallest' | 'stack_first' | 'slot_first'`)
  - `compile_source(source, target, config, filename='<source>') -> CompileResult`
  - `compile_file(path, target, config) -> CompileResult`
  - `CompileResult(asm: str, functions: dict[str, FunctionResult])` (`.report()` omits MIR internals and includes `pool_contract`, `parks`, `push_depth`).
- `rpkbin.mcucc.target`:
  - `load_target(path) -> Target`
  - `load_target_file(path) -> tuple[Target, Any, Any]` (`target, sim, stubs` — `sim`/`stubs` are `None` if omitted)
  - Declarative classes: `Target`, `Space`, `Reg`, `Alias`, `Cls`, `Flag`, `Form`, `Abi`, `Formatter`, `R`, `I`, `M`, `P`, `T`, `SimBinding`, `TargetError`.
- `rpkbin.mcucc.assemble`: `assemble(result: CompileResult, target: Target, origin: int = 0) -> Program`
- `rpkbin.mcucc.formcheck`: `check_forms(target: Target, sim, seeds: int = 1) -> list`
- `rpkbin.mcucc.diff`: `run_differential(source, target, sim, config, *, stubs=None, seeds=..., inputs_per_function=..., window=..., post_check=None)`
- `rpkbin.mcucc.lang`: `u8`, `u16`, `mem`, `Ptr`, `extern`, `preserves`, `asm`, `run_reference(...)`.

## CLI (`rpk-mcucc` / `python -m rpkbin.mcucc`)

```bash
rpk-mcucc check-forms rpkbin/mcucc/examples/target.py.txt --seeds 1
rpk-mcucc compile prog.py --target target.py --spill-pool data:0x60-0x6f --report -o out.asm
rpk-mcucc verify prog.py --target target.py --spill-pool data:0x60-0x6f --window 256
rpk-mcucc lint-target target.py
rpk-mcucc --version
```

Note: `--window` accepts either a positive integer cell count from `0` or repeatable named ranges `SPACE:LO-HI` of width `>= 512`.

## Gotchas

- `unverified: no SIM` exits `0` when `SIM` is absent—check stdout/result flags before assuming simulator differential verification ran.
- `run_reference(module_path_or_source, ...)` accepts `Path(...)` directly for `.py.txt` bundled examples or plain source strings.
- Targets using `borrow_msb0` / `nborrow_msb0` flag semantics restrict unmasked comparisons to proven MSB-0 inputs (e.g. `(a & 0x7F) < (b & 0x7F)`).

## Recipes

### 1. Compile, Inspect Report, Assemble & Check Reference Semantics

```python
from pathlib import Path
from rpkbin.mcucc import CompileConfig, compile_file
from rpkbin.mcucc.assemble import assemble
from rpkbin.mcucc.lang import run_reference
from rpkbin.mcucc.target import load_target

target = load_target("rpkbin/mcucc/examples/target.py.txt")
config = CompileConfig({"data": (0x60, 0x6F)}, False)
result = compile_file("rpkbin/mcucc/examples/program.py.txt", target, config)
prog = assemble(result, target, origin=0)
ret, _ = run_reference(
    Path("rpkbin/mcucc/examples/program.py.txt"),
    "add2",
    (250, 9),
    {"data": [0] * 256},
    addr_bits={"data": 8},
)
assert len(prog.cells) > 0 and ret == 3
```
