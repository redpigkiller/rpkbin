# `rpkbin.mcucc`

[繁體中文](../../zh/mcucc/README.md) · [Documentation Index](../README.md) · [isasim Quickstart](../isasim/README.md)

`rpkbin.mcucc` compiles a restricted subset of Python into target assembly, runs an independent static verifier on every compilation, and optionally checks instruction encodings and whole functions against an `isasim` simulator.

## Installation

Requires Python 3.11 or newer (`pyproject.toml`, frontend `feature_version=(3, 11)`). From the repository root, install the package in editable mode:

```bash
python -m pip install -e .
```

Installing the package exposes the `rpk-mcucc` console command alongside `python -m rpkbin.mcucc`. Core compilation uses only the Python standard library; form and differential simulator checks use the bundled [`rpkbin.isasim`](../isasim/README.md) package.

## CLI Reference

### Syntax

```text
rpk-mcucc [-h] [-V] {compile,verify,lint-target,check-forms} ...
rpk-mcucc lint-target target
rpk-mcucc check-forms target [--seeds N]
rpk-mcucc compile source --target TARGET --spill-pool SPACE:LO-HI [--park-depth N] [--park-policy {smallest,stack_first,slot_first}] [--no-comments] [-o OUTPUT | --output OUTPUT] [--report]
rpk-mcucc verify source --target TARGET --spill-pool SPACE:LO-HI --window WINDOW [--park-depth N] [--park-policy {smallest,stack_first,slot_first}] [--inputs N] [--seed S]
python -m rpkbin.mcucc ...
```

### Description

- `rpk-mcucc` and `python -m rpkbin.mcucc` invoke the same parser and subcommands (`rpkbin.mcucc.cli.main`). Passing `-V` or `--version` prints `<prog> <version>` from `rpkbin.__version__` and exits.
- `rpk-mcucc lint-target target` loads and validates a target file, then prints a tab-separated availability matrix (`op`, `width`, `available`, `forms`, `min_bytes`) to stdout.
- `rpk-mcucc check-forms target [--seeds N]` tests declared `Form` encoders and semantics against `SIM` across seeds `0` through `N - 1`. Without `SIM`, it prints `unverified: no SIM` and exits `0`.
- `rpk-mcucc compile source ...` compiles restricted Python source into target assembly, runs static verification (`V1` through `V11`), and writes assembly to stdout or `-o` / `--output`. With `--report`, it prints JSON report metadata to stderr.
- `rpk-mcucc verify source ...` compiles each function and runs differential verification against `SIM` across boundary and random inputs, comparing return values, checked data memory, and registers outside `clobbers`. Without `SIM`, it prints `unverified: no SIM` (or `unverified: asm` / `unsupported: ...`) per function and exits `0`.

### Examples

Run the bundled example from the repository root to print the version, check target forms, compile `add2`, and run differential verification:

```bash
python -m rpkbin.mcucc --version
python -m rpkbin.mcucc check-forms rpkbin/mcucc/examples/target.py.txt --seeds 1
python -m rpkbin.mcucc compile rpkbin/mcucc/examples/program.py.txt --target rpkbin/mcucc/examples/target.py.txt --spill-pool data:0x60-0x6f --no-comments --report
python -m rpkbin.mcucc verify rpkbin/mcucc/examples/program.py.txt --target rpkbin/mcucc/examples/target.py.txt --spill-pool data:0x60-0x6f --window 256
```

Output:

```text
python -m rpkbin.mcucc 0.3.0
c9 skipped: out_of_space=0 ret_not_fit=0
verified: forms
{"functions": {"add2": {"bytes": 2, "clobbers": ["a", "b"], "slots": [], "findings": [], "verified": true}}}
add2:
    inksum a, b
    inkdone
add2: verified
```

For targets with wider data spaces where low addresses are reserved (for example, memory-mapped I/O or ABI cells), specify a named window of at least 512 cells and optional stack parking flags:

```bash
rpk-mcucc compile source.py --target target.py --spill-pool data:0x60-0x6f --park-depth 4 --park-policy smallest -o out.asm
rpk-mcucc verify source.py --target target.py --spill-pool data:0x60-0x6f --window data:0x300-0x5ff
```

### Options

| Flag | Env Var | Default | Description |
|---|---|---|---|
| `-h`, `--help` | (none) | (none) | Show help for the command or subcommand and exit. |
| `-V`, `--version` | (none) | (none) | Print `<prog> <version>` from `rpkbin.__version__` and exit. |
| `--target TARGET` | (none) | required (`compile`, `verify`) | Path to a trusted Python target description defining `TARGET` (and optional `SIM`, `STUBS`). |
| `--spill-pool SPACE:LO-HI` | (none) | required (`compile`, `verify`) | Inclusive compiler-reserved range in one data space (`SPACE:LO-HI`, decimal or `0x` hex). Rejects more than one data space. |
| `--park-depth N` | (none) | `0` | Maximum simultaneous stack `push` count allowed across register parking, `preserves`, call saves, and module callees (`0` disables parking). |
| `--park-policy {smallest,stack_first,slot_first}` | (none) | `smallest` | Variant selection when `--park-depth > 0`: `smallest` picks fewer bytes (ties keep unparked), `stack_first` picks the parked variant if unparked used spill slots, `slot_first` keeps the unparked variant. |
| `--no-comments` | (none) | `False` | (`compile`) Omit function header comments (`bytes`, `clobbers`, `slots`) from emitted assembly. |
| `-o OUTPUT`, `--output OUTPUT` | (none) | `None` (stdout) | (`compile`) Write UTF-8 assembly with LF (`\n`) line endings to `OUTPUT`. An empty path raises `ConfigError` (exit `2`). |
| `--report` | (none) | `False` | (`compile`) Write JSON `CompileResult.report()` to stderr. |
| `--window WINDOW` | (none) | required (`verify`) | Either a positive integer cell count from `0` (for example `256`) or a repeatable named range `SPACE:LO-HI` (decimal or `0x` hex, inclusive, width `>= 512`). Cannot mix or repeat integer windows. |
| `--inputs N` | (none) | `120` | (`verify`) Minimum sampled inputs per function (`>= 1`). |
| `--seed S` | (none) | `0` | (`verify`) Random seed passed to differential input generation. |
| `--seeds N` | (none) | `1` | (`check-forms`) Number of seeds `0..N-1` (`>= 1`). |

The `rpk-mcucc` CLI does not read environment variables.

### Exit Codes and Tips

- `0`: Command completed cleanly. Note that `unverified: no SIM`, `unverified: asm`, and `unsupported: ...` still exit `0` when no comparison mismatch occurred; check per-function status lines before treating a file as verified.
- `1`: `check-forms` found one or more instruction mismatches, or `verify` recorded failures in `report.failures` (including return/memory mismatches, unreported register clobbers, pool/window violations, or a per-function reference timeout rate above 10%).
- `2`: Invalid CLI syntax (`argparse` error) or an `McuccError` (`SourceError`, `TargetError`, `UnsupportedError`, `AllocationError`, `ConfigError`, `InternalError`) printed to stderr as `error: ...` (for example `error: --window is required` when `--window` is omitted on `verify`, or empty `-o ""`).

## Python API (`rpkbin.mcucc`)

### Syntax

```python
from rpkbin.mcucc import CompileConfig, CompileResult, FunctionResult, compile_file, compile_source
from rpkbin.mcucc.target import load_target, load_target_file
from rpkbin.mcucc.assemble import assemble
from rpkbin.mcucc.formcheck import check_forms
from rpkbin.mcucc.diff import run_differential
from rpkbin.mcucc.lang import run_reference
from rpkbin.mcucc.cli import main

config = CompileConfig(spill_pool, comments, *, park_depth=0, park_policy="smallest")
result = compile_source(source, target, config, filename="<source>")
result = compile_file(path, target, config)
report_dict = result.report()

target = load_target(path)
target, sim, stubs = load_target_file(path)
program = assemble(result, target, origin)
failures = check_forms(target, sim, seeds)
diff_report = run_differential(source, target, sim, config, *, stubs=None, seeds, inputs_per_function, window, post_check=None)
ret, memory_after = run_reference(module_path_or_source, func_name, args, memory, *, addr_bits)
exit_code = main(argv=None)
```

### Description

- `CompileConfig(spill_pool, comments, *, park_depth=0, park_policy="smallest")` validates compilation settings and freezes a shallow copy of `spill_pool` as a `MappingProxyType`. Pass `{}` for `spill_pool` to compile without spill slots.
- `compile_source(source, target, config, filename="<source>")` and `compile_file(path, target, config)` compile a restricted Python module against `target` and return a `CompileResult` whose static verifier (`V1` through `V11`) has passed.
- `result.report()` returns a JSON-serializable dict keyed by `"functions"`, omitting `mir` instruction records and adding `"pool_contract": "private"` or `"parks"`/`"push_depth"` when applicable.
- `load_target(path)` and `load_target_file(path)` execute a trusted Python target file via `runpy.run_path`, validate `TARGET`, and return `Target` or `(target, sim, stubs)` with `None` for omitted optional bindings.
- `assemble(result, target, origin)` runs two-pass placement from nonnegative integer `origin`, invokes each form's `encode` callback, and returns a `Program`. It raises `UnsupportedError` if any function contains inline `asm`.
- `check_forms(target, sim, seeds)` checks form encodings, lengths, arithmetic, flags, memory, branches, stack pairs, and call/return behavior. When `sim` is `None`, it returns `[]`; verify `sim is not None` before treating an empty list as verified.
- `run_differential(...)` compiles each non-extern function, builds a driver calling it, runs `run_reference` alongside `sim`, checks returns, memory outside `spill_pool`, and registers outside `clobbers`, and returns a `DiffReport`.
- `run_reference(module_path_or_source, func_name, args, memory, *, addr_bits)` executes restricted Python semantics in isolation against a deep copy of `memory` and returns `(ret, memory_after)`.
- `main(argv=None)` runs the CLI entry point with `prog="rpk-mcucc"`.

### Examples

#### 1. Minimal Example: Compile, Report, Assemble, and Run Reference

Compile the bundled `program.py.txt` against `target.py.txt`, inspect `result.report()`, encode machine cells with `assemble`, and cross-check with `run_reference` using a `Path` object.

```python
from pathlib import Path
from rpkbin.mcucc import CompileConfig, compile_file
from rpkbin.mcucc.assemble import assemble
from rpkbin.mcucc.lang import run_reference
from rpkbin.mcucc.target import load_target

target = load_target("rpkbin/mcucc/examples/target.py.txt")
config = CompileConfig({"data": (0x60, 0x6F)}, False)
result = compile_file("rpkbin/mcucc/examples/program.py.txt", target, config)
print(result.asm.strip())
print("report:", result.report())

program = assemble(result, target, origin=0)
print("cells:", [program.cells[a] for a in sorted(program.cells)])

ret, _ = run_reference(
    Path("rpkbin/mcucc/examples/program.py.txt"),
    "add2",
    (250, 9),
    {"data": [0] * 256},
    addr_bits={"data": 8},
)
print("ref_ret:", ret)
```

Output:

```text
add2:
    inksum a, b
    inkdone
report: {'functions': {'add2': {'bytes': 2, 'clobbers': ('a', 'b'), 'slots': [], 'findings': (), 'verified': True}}}
cells: [17, 97]
ref_ret: 3
```

#### 2. Progressive Example: Stack Parking, Private Spill Pool, and `borrow_msb0`

Compare `park_policy` variants under `park_depth=4`, trigger automatic private-pool fallback (`pool_contract="private"`) when a value stays live across an indirect write, and observe how `borrow_msb0` accepts masked comparisons (`& 0x7F`) while rejecting unproved `a < b`.

```python
from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.errors import UnsupportedError
from rpkbin.mcucc.target import Abi, Cls, Flag, Form, I, M, P, R, Reg, Space, T, Target

regs = ("a", "b", "c")
park_target = Target(
    name="park_demo",
    cell_bits=8,
    spaces=(Space("code", 12, "program"), Space("data", 8, "data")),
    regs=tuple(Reg(r, 8) for r in regs),
    aliases=(),
    classes=(Cls("byte", regs),),
    flags=(Flag("Z"), Flag("C")),
    forms=(
        Form("mov", "mov", 8, (R("d", "byte"), R("s", "byte")), {"dst": "d", "a": "s"}, 1, "mov {d}, {s}"),
        Form("ldi", "const", 8, (R("d", "byte"), I("k", 8)), {"dst": "d", "a": "k"}, 2, "ldi {d}, #{k}"),
        Form("add", "add", 8, (R("d", "byte"), R("s", "byte")), {"dst": "d", "a": "d", "b": "s"}, 1, "add {d}, {s}"),
        Form("and_ri", "and", 8, (R("d", "byte"), I("k", 8)), {"dst": "d", "a": "d", "b": "k"}, 2, "and {d}, #{k}"),
        Form("ld", "ld", 8, (R("d", "byte"), M("m", "data")), {"dst": "d", "a": "m"}, 2, "ld {d}, [{m}]"),
        Form("st", "st", 8, (M("m", "data"), R("s", "byte")), {"a": "m", "b": "s"}, 2, "st [{m}], {s}"),
        Form("st_i", "st_ind", 8, (P("p", "byte", "data"), R("s", "byte")), {"a": "p", "b": "s"}, 2, "st [{p}], {s}"),
        Form("push", "push", 8, (R("s", "byte"),), {"a": "s"}, 1, "push {s}"),
        Form("pop", "pop", 8, (R("d", "byte"),), {"dst": "d"}, 1, "pop {d}"),
        Form("cmp", "cmp", 8, (R("a", "byte"), R("b", "byte")), {"a": "a", "b": "b"}, 1,
             "cmp {a}, {b}", flags_write={"C": "borrow_msb0", "Z": "zero"}),
        Form("jc", "br", 0, (T("t", rel_bits=8),), {}, 2, "jc {t}", flags_read={"C": 1}),
        Form("jnc", "br", 0, (T("t", rel_bits=8),), {}, 2, "jnc {t}", flags_read={"C": 0}),
        Form("jz", "br", 0, (T("t", rel_bits=8),), {}, 2, "jz {t}", flags_read={"Z": 1}),
        Form("jnz", "br", 0, (T("t", rel_bits=8),), {}, 2, "jnz {t}", flags_read={"Z": 0}),
        Form("jmp", "jmp", 0, (T("t", rel_bits=8),), {}, 2, "jmp {t}"),
        Form("ret", "ret", 0, (), {}, 1, "ret"),
    ),
    abi=Abi({8: regs}, {8: "a"}, ()),
    long_branch="none",
    comment=";",
    local_label=".L{fn}_{n}",
    label_def="{name}:",
)

park_src = """from rpkbin.mcucc.lang import mem, u8

def mix(a: u8, b: u8) -> u8:
    k = a + b
    x = mem.data[0x10]
    y = mem.data[0x11]
    z = mem.data[0x12]
    mem.data[0x20] = x + y
    mem.data[0x21] = y + z
    return k + mem.data[0x20] + mem.data[0x21]
"""
for policy in ("slot_first", "smallest", "stack_first"):
    cfg = CompileConfig({"data": (0x60, 0x6F)}, False, park_depth=4, park_policy=policy)
    info = compile_source(park_src, park_target, cfg).report()["functions"]["mix"]
    print(f"{policy}: bytes={info['bytes']} slots={info['slots']} parks={info['parks']} push_depth={info['push_depth']}")

private_src = """from rpkbin.mcucc.lang import Ptr, mem, u8

DataPtr = Ptr("data")

def keep_and_cmp(p: DataPtr, a: u8, b: u8) -> u8:
    k = a + b
    x = mem.data[0x10]
    y = mem.data[0x11]
    z = mem.data[0x12]
    mem.data[p] = x
    if (a & 0x7F) < (y & 0x7F):
        return k + x + y + z
    return z + 1
"""
priv_info = compile_source(
    private_src, park_target, CompileConfig({"data": (0x60, 0x6F)}, False)
).report()["functions"]["keep_and_cmp"]
print("private:", priv_info["pool_contract"], "verified:", priv_info["verified"])

try:
    compile_source(
        "from rpkbin.mcucc.lang import u8\ndef raw_lt(a: u8, b: u8) -> u8:\n    return u8(1) if a < b else u8(0)\n",
        park_target,
        CompileConfig({"data": (0x60, 0x6F)}, False),
    )
except UnsupportedError as exc:
    print("unproved_msb0:", type(exc).__name__)
```

Output:

```text
slot_first: bytes=28 slots=[('data', 96, 1), ('data', 97, 1)] parks=0 push_depth=0
smallest: bytes=20 slots=[] parks=1 push_depth=1
stack_first: bytes=20 slots=[] parks=1 push_depth=1
private: private verified: partial: indirect memory is abstract
unproved_msb0: UnsupportedError
```

#### 3. Differential Verification, Dict `window`, and Inline `asm` / `@extern`

Run `run_differential` with an integer `window` and `post_check`, repeat on a 10-bit data space using a 512-cell dict `window={"data": (0x200, 0x3FF)}`, and compile a function combining `@extern` and inline `asm`.

```python
from dataclasses import replace
from rpkbin.isasim import MCU, Simulator
from rpkbin.mcucc import CompileConfig, compile_source
from rpkbin.mcucc.diff import run_differential
from rpkbin.mcucc.lang import run_reference
from rpkbin.mcucc.target import SimBinding, Space, load_target_file

base_target, base_sim, _ = load_target_file("rpkbin/mcucc/examples/target.py.txt")
depths = []
report = run_differential(
    "from rpkbin.mcucc.lang import u8\ndef add2(a: u8, b: u8) -> u8:\n    return a + b\n",
    base_target,
    base_sim,
    CompileConfig({"data": (0x60, 0x6F)}, False),
    seeds=(0,),
    inputs_per_function=10,
    window=256,
    post_check=lambda phase, sim: depths.append((phase, sim.read("depth"))),
)
print("int_window:", report.checked, report.failures, depths[:2])

def rr_op(dd, ss, add):
    def step(st):
        st.reg[dd] = ((st.reg[dd] + st.reg[ss]) if add else st.reg[ss]) & 0xFF
    return step

m = MCU("win10")
m.space("code", cell_bits=8, size=4096, access="ro", reset_state="initialized", init=0, is_program=True)
m.space("data", cell_bits=8, size=1024, access="rw", reset_state="initialized", init=0)
m.space("frames", cell_bits=8, size=4096, access="rw", reset_state="initialized", init=0)
for r in ("a", "b"):
    m.reg(r, bits=8, reset=0)
m.reg("cursor", bits=12, reset=0, is_pc=True)
m.reg("depth", bits=16, reset=0)
m.on_undefined_opcode("stop")
m.on_out_of_range("stop")
m.on_write_to_readonly("stop")
m.on_uninitialized_read("stop")
m.instr("00000000", syntax="halt", cycles=1, name="halt")(lambda st: st.halt("done"))
for d, dst in enumerate(("a", "b")):
    for s, src in enumerate(("a", "b")):
        m.instr(format(0x10 + 2 * d + s, "08b"), syntax=f"inksum {dst}, {src}", cycles=1, name=f"add_{d}_{s}")(rr_op(dst, src, True))
        m.instr(format(0x20 + 2 * d + s, "08b"), syntax=f"inkcopy {dst}, {src}", cycles=1, name=f"mov_{d}_{s}")(rr_op(dst, src, False))
m.instr("011000000000" + "t" * 12, syntax="inkcall {t}", cycles=1, name="call")(
    lambda st, t: (st.mem["frames"].write_word(st.reg["depth"], 2, st.next_pc, "little"),
                   st.reg.__setitem__("depth", st.reg["depth"] + 2),
                   setattr(st, "next_pc", t)))
m.instr("01100001", syntax="inkdone", cycles=1, name="ret")(
    lambda st: (st.reg.__setitem__("depth", st.reg["depth"] - 2),
                setattr(st, "next_pc", st.mem["frames"].read_word(st.reg["depth"], 2, "little"))))
m.instr("01100010" + "t" * 8, syntax="inkskip {t}", cycles=1, name="jmp")(
    lambda st, t: setattr(st, "next_pc", st.next_pc + (t if t < 128 else t - 256)))

target_10 = replace(base_target, spaces=(Space("code", 12, "program"), Space("data", 10, "data")))
sim_10 = SimBinding(lambda: Simulator(m), "code", "cursor", (0,))
dict_report = run_differential(
    "from rpkbin.mcucc.lang import u8\ndef add2(a: u8, b: u8) -> u8:\n    return a + b\n",
    target_10,
    sim_10,
    CompileConfig({"data": (0x60, 0x6F)}, False),
    seeds=(0,),
    inputs_per_function=10,
    window={"data": (0x200, 0x3FF)},
)
print("dict_window:", dict_report.checked, dict_report.failures)

asm_src = """from rpkbin.mcucc.lang import asm, extern, u8

@extern(args=("a",), ret="a", clobbers=())
def existing(x: u8) -> u8:
    return x + 1

def step_both(a: u8) -> u8:
    asm("inc {0}", ins=((a, "a"),), outs=((a, "a"),),
        clobbers=("b",), nbytes=1, ref=lambda v: (v + 1) & 0xFF)
    return existing(a)
"""
asm_info = compile_source(asm_src, base_target, CompileConfig({"data": (0x60, 0x6F)}, False)).report()["functions"]["step_both"]
ref_val, _ = run_reference(asm_src, "step_both", (10,), {"data": [0] * 256}, addr_bits={"data": 8})
print("asm_verified:", asm_info["verified"], "ref_val:", ref_val)
```

Output:

```text
int_window: 120 () [('before', 0), ('after', 0)]
dict_window: 120 ()
asm_verified: unverified: asm ref_val: 12
```

### Input Arguments

| Name | Type | Default | Description |
|---|---|---|---|
| `spill_pool` | `Mapping[str, tuple[int, int]]` | required (`CompileConfig`) | Inclusive `(low, high)` bounds for at most one data space, or `{}` to disable spill slots. |
| `comments` | `bool` | required (`CompileConfig`) | Exact `bool`; enables per-function size, clobber, and slot header comments in `asm`. |
| `source` | `str` | required (`compile_source`, `run_differential`) | Restricted Python source text. |
| `path` | `str \| PathLike` | required (`compile_file`, `load_target`, `load_target_file`) | UTF-8 path to source file or trusted target definition file. |
| `target` | `Target` | required | Validated target machine declaration. |
| `config` | `CompileConfig` | required | Explicit spill pool, comment, and stack-parking configuration. |
| `filename` | `str` | `"<source>"` (`compile_source`) | Filename shown in diagnostics. |
| `origin` | `int` | required (`assemble`) | Nonnegative starting address in program cells. |
| `sim` | `SimBinding \| None` | required | Simulator binding (`check_forms` returns `[]` on `None`; `run_differential` requires a `SimBinding`). |
| `seeds` | `Iterable[int]` | required (`check_forms`, `run_differential`) | Random seeds for form checking or differential input sampling. |
| `inputs_per_function` | `int` | required (`run_differential`) | Minimum differential test inputs per function (minimum 120 when a function has 2 or more parameters). |
| `window` | `int \| Mapping[str, tuple[int, int]]` | required (`run_differential`) | Integer prefix size from `0`, or `{space: (low, high)}` inclusive intervals of width `>= 512`. |
| `module_path_or_source` | `Path \| str` | required (`run_reference`) | `Path`, single-line `.py` file path `str`, or multi-line source `str`. |
| `func_name` | `str` | required (`run_reference`) | Name of the function to run. |
| `args` | `Sequence[int]` | required (`run_reference`) | Positional arguments converted to the parameter types of `func_name`. |
| `memory` | `dict[str, list[int]]` | required (`run_reference`) | Initial data cell lists keyed by data space name (deep-copied per call). |
| `argv` | `Sequence[str] \| None` | `None` (`main`) | CLI argument list excluding program name (`None` reads `sys.argv[1:]`). |

### Name-Value Arguments

| Name | Type | Default | Description |
|---|---|---|---|
| `park_depth` | `int` | `0` (`CompileConfig`) | Nonnegative integer maximum concurrent `push` count across parking, saves, and callees. |
| `park_policy` | `str` | `"smallest"` (`CompileConfig`) | `"smallest"`, `"stack_first"`, or `"slot_first"` when `park_depth > 0`. |
| `stubs` | `Mapping[str, tuple[int, ...]] \| None` | `None` (`run_differential`) | Machine cell tuples keyed by `@extern` function name (`None` is treated as `{}`). |
| `post_check` | `Callable[[str, Simulator], None] \| None` | `None` (`run_differential`) | Hook called with `("before", sim)` and `("after", sim)`; `AssertionError` records an input failure. |
| `addr_bits` | `Mapping[str, int]` | required (`run_reference`) | Address width (`8` or `16`) for each data space referenced by `Ptr` or `mem`. |

### Output

- `compile_source` and `compile_file` return `CompileResult(asm, functions)`, where `asm` is the rendered assembly string and `functions` maps function names in source order to `FunctionResult(name, bytes, clobbers, slots, findings, verified, mir)`.
- `CompileResult.report()` returns `{"functions": {name: entry, ...}}` with fields:
  - `bytes` (`int`): static assembly byte count.
  - `clobbers` (`tuple[str, ...]`): base registers that may change across a call.
  - `slots` (`list[tuple[str, int, int]]`): allocated spill slots as `(space, start, cells)`.
  - `findings` (`tuple[dict, ...]`): residual peephole rewrite matches (`rule`, `function`, `index`).
  - `verified` (`bool | str`): `True`, `"partial: ..."` (when abstract operations such as indirect memory or calls limit full intra-block proof), or `"unverified: asm"`.
  - `pool_contract` (`str`, optional): present with value `"private"` only when allocation fell back from conservative aliasing to private-pool mode.
  - `parks`, `push_depth` (`int`, optional): present only when `park_depth > 0`; number of park `push`/`pop` pairs used and peak concurrent `push` depth including callees.
- `assemble` returns `Program(cells, symbols, sizes)`.
- `check_forms` returns `list[Failure]`, each with `form_id`, `check`, `seed`, and `detail`.
- `run_differential` returns `DiffReport(checked, failures, unsupported, timeouts)`.
- `run_reference` returns `(ret, memory_after)`, where `ret` is `None` for void functions.

## Executable Walkthrough (`small_target.py`)

### Compile an addition function

Create `program.py`:

```python
# program.py
from rpkbin.mcucc.lang import u8

def add2(a: u8, b: u8) -> u8:
    return a + b
```

Create `small_target.py` with the complete example below. The model has two registers and just enough instructions for the walkthrough below. For your own target, supply instruction encodings and simulator behavior that match your hardware.

<details>
<summary>Complete small_target.py</summary>

```python
# small_target.py
from rpkbin.mcucc.target import (
    Target, Space, Reg, Cls, Form, R, I, T, Abi, SimBinding,
)
from rpkbin.isasim import MCU, Simulator

names = ("a", "b")
mask = (1 << 8) - 1

def rr_encoder(base):
    def encode(v):
        return (base + 2 * names.index(v["d"]) + names.index(v["s"]),)
    return encode

forms = (
    Form("add_rr", "add", 8, (R("d", "byte"), R("s", "byte")),
         {"dst": "d", "a": "d", "b": "s"}, 1, "add {d}, {s}",
         encode=rr_encoder(0x10)),
    Form("mov", "mov", 8, (R("d", "byte"), R("s", "byte")),
         {"dst": "d", "a": "s"}, 1, "mov {d}, {s}",
         encode=rr_encoder(0x20)),
    Form("add_ri", "add", 8, (R("d", "byte"), I("k", 8)),
         {"dst": "d", "a": "d", "b": "k"}, 2, "add {d}, #{k}",
         encode=lambda v: (0x40 + names.index(v["d"]), v["k"])),
    Form("call", "call", 0, (T("t", abs_bits=12),), {}, 3, "call {t}",
         encode=lambda v: (0x60, v["t"] >> 8, v["t"] & mask)),
    Form("ret", "ret", 0, (), {}, 1, "ret", encode=lambda v: (0x61,)),
    Form("jmp", "jmp", 0, (T("t", rel_bits=8),), {}, 2, "jmp {t}",
         encode=lambda v: (0x62, v["t"] & mask)),
)
TARGET = Target(
    name="small", cell_bits=8,
    spaces=(Space("code", 12, "program"), Space("data", 8, "data")),
    regs=tuple(Reg(name, 8) for name in names), aliases=(),
    classes=(Cls("byte", names),), flags=(), forms=forms,
    abi=Abi({8: names}, {8: "a"}, ()), long_branch="none",
    comment=";", local_label=".L{fn}_{n}", label_def="{name}:",
)

machine = MCU("small")
for name, width, access, program in (
    ("code", 12, "ro", True), ("data", 8, "rw", False),
    ("frames", 12, "rw", False),
):
    machine.space(name, cell_bits=8, size=1 << width, access=access,
                  is_program=program, reset_state="initialized", init=0)
for name in names:
    machine.reg(name, bits=8, reset=0)
machine.reg("cursor", bits=12, reset=0, is_pc=True)
machine.reg("depth", bits=16, reset=0)
machine.on_undefined_opcode("stop")
machine.on_out_of_range("stop")
machine.on_write_to_readonly("stop")
machine.on_uninitialized_read("stop")
machine.instr("00000000", syntax="halt", cycles=1, name="halt")(
    lambda state: state.halt("example"))

def rr_handler(destination, source, add):
    def execute(state):
        value = state.reg[source]
        if add:
            value += state.reg[destination]
        state.reg[destination] = value & mask
    return execute

def immediate_handler(destination):
    def execute(state, k):
        state.reg[destination] = (state.reg[destination] + k) & mask
    return execute

for d, destination in enumerate(names):
    for s, source in enumerate(names):
        for base, add, mnemonic in ((0x10, True, "add"), (0x20, False, "mov")):
            machine.instr(format(base + 2*d + s, "08b"),
                          syntax=f"{mnemonic} {destination}, {source}", cycles=1,
                          name=f"{mnemonic}_{d}_{s}")(
                rr_handler(destination, source, add))
    machine.instr(format(0x40 + d, "08b") + "k"*8,
                  syntax=f"add {destination}, {{k}}", cycles=1, name=f"add_i_{d}")(
        immediate_handler(destination))

def call_handler(state, t):
    depth = state.reg["depth"]
    state.mem["frames"].write_word(depth, 2, state.next_pc, "little")
    state.reg["depth"] = depth + 2
    state.next_pc = t

def return_handler(state):
    depth = state.reg["depth"] - 2
    state.next_pc = state.mem["frames"].read_word(depth, 2, "little")
    state.reg["depth"] = depth

def jump_handler(state, t):
    offset = t if t < (1 << 7) else t - (1 << 8)
    state.next_pc = state.next_pc + offset

machine.instr("011000000000" + "t"*12, syntax="call {t}", cycles=1)(call_handler)
machine.instr("01100001", syntax="ret", cycles=1)(return_handler)
machine.instr("01100010" + "t"*8, syntax="jmp {t}", cycles=1)(jump_handler)
SIM = SimBinding(lambda: Simulator(machine), "code", "cursor", (0,))
STUBS = {"existing": (*forms[2].encode({"d": "a", "k": 1}), 0x61)}
```

</details>

Compile `add2` to `program.asm`:

```sh
python -m rpkbin.mcucc compile program.py --target small_target.py --spill-pool data:0x60-0x6f --no-comments -o program.asm --report
```

`program.asm` contains:

```text
add2:
    add a, b
    ret
```

### Check the target and compare machine execution

```sh
python -m rpkbin.mcucc lint-target small_target.py
python -m rpkbin.mcucc check-forms small_target.py --seeds 1
python -m rpkbin.mcucc verify program.py --target small_target.py --spill-pool data:0x60-0x6f --window 256
```

`check-forms` output:

```text
c9 skipped: out_of_space=0 ret_not_fit=0
verified: forms
```

In `isasim` instruction handlers, `state.pc` and `state.cycle` are read-only; branches and calls update `state.next_pc`, and an external driver can update PC with `sim.poke("cursor", address)`.

### Compile and inspect in Python

```python
# api_check.py
from rpkbin.mcucc import CompileConfig, compile_file
from rpkbin.mcucc.target import load_target
from rpkbin.mcucc.assemble import assemble
from rpkbin.mcucc.lang import run_reference

target = load_target("small_target.py")
result = compile_file("program.py", target, CompileConfig({"data": (0x60, 0x6f)}, False))
assert result.functions["add2"].bytes == 2
assert not result.functions["add2"].findings
program = assemble(result, target, origin=16)
assert program.sizes["add2"] == 2
value, memory = run_reference("program.py", "add2", (250, 9),
                             {"data": [0] * 256}, addr_bits={"data": 8})
assert value == 3
```

```sh
python api_check.py
```

### Call an existing assembly function

```python
# existing.py
from rpkbin.mcucc.lang import u8, extern

@extern(args=("a",), ret="a", clobbers=())
def existing(x: u8) -> u8:
    return x + 1

def use_existing(x: u8) -> u8:
    return existing(x) + 2
```

```sh
python -m rpkbin.mcucc compile existing.py --target small_target.py --spill-pool data:0x60-0x6f --no-comments -o existing.asm
python -m rpkbin.mcucc verify existing.py --target small_target.py --spill-pool data:0x60-0x6f --window 256
```

### Emit inline assembly

```python
# inline.py
from rpkbin.mcucc.lang import u8, asm

def inline(a: u8) -> u8:
    asm("inc {0}", ins=((a, "a"),), outs=((a, "a"),),
        clobbers=("b",), nbytes=1, ref=lambda value: (value + 1) & 0xff)
    return a
```

```sh
python -m rpkbin.mcucc compile inline.py --target small_target.py --spill-pool data:0x60-0x6f --no-comments --report
python -m rpkbin.mcucc verify inline.py --target small_target.py --spill-pool data:0x60-0x6f --window 256
```

## Target Declarations and Flag Semantics

Define hardware in a Python file exporting `TARGET = Target(...)` (and optional `SIM = SimBinding(...)` and `STUBS = {...}`):

- Storage and structure: `Space(name, addr_bits, kind)` (`"program"` or `"data"`), `Reg(name, bits)` (`cell_bits=8`), `Alias(name, parts)` (MSB-to-LSB register grouping for 16-bit views), `Cls(name, members)`, `Flag(name)`, and `Abi(arg_locs, ret_locs, callee_saved)` (`MemLoc(space, addr, n=1)` for fixed single-cell ABI slots).
- Operands and forms: `R(name, cls)`, `I(name, bits, signed=False, values=None)`, `M(name, space, bits=None)`, `P(name, cls, space)`, `T(name, rel_bits=None, abs_bits=None, base="end")`, and `Form(id, op, width, operands, bind, bytes, asm, flags_read=None, flags_write=None, clobbers=(), encode=None)`.

| `flags_write` word | Value `1` condition / meaning | Allowed `Form.op` and compiler rules |
|---|---|---|
| `zero` | Truncated operation result equals `0`. | Valid on value-producing ops and `cmp`. |
| `carry` | Unsigned `a + b >= 2**width`. | Used on `add`; not paired with `cmp`/`br`. |
| `borrow` | Unsigned `a < b`. | `sub` or `cmp`; freely paired with `br`. |
| `nborrow` | Unsigned `a >= b`. | `sub` or `cmp`; freely paired with `br`. |
| `borrow_msb0` | Unsigned `a < b` only when `max(a, b) < 2**(width - 1)`. | `sub` or `cmp` only (`TargetError` otherwise). Used only when V10 proves both MSBs are `0` (constants `< 2**(width-1)`, `zext`, `shr >= 1`, `&` masks, or `mov` chains); otherwise widens 8-bit `cmp` to 16 bits if supported or raises `UnsupportedError`. |
| `nborrow_msb0` | Unsigned `a >= b` only when `max(a, b) < 2**(width - 1)`. | Same `sub`/`cmp` restriction, V10 proof, and `check-forms` MSB=0 sampling as `borrow_msb0`. |
| `shift_out` | Last bit shifted out (`1 <= shift <= width`). | Used on `shl` / `shr`. |
| `undef` | Flag may be overwritten with an unspecified value. | Declares flag clobbering so the compiler avoids relying on stale flags. |

## Tips and Limitations

- **Source subset limits**: Supports `u8`, `u16`, `Ptr("space")`, single-byte `mem.space[addr]` reads/writes, constant integer shifts, six unsigned comparisons (condition-only), `if`/`elif`/`else`, `while`, `for ... in range(...)` (step 1), `break`/`continue`, `@fn`, `@extern`, and `asm(...)`. Runtime `*`, `/`, `%`, `**`, variable shifts, negative/signed types, floats, containers, recursion, interrupts, and multi-cell `MemLoc` ABI arguments or returns are rejected.
- **`run_differential` behavior**: `stubs` defaults to `None` (`{}`), while passing `sim=None` raises `AttributeError` when reading `sim.halt_cells` unless every function is unsupported or `unverified: asm`. Reference `TimeoutError` events are tracked per function: any function exceeding a 10% timeout ratio (`fn_timeouts * 10 > fn_attempts`) appends `(name, "reference timeout ratio", fn_timeouts, fn_attempts)` to `report.failures`, and `report.timeouts` counts total timeouts. With a dict `window`, listed spaces require width `>= 512` and reject out-of-window source accesses; unlisted spaces allow constant-address reads/writes (initialized to `0`) and are excluded from memory diffing.
- **`run_reference` path handling**: `run_reference` loads a file only when given a `pathlib.Path` or a single-line `str` ending with `".py"`. Passing a string path such as `"rpkbin/mcucc/examples/program.py.txt"` causes it to parse the path string as Python source and fail with `SourceError`; wrap non-`.py` paths in `Path(...)` or pass source text directly. Missing or unreadable files raise `SourceError`. Standalone `run_reference` has no `spill_pool` bounds and does not check pool overlap.
- **Compatibility**: Offline, standard-library core compiler targeting Linux RHEL 8.8 and Python >= 3.11 (`pyproject.toml`, `feature_version=(3, 11)`; Python 3.12+ syntax such as `def f[T](...)` is rejected by the frontend). All examples in this page were verified with `venv\Scripts\python.exe` on Windows.

## See Also

- [Source Language Reference](../../../rpkbin/mcucc/doc/language.md)
- [Target Description Reference](../../../rpkbin/mcucc/doc/target.md)
- [Guarantees and Assumptions](../../../rpkbin/mcucc/doc/guarantees.md)
- [Error Catalog](../../../rpkbin/mcucc/doc/errors.md)
- [Bundled Small Target Example](../../../rpkbin/mcucc/examples/README.md)
- [AI Integration Guide](../../../rpkbin/mcucc/AI-GUIDE.md)
- [mcucc Changelog](../../../rpkbin/mcucc/CHANGELOG.md)
- [isasim Quickstart](../isasim/README.md)
- [Documentation Index](../README.md)
