# `rpkbin.isasim` Assembler and Source Debugging

[繁體中文](../../zh/isasim/assembler.md) · [Documentation Index](../README.md) · [Quickstart](README.md) · [Scenarios](scenarios.md) · [API Reference](reference.md) · [Architecture](architecture.md)

The two-pass assembler translates a main assembly file and its `.include` tree against an `MCU` declaration into a validated `.isa.json` bundle without executing instruction callbacks.

## Syntax

```python
op = Operand(signed=False, relative=None, names=None, range=None)
image = assemble(
    mcu,
    source,
    *,
    filename="<string>",
    include_paths=(),
    defines=None,
    endian=None,
    case_sensitive_names=True,
)
image = Assembly.load(mcu, path)
sim.load_assembly(image)
rows = sim.source_context(where=None, *, context=2)
```

```bash
rpk-isasim assemble MCU.py SOURCE.asm [-o OUTPUT] [--listing LISTING] [-I DIR]... [-D NAME[=VAL]]... [--endian {little,big}] [--case-insensitive]
```

## Description

- `Operand(...)` attaches signedness, PC-relative addressing (`"pc"` or `"next_pc"`), named enum values, or inclusive integer ranges to an instruction pattern field.
- `assemble(mcu, source, ...)` runs preprocessing and two-pass layout/encoding to return an `Assembly` object.
- `Assembly.load(mcu, path)` verifies the stored SHA-256 hashes, ISA fingerprint, and rebuilt cells before returning an `Assembly`.
- `Simulator.load_assembly(image)` atomically replaces program memory and binds source lines for `source_context()`, `source_line()`, and `filename:line` / `:line` breakpoints.
- `rpk-isasim assemble` writes `<source_parent>/<source_stem>.isa.json` unless `-o` / `--output` specifies another path.

## Declare Instruction Operands

`MCU.instr()` uses its `syntax` template as the assembly grammar and accepts `operands` to configure each encoded pattern letter:

```text
MCU.instr(pattern, *, syntax, cycles, name=None, operands=None, overrides=None, kind=None) -> decorator
Operand(signed: bool = False, relative: str | None = None, names: Mapping[str, int] | None = None, range: tuple[int, int] | None = None)
```

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `signed` | `bool` | `False` | Encodes field values in two's complement instead of unsigned raw integers. |
| `relative` | `str \| None` | `None` | Set `"pc"` or `"next_pc"` to take an absolute target cell address in assembly and encode the signed displacement from that base without PC wrap-around. |
| `names` | `Mapping[str, int] \| None` | `None` | Maps identifier tokens to unique integers fitting the field width; disassembly emits the declared name. |
| `range` | `tuple[int, int] \| None` | `None` | Inclusive `(lo, hi)` filter checked during layout (cannot depend on labels). |

Every pattern field placeholder must appear exactly once in `syntax`. Missing, repeated, adjacent, or whitespace-only separated placeholders are rejected; use explicit delimiters such as `OP {a}, {b}` or `OP r{a}, r{b}`. Don't-care (`-`) pattern bits encode as zero. Neither `names` nor `range` combines with `relative`. When multiple instruction variants share a mnemonic, `range` filters viable templates and assembly requires exactly one match. `disassemble()` converts signed fields back to signed integers and relative fields back to absolute target addresses, while callbacks and trace events still receive or display raw decoded field bits.

## Source Syntax

The assembler runs in two passes. Mnemonics and directives are case-insensitive, while labels, constants, and named operands are case-sensitive by default (`case_sensitive_names=False` folds case for lookup while keeping declared spelling). Semicolons start line comments. A label may stand alone or precede a statement on the same line, and horizontal whitespace (including tabs) may separate a directive from its arguments.

```asm
limit = (2 + 3) * 2
.org 0x04
entry: PLUS limit
    PLUS 1 ; add one more
    END
```

`.org` only moves the emitted cell address; it does not alter the MCU's PC reset value or set an entry point.

| Statement | Meaning |
| :--- | :--- |
| `label:` | Bind a label name to the current output cell address. |
| `NAME = expr`, `NAME EQU expr`, `.equ NAME, expr` | Define an integer constant (forward constant references allowed unless cyclic). |
| `.org expr` | Set the current output cell address (must resolve during layout; cannot depend on a later label). |
| `.cell expr[, expr ...]` | Emit one or more unsigned raw program cells. |
| `.db v1, v2, ...` | Emit 8-bit cells (only when `cell_bits == 8`); accepts signed/unsigned `-128..255` (two's complement). |
| `.dw v1, v2, ...` | Emit 16-bit values (`-32768..65535`); writes one 16-bit cell or two 8-bit cells using `endian`. |
| `.space n[, fill]`, `.ds n[, fill]` | Emit `n >= 0` cells initialized to `fill` (default `0`, unsigned cell range checked even when `n=0`). `n` cannot depend on labels. |
| `MNEMONIC operand[, ...]` | Encode an instruction declared via `MCU.instr()`. |

Expressions support decimal, `0x`, `0b`, and `0o` integers, labels and constants, parentheses, unary `+`, `-`, `~`, and binary `+`, `-`, `*`, `//`, `%`, `<<`, `>>`, `&`, `|`, `^`. Expressions are parsed by the assembler AST evaluator, never Python `eval()`. Hard limits: 4096 characters and 128 AST nodes per expression, 4096 bits per integer, shift counts `0..4096`, and 64 nested constant references. `.cell` data lines may carry labels, but are not executable breakpoint locations.

## Include Source and Select a Branch

`.include "path"` searches the including file's directory before `include_paths` (virtual sources such as `"<string>"` search only `include_paths`). Keys under the main file's directory are stored relative to it; external paths are stored as POSIX absolute paths. Include cycles and depths over 16 raise `AssemblyError`. Input files accept a UTF-8 BOM and normalize line endings to LF; semicolons inside quotes stay part of the filename.

`.if`, `.elseif`, `.else`, `.endif`, `.ifdef`, and `.ifndef` select active lines within the same file. Inactive branches do not evaluate expressions or open `.include` targets. `.if` and `.elseif` require constants whose dependencies are already visible and label-free; `.ifdef` and `.ifndef` test only whether a constant name is defined. Condition precedence runs from unary `+`, `-`, `~`, `not`, `!` through arithmetic/bitwise operators, left-to-right comparisons (`==`, `!=`, `<`, `<=`, `>`, `>=`), `and`, and `or`, returning `0` or `1` (evaluating both sides of `and`/`or`).

### Example 1: Include, Conditional Assembly, and Portable Bundle

Assemble an ASM program that includes a header file and uses `-D`/`defines` and `case_sensitive_names=False`, save the `.isa.json` bundle, delete the included file, and run from the saved bundle.

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from rpkbin.isasim import Assembly, MCU, Simulator, assemble, helpers

mcu = MCU("demo")
mcu.space("prog", cell_bits=5, size=16, access="ro", reset_state="uninitialized", init=0, is_program=True)
mcu.reg("pc", bits=5, reset=0, is_pc=True)
mcu.reg("value", bits=5, reset=0)
mcu.on_undefined_opcode("stop")
mcu.on_out_of_range("stop")
mcu.on_write_to_readonly("stop")
mcu.on_uninitialized_read("stop")

@mcu.instr("0 iiii", syntax="PLUS {i}", cycles=2)
def plus(st, i):
    st.reg["value"] = helpers.add_with_carry(st.reg["value"], i, 0, 5).result

@mcu.instr("10000", syntax="END", cycles=1)
def end(st):
    st.halt("done")

with TemporaryDirectory() as folder:
    root = Path(folder)
    included = root / "amount.inc"
    included.write_text("AMOUNT EQU 2\n", encoding="utf-8")
    source = """
.include "amount.inc"
.if (not OFF) == 1
PLUS amount
.endif
END
"""
    image = assemble(
        mcu,
        source,
        include_paths=[root],
        defines={"OFF": 0},
        case_sensitive_names=False,
    )
    bundle = root / "demo.isa.json"
    image.save(bundle)
    included.unlink()

    with Simulator(mcu) as sim:
        sim.load_assembly(Assembly.load(mcu, bundle))
        result = sim.run(max_steps=4)
        print(result.stop_reason, sim.read("value"))
```

Output:
```text
halted 2
```

## Bundle Format and Source Records

| Member | Type | Description |
| :--- | :--- | :--- |
| `Assembly.cells` | `dict[int, int]` | Independent copy of emitted address-to-cell pairs. |
| `Assembly.symbols` | `dict[str, int]` | Independent copy of label-to-address pairs. |
| `Assembly.constants` | `dict[str, int]` | Independent copy of resolved constants (including `defines`). |
| `Assembly.sources` | `dict[str, str]` | Independent copy of captured source texts keyed by source path. |
| `Assembly.main_key` | `str` | Read-only key of the main file inside `Assembly.sources`. |
| `Assembly.records()` | `tuple[SourceRecord, ...]` | Emitted `SourceRecord(addr, file, line, cells, is_instruction)` items in listing order. |
| `Assembly.listing()` | `str` | Formatted address, cell hex, `file:line`, and source line text. |
| `Assembly.save(path)` | `None` | Writes UTF-8 JSON (LF newlines, no BOM). |
| `Assembly.load(mcu, path)` | `Assembly` | Validates format, SHA-256 digests, ISA hash, and rebuilt payload. |

Programs with no preprocessing directives and default `defines`, `endian`, and `case_sensitive_names=True` emit `"isasim-assembly-1"`. Any preprocessing directive, non-empty `defines`, explicit `endian`, or `case_sensitive_names=False` emits `"isasim-assembly-2"` (storing `"sources"`, `"main"`, `"sources_sha256"`, `"options"`, `"includes"`, and `"constants"`). `Assembly.load()` checks internal consistency and ISA fingerprint compatibility (`load_mcu()` hashes the MCU file bytes; imported helper modules are outside that hash).

### Example 2: Inspect `SourceRecord` and Source Breakpoints

Load `playground/isasim_demo/demo_mcu.py`, assemble a short program, inspect `Assembly.records()`, and match a source breakpoint. Run from the repository root.

```python
from rpkbin.isasim import Simulator, assemble, load_mcu

mcu = load_mcu("playground/isasim_demo/demo_mcu.py")
image = assemble(mcu, "LDI r0, 1\nHLT\n", filename="main.asm")
with Simulator(mcu) as sim:
    sim.load_assembly(image)
    program = next(space for space in sim.describe().spaces if space.is_program)
    first = image.records()[0]
    assert program.name == "prog"
    assert sim.source_line(first.addr) == (image.main_key, first.line)

    bp_id = sim.add_breakpoint(f"{image.main_key}:{first.line}", temporary=True)
    print(repr(image))
    print(first)
    print("next_bp:", sim.next_breakpoint() == bp_id)
```

Output:
```text
Assembly('main.asm', cells=2, symbols=0)
SourceRecord(addr=0, file='main.asm', line=1, cells=(4097,), is_instruction=True)
next_bp: True
```

## Simulator Source Navigation

`Simulator.load_assembly(image)` validates the bundle before atomically updating program memory and source mappings. It leaves registers and cycle counters untouched, while `reset()` restores the attached image and retains its source mapping. Loading cells via `load_cells()`, `load_hex()`, `load_bin()`, or legacy symbols via `load_symbols()` clears the assembly source association.

When an assembly is attached, `add_breakpoint()`, `disassemble()`, and REPL `break` / `tbreak` / `until` / `source` accept `filename:line` or `:line` (on Windows, the final colon splits path and line; quote paths containing spaces in the REPL). Targets must contain an executable instruction (`.cell` lines are data). Lookups match the full source key first, then a unique basename. If repeated `.include` directives map one line to multiple addresses, `add_breakpoint()` returns one shared group ID, whereas scalar lookups (`disassemble("part.inc:1")`) reject ambiguous multi-address lines. If `poke()` or `restore()` mutates assembly-mapped cells, `source_context()` raises `DebugError` and registering new source breakpoints raises `TargetError`, while previously registered breakpoints keep their resolved addresses.

## CLI and REPL

```bash
rpk-isasim assemble tiny_mcu.py path/to/demo.asm
# writes path/to/demo.isa.json
rpk-isasim assemble tiny_mcu.py path/to/demo.asm -o build/demo.isa.json --listing build/demo.lst -I inc -D FLAG=1 --endian little --case-insensitive
rpk-isasim run tiny_mcu.py path/to/demo.asm --max-steps 100
rpk-isasim run tiny_mcu.py build/demo.isa.json --max-steps 100
rpk-isasim repl tiny_mcu.py path/to/demo.asm
```

Without `-o` / `--output`, `rpk-isasim assemble` writes `<source_parent>/<source_stem>.isa.json` (in the source file's directory). Output and `--listing` paths must differ from the MCU file, source file, any included file, and each other. `run` and `repl` detect `.asm` and `.isa.json` by extension (or `--format asm` / `--format image`), and reject `--space`, `--base`, `--order`, and `--symbols` for ASM/image input.

### Assembly CLI Options

These options apply to `assemble`, and to `run` / `repl` when the input format is `asm` (passing them with `hex`, `bin`, or `image` is a CLI error):

| Flag | Env Var | Default | Description |
| :--- | :--- | :--- | :--- |
| `-o OUTPUT`, `--output OUTPUT` | `-` | `<source_parent>/<source_stem>.isa.json` | Output `.isa.json` bundle path (`assemble` only). |
| `--listing LISTING` | `-` | `None` | Write formatted assembly listing to `LISTING` (`assemble` only). |
| `-I DIR`, `--include-dir DIR` | `-` | `[]` | Repeatable include search directory. |
| `-D NAME[=VAL]` | `-` | `[]` | Repeatable integer constant definition (`NAME` defaults to `1`; accepts integer literals such as `-0x2`, not expressions). |
| `--endian {little,big}` | `-` | `None` | Byte order for multi-byte `.dw` values on 8-bit program spaces. |
| `--case-insensitive` | `-` | `False` | Match labels, constants, and named operands case-insensitively. |

In the REPL, `source [where] [context]` prints `filename:line pc=0xADDR`, marks the active row with `> line: text`, prints context lines as `  line: text`, and appends `cells: 0x...`. `where` and stop displays print the same source view when mapped, `no source at 0xADDR` when unmapped, or `source unavailable: ...` if program memory no longer matches the assembly. See the [API Reference](reference.md#cli-and-repl) for all 25 REPL commands and 7 aliases.

## Tips and Limitations

- **Fixed instruction widths**: Each declaration has a fixed cell length; there are no macros, linker, or instruction relaxation.
- **Validation boundary**: Successful assembly confirms encoding against the declared MCU patterns; validating behavior against physical hardware still requires an accurate target model and independent reference tests.

## See Also

- [Quickstart](README.md)
- [API Reference](reference.md)
- [Scenarios](scenarios.md)
- [Architecture](architecture.md)
