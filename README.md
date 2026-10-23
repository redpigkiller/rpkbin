# rpkbin — Wave, MapBV, TablePack & rpk-calc

`rpkbin` is a lightweight Python toolkit designed for IC design and verification workflows. It provides four self-contained tools:

1. **Wave**: A Python-defined external command scheduler and monitoring engine with a local Web GUI and headless CLI runner. It manages job concurrency, resource quotas, streaming log observation, declarative `track` / `check` / `on` rules, immutable verification reports, and bounded execution history.
2. **MapBV**: A bitfield utility designed for hardware register modeling. It supports live bidirectional register slicing, bus concatenation (`concat`), read-only constant protection, and combinational logic expression trees with deferred and symbolic evaluation.
3. **TablePack**: A schema-driven codec that packs a table of rows into a compact `bytes` blob (typed columns, delta/repeat/nullable encoding, optional brotli) and restores it exactly. A pure library: it never reads files.
4. **rpk-calc**: An interactive Python workspace for engineering calculations, NumPy, and explicit APyTypes fixed-point (FXP) modeling, with GUI inspection and editing of values and arrays.

[繁體中文](README_zh.md) · [Documentation Index](docs/en/README.md) · [MIT License](LICENSE)

---

## Installation

Requires Python 3.11 or newer:

```bash
# Core package (Wave, MapBV and TablePack; standard library plus NumPy)
pip install -e .

# Optional: include brotli compression for TablePack (zlib is used without it)
pip install -e ".[table]"

# Optional: include fixed-point math modeling support (apytypes)
pip install -e ".[fixed]"

# Optional: include rpk-calc GUI and fixed-point support
pip install -e ".[calc]"
```

---

## Quickstart: Getting Your First Verifiable Results

### 1. Wave: Run Your First Batch

Create `work.py` to define your jobs. A work file only registers jobs and monitoring rules; it does not dispatch processes:

```python
import sys
from rpkbin.wave import Job, JobManager, Expect

# Concurrency limit: at most two jobs running simultaneously
mgr = JobManager(max_workers=2)

for name in ("sim_core", "sim_dma"):
    job = mgr.add(Job(name, [sys.executable, "-u", "-c", "print('TEST_PASS'); print('DONE')"]))
    # Verification criteria: must print TEST_PASS and exit with returncode 0
    job.check("pass_marker", match=r"^TEST_PASS$", expect=Expect.PRESENT)
```

**Run in terminal (Headless batch mode):**

```bash
rpk-wave work.py --headless
```

The terminal streams child logs in real time and concludes with an immutable summary:

```text
[Wave] Starting 2 job(s); workers=2
[child sim_core #1] TEST_PASS
[child sim_core #1] DONE
[child sim_dma #1] TEST_PASS
[child sim_dma #1] DONE

[Wave] Final summary
JOB                        STATE           RESULT       WARNINGS  EXIT
sim_core                   DONE            PASS         0         0
  checks: pass_marker: OK
sim_dma                    DONE            PASS         0         0
  checks: pass_marker: OK
```

**Run with the local Web GUI:**

```bash
rpk-wave work.py
```

The browser opens the monitoring dashboard. The work file is loaded into **Preview** mode; click the **Start** button in the top bar to explicitly begin dispatch.

---

### 2. MapBV: Create and Slice Register Fields

Define a 16-bit register variable, extract slices, and verify bidirectional live linkage:

```python
import rpkbin.mapbv as mbv

# Create a 16-bit register initialized to 0x1234
reg = mbv.var("CSR", 16, 0x1234)

# Slice [15:8] (high byte) and [3:0] (low nibble)
# Slices are live, bidirectional views linked to the parent register
high_byte = reg[15:8]
low_nibble = reg[3:0]

print(f"Original register: {reg:hex} (0x{reg.value:04X})")  # 0x1234
print(f"High byte slice:   {high_byte:hex}")                 # 0x12
print(f"Low nibble slice:  {low_nibble:hex}")                # 0x4

# Modifying the slice immediately updates the underlying register
high_byte.value = 0xAB
print(f"After modifying slice: {reg:hex}")                  # 0xAB34
```

Running this script immediately confirms the value update and linkage.

> [!IMPORTANT]
> **Comparison Semantics**: `reg == 0xAB34` tests Python object identity and raises `TypeError` to prevent accidental logic bugs. Always compare values with `reg.value == 0xAB34` or `reg.value_eq(0xAB34)`.

### 3. TablePack: Pack a Table into Bytes and Back

Describe the columns once, pack rows into a compact `bytes` blob, and restore them exactly:

```python
from rpkbin.tablepack import Schema, Uint, Enum, Str, pack, unpack

schema = Schema(
    Str("module", repeat=True),             # often equal to the previous row: repeats cost 1 bit
    Uint("addr", bits=16, delta=True),      # store the step from the previous row
    Enum("kind", ["RW", "RO", "W1C"]),      # a known set of values
    Str("note", nullable=True),             # may be None
)
rows = [
    {"module": "uart", "addr": 0x10, "kind": "RW", "note": None},
    {"module": "uart", "addr": 0x14, "kind": "RO", "note": "status"},
    {"module": "spi", "addr": 0x40, "kind": "W1C", "note": None},
]

blob = pack(rows, schema)
assert unpack(blob) == rows               # the schema is embedded in the blob by default
print(unpack(blob)[1])                    # {'module': 'uart', 'addr': 20, 'kind': 'RO', 'note': 'status'}
```

TablePack never reads files: pass it rows from any source and it returns `bytes`. `pack(rows)` without a schema infers one for you. For brotli compression install `pip install -e ".[table]"`; without it zlib is used.

---

## Where to Read Next

### Wave Guides & Reference
- **[Wave Quickstart](docs/en/wave/README.md)**: Completing your first batch, understanding Preview and Start.
- **[Python Scenarios](docs/en/wave/scenarios.md)**: Progressing from single jobs to concurrency, multi-check combinations, warnings, data tracking, interactive stdin/signals, resource quotas, retries, and bounded history.
- **[Web GUI Manual](docs/en/wave/gui.md)**: Dashboard layout, job selection, log filtering, batch controls, and Settings (Execution Limits, Memory Retention, Display Preferences).
- **[Python API Reference](docs/en/wave/reference/python.md)** & **[CLI Reference](docs/en/wave/reference/cli.md)**: Exact parameter types, defaults, and CLI exit codes.
- **[Architecture & Contracts](docs/en/wave/architecture.md)** and **[Troubleshooting Guide](docs/en/wave/troubleshooting.md)**.

### MapBV Guides & Reference
- **[MapBV Quickstart](docs/en/mapbv/README.md)**: Creating variables, constants, and formatting hexadecimal/binary representations.
- **[Practical Scenarios](docs/en/mapbv/scenarios.md)**: Slicing live views, hardware bus concatenation (`concat`), read-only constant bit enforcement, and combinational logic expressions with symbolic `eval()`.
- **[API & Semantics Reference](docs/en/mapbv/reference.md)**: Class methods, operator tables, and boundary conditions.

### TablePack Guides & Reference
- **[TablePack Quickstart](docs/en/tablepack/README.md)**: Packing rows without a schema, describing columns, and the first size comparison.
- **[Usage Scenarios](docs/en/tablepack/scenarios.md)**: Column types, `infer`/`explain`, schema sharing, column-oriented data, tuning, compressors, build-time verification, and errors.
- **[API & Semantics Reference](docs/en/tablepack/reference.md)**: Functions, column types and options, exceptions, and the binary format.

### rpk-calc Guide
- **[rpk-calc Quickstart](docs/en/calc/README.md)**: Start a clean IPython workspace, use Python and FXP together, inspect and edit arrays, and learn execution controls. `rpk-calc --eval` is a separate headless `WorkspaceSession` command.
- **[Architecture and Data Boundaries](docs/en/calc/architecture.md)**: Kernel ownership, JSON snapshots, matrix paging limits, and build compatibility.

## License

This project's code is licensed under the [MIT License](LICENSE). Embedded SVG
icons include Feather (MIT) and Lucide (ISC) designs; their notices are retained in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The distribution therefore declares
`MIT AND ISC`. Third-party
dependencies keep their own licenses; MIT does not relicense those packages.
In particular, Calc uses PySide6 / Qt for Python, whose community distribution
has [LGPL/GPL licensing terms](https://doc.qt.io/qtforpython-6/licenses.html).
The rpkbin wheel contains this project's code, not bundled copies of Qt or its
other dependencies. Redistributing a standalone application with those dependencies
requires reviewing their licenses and distribution requirements separately.
