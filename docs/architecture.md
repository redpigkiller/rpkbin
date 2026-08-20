# rpkbin architecture

`rpkbin` is a collection of focused Python modules rather than one mandatory runtime. A caller selects the module that owns the relevant data model, then composes modules when a workflow needs more than one concern.

## Top-level flow

```text
Python / Excel / HIR input
        │
        ├─ MapBV        register and bit-slice relationships
        ├─ NumBV        fixed-point values and arithmetic
        ├─ Excel       template matching and extracted records
        ├─ CFG         blocks, edges, calls, and deterministic layouts
        ├─ StageTracker workflow stages, issues, and summaries
        │
        ├─ Wave CLI/TUI
        │       └─ Session ──> Job Manager ──> CmdJob / FuncJob / PtyJob
        │
        └─ Codegen
                HIR ─> HIR validation ─> LIR lowering ─> rewrite
                    ─> optional register allocation ─> Target selection ─> pseudo ASM
```

## Components

### Core modeling modules

- `rpkbin.mapbv` owns named bit-vector values, slices, concatenated views, and symbolic evaluation. It keeps mapped views synchronized; it does not model fixed-point scaling.
- `rpkbin.numbv` owns fixed-point formats, quantization, overflow/rounding, scalar/array values, and optional JAX execution. The backend is process-global and must be selected before objects are created.
- `rpkbin.excel_extractor` normalizes workbook cells and matches template-shaped rows/groups/blocks. The template describes expected structure instead of hard-coded coordinates.
- `rpkbin.cfg` owns target-neutral blocks, edges, calls, validation, analysis, diffs, and FSM/MCU layout recipes. It does not parse assembly, allocate registers, or define an ISA.
- `rpkbin.utils.stage_tracker` records multi-stage progress, issues, timing, and summaries for a caller-owned workflow.
- `rpkbin.debug` defines target-neutral debugger records and structural protocols. Its
  stdio DAP server owns protocol framing and session lifecycle, while adapters retain
  execution semantics, source mapping, register schemas, and condition evaluation.

### Job Manager and Wave

`JobManager` is the execution layer. It schedules `CmdJob` shell commands and `FuncJob` Python callables with bounded workers, priorities, retries, cancellation, callbacks, and resource capacities. A manager may be used directly in Python and has an explicit lifecycle (`start`, `add`, `wait`, `stop`), with context-manager support for cleanup.

Wave adds a user-facing session layer above that manager. A wave file configures the singleton `session` and registers jobs; `rpk-wave run` loads the file, applies CLI overrides, starts the manager, then chooses the full-screen TUI or a headless wait/REPL path. Job execution remains local to the process; Wave does not provide a remote scheduler.

### Codegen

The production-facing contract is target-neutral:

1. A frontend constructs `HFunction`, `HFragment`, or `HModule`.
2. HIR validation reports source-level type and contract errors.
3. Functions/fragments lower to LIR and receive structural validation.
4. Optional rewrite patterns run, followed by another LIR validation pass.
5. A supplied `RegisterModel` enables physical-register validation and allocation; without one, allocation is skipped for functions and unresolved fragment locals fail closed.
6. An injected `Target` or `FragmentTarget` selects instructions and returns pseudo-ASM.

Errors are intentionally staged: malformed HIR raises `HIRValidationError`, unsupported constructs may raise `NotImplementedError`, invalid LIR raises `ValueError`, and register pressure can raise an allocation error. The package does not own a DSL parser, MCU ABI, real ISA, assembler, linker, or binary encoder.

## Load-bearing assumptions

- Optional dependencies are not imported as part of the core install; users must install the matching extra before importing the optional feature.
- Wave files are executable Python. Treat them as trusted local code and keep their side effects explicit.
- Job resource names and capacities are scheduler contracts; a job requesting more than the configured capacity cannot be admitted.
- Codegen targets and register models are injected protocols. Hardware semantics, ABI rules, and final encoding remain in the target/frontend package.
- Debug targets are injected structural protocols. Address spaces declare their unit
  width, and no common emulator state or inheritance hierarchy is required.
- Codegen register allocation is not a general spilling system. Production spill/reload is deliberately disabled until a target-level contract exists.

## Design decisions

### Wave remains a layer above Job Manager

**Context:** direct job scheduling and user-facing observability have different responsibilities.

**Decision:** keep `JobManager` focused on scheduling/execution and put wave files, parsers, hooks, TUI, and headless controls in `rpkbin.wave`.

**Consequence:** Python callers can use the small manager without installing the Wave extra, while Wave can evolve its UI without changing the core scheduler contract.

### Codegen delegates target policy

**Context:** a reusable backend cannot safely guess every MCU's ABI, register aliases, flags, or instruction encoding.

**Decision:** inject `Target`, `FragmentTarget`, and `RegisterModel` implementations.

**Consequence:** the package can validate and lower shared IR, but a frontend/target package must supply hardware-specific policy and final emission.

### Templates describe Excel structure

**Context:** fixed cell coordinates break when rows, columns, or merged cells move.

**Decision:** Excel Extractor matches declared row/group/block shapes against a normalized workbook grid.

**Consequence:** templates are more portable across layout variations, while matching behavior depends on the template conditions being specific enough.
