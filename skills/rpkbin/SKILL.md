---
name: rpkbin
description: Use when writing Python scripts, CLI workflows, tests, or IC verification automation with rpkbin tools (mapbv, tablepack, jobcore, flow, wave, mcucc, isasim, isasim_gui, calc) without reading source code.
---

# `rpkbin` Toolkit Usage Guide

`rpkbin` (`0.3.0`) provides modular libraries (`rpkbin.*`) and local engineering apps (`rpkbin.app.*`) targeting **Python >= 3.11** offline environments (**Linux RHEL 8.8** compatible except Desktop `rpk-calc`).

## Module Router

| Task | Module / CLI | Reference |
|---|---|---|
| Hardware bit-field modeling, closed `bv[hi:lo]` slicing, `concat` buses | `rpkbin.mapbv` | [references/mapbv.md](references/mapbv.md) |
| Schema-driven columnar/row binary `bytes` serialization | `rpkbin.tablepack` | [references/tablepack.md](references/tablepack.md) |
| Multi-worker process execution, resource quotas, prompts, retries | `rpkbin.jobcore` | [references/jobcore.md](references/jobcore.md) |
| Streaming log parsers, analyzers, automations, and `ViewSpec` charts | `rpkbin.flow` | [references/flow.md](references/flow.md) |
| Job orchestration + `Flow` + Web GUI + SQLite `RunArchive` | `rpkbin.wave`, `rpk-wave` | [references/wave.md](references/wave.md) |
| Restricted-Python-to-MCU compiler & differential checker | `rpkbin.mcucc`, `rpk-mcucc` | [references/mcucc.md](references/mcucc.md) |
| Retargetable MCU simulator, 2-pass assembler, trace & snapshots | `rpkbin.isasim`, `rpk-isasim` | [references/isasim.md](references/isasim.md) |
| Browser visual debugger (reverse step, watch/poke, breakpoints) | `rpkbin.app.isasim_gui`, `rpk-isasim-gui` | [references/isasim_gui.md](references/isasim_gui.md) |
| Fixed-point (FXP) calculator, IPython workspace, headless `--eval` | `rpkbin.app.calc`, `rpk-calc` | [references/calc.md](references/calc.md) |

## Installation & Extras

```bash
python -m pip install -e .             # Core: mapbv, tablepack, jobcore, flow, wave, mcucc, isasim, isasim_gui
python -m pip install -e ".[table]"    # + brotli compression for tablepack
python -m pip install -e ".[fixed]"    # + apytypes for high-precision FXP in calc
python -m pip install -e ".[calc]"     # + PySide6, qtconsole, ipykernel for calc GUI
python -m pip install -e ".[all]"      # Full development environment
```

## CLI Entry Points

Every CLI accepts `-V` / `--version` and mirrors `python -m <module>`:

| CLI | Module Entry | Headless / Batch Usage |
|---|---|---|
| `rpk-wave` | `python -m rpkbin.wave` | `rpk-wave work.py --headless --no-save` |
| `rpk-mcucc` | `python -m rpkbin.mcucc` | `rpk-mcucc verify prog.py --target target.py` |
| `rpk-isasim` | `python -m rpkbin.isasim` | `rpk-isasim run mcu.py prog.asm --max-steps 1000` |
| `rpk-isasim-gui` | `python -m rpkbin.app.isasim_gui` | `rpk-isasim-gui mcu.py prog.asm --no-browser` |
| `rpk-calc` | `python -m rpkbin.app.calc` | `rpk-calc --eval "fxp(1.25, 'Q4.4')"` |

## Quick Cross-Module Rules

1. **Always close managers/simulators:** Use context managers (`with JobManager() as mgr:`, `with FlowSession(flow) as sess:`, `with WaveWorkspace() as ws:`, `with Simulator(mcu) as sim:`) to guarantee process-tree, archive WAL, and trace cleanup.
2. **Closed inclusive slices in `mapbv`:** `bv[hi:lo]` uses MSB-first closed intervals (`bv[7:4]` = 4 bits `[7..4]`), NOT Python half-open `slice`.
3. **`WaveWorkspace` vs `JobManager`:** Use `JobManager` for pure headless process scheduling; add `Flow` when parsing logs; use `WaveWorkspace` for multi-job Web GUI monitoring and SQLite `RunArchive`.
4. **RHEL 8.8 NFS staging in `wave`:** Default `stage_on_network_fs=True` writes active SQLite WAL under `--temp-dir` (`RPK_WAVE_TEMP_DIR`) and atomically moves finished `.sqlite3` archives into `--archive-dir`.
5. **Narrow `mcucc` subset:** Do not emit general Python libraries inside `@target`-compiled functions—check `lang` types (`u8`, `u16`, `mem`, `asm`, etc.) and target `borrow_msb0` / `nborrow_msb0` flag semantics in [references/mcucc.md](references/mcucc.md).
6. **Headless FXP evaluation:** Use `rpk-calc --eval "..."` or `rpkbin.app.calc.fxp(...)` directly from scripts without launching Qt (`calc` GUI requires desktop PySide6, exempt from RHEL headless constraints).

## Reference Docs in Repo

- Human docs (English): [docs/en/README.md](../../docs/en/README.md)
- Human docs (繁體中文): [docs/zh/README.md](../../docs/zh/README.md)
- Machine index: [llms.txt](../../llms.txt)
