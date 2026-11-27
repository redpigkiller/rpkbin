# `rpkbin.app.calc` Reference

Engineering & APyTypes fixed-point (FXP) calculator, headless `--eval` evaluator, and desktop PySide6/IPython Studio (`rpk-calc`). Exempt from RHEL headless constraints when using GUI; `--eval` and `rpkbin.app.calc.core` work headlessly everywhere.

## Public API

- `rpkbin.app.calc`:
  - `fxp(x, fmt=None, *, bits=None, wl=None, fwl=None, int_bits=None, frac_bits=None, signed=True, quant=None, overflow=None)`
  - `fxpi(x, fmt=None, *, bits=None, wl=None, fwl=None, int_bits=None, frac_bits=None, signed=True)`
  - `launch(...)`, `main(...)`
- `rpkbin.app.calc.core`:
  - `CalcConfig(...)`, `FormatEngine()`, `FxpSpec(signed=True, wl=16, fwl=15)`
  - `make_fxp(value_or_fmt=0.0, fmt=None, *, quant='RND', overflow='SAT') -> RpkFixed | RpkFixedArray`
  - `make_fxpi(raw_int, fmt, *, overflow='SAT') -> RpkFixed | RpkFixedArray`
  - `WorkspaceSession(config=None, *, seed_demo_vars=True, namespace=None)` (headless Python/FXP session with `.execute(...)`, `.get_inspector_snapshot(...)`, `.list_workspace_rows()`).

## CLI (`rpk-calc` / `python -m rpkbin.app.calc`)

```bash
rpk-calc --eval "fxp(1.25, 'Q4.4')"
rpk-calc --eval "hex(fxp(3.5, 'Q4.4'))"
rpk-calc --simple            # Launch Simple fixed-point calculator GUI
rpk-calc --python            # Launch IPython Studio workspace GUI (alias: --studio)
rpk-calc -c custom.json --mode simple
rpk-calc --version
```

## Gotchas

- Install extras: `.[fixed]` provides `apytypes` (`ap` / APyTypes interop); `.[calc]` installs `PySide6`, `qtconsole`, `ipykernel`.
- `--eval ""` (empty expression) returns exit code 2 on stderr instead of launching Qt GUI; `--eval` errors print to stderr with non-zero exit code.
- Pass `CalcConfig(startup_script="")` to `WorkspaceSession(...)` in automated scripts so it never reads or creates `~/.rpkbin/startup.py`.
- In Simple mode, `DEC` edits the raw FXP storage integer directly without scaling by `2**-fwl`, and shift counts are integers (`Q4.4 << 1`), never FXP-scaled.

## Recipes

### 1. Scripted Fixed-Point Creation & Bit Inspection via `rpkbin.app.calc`

```python
from rpkbin.app.calc import fxp, fxpi
from rpkbin.app.calc.core import CalcConfig, WorkspaceSession

a = fxp(1.875, "Q4.4")
b = fxpi(0x1E, "Q4.4")  # raw 30 / 16 == 1.875
assert float(a) == float(b) == 1.875

sess = WorkspaceSession(CalcConfig(startup_script=""), seed_demo_vars=False)
res = sess.execute("x = fxp(1.25, 'Q4.4')\nx")
assert not res.error_text
snap = sess.get_inspector_snapshot("x")
assert snap.is_fxp and snap.format_str == "Q4.4"
```

### 2. Headless `--eval` CLI Verification

```bash
python -m rpkbin.app.calc --eval "fxp(1.25, 'Q4.4')"
```
