# `rpkbin.mapbv` Reference

Unsigned hardware register and logic modeling with MSB-first closed-interval slicing (`[hi:lo]`) and deferred symbolic expression trees.

## Public API

- `var(name: str, width: int, value: int = 0) -> MapBV` — Named mutable bit-vector variable.
- `const(value: int, width: int) -> MapBV` — Read-only fixed constant.
- `concat(name: str, *parts: MapBV) -> MapBV` — Composite bus linked bidirectionally to `parts` (writes propagate through `concat` slices, rejecting overlapping parts and writes to `const` slices).
- `MapBV(parent: str | MapBV | None, high: int, low: int, value: int = 0)` — Low-level constructor (prefer `var`, `const`, `concat`, or slicing).
- `MapBVExpr(op: str, operands: list, width: int)` — Deferred bitwise/comparison expression node.

### Key Attributes & Methods (`MapBV` / `MapBVExpr`)

- Indexing / slicing: `bv[idx]` (1-bit slice) and `bv[hi:lo]` (closed inclusive range `[lo..hi]`, `0 <= lo <= hi < bv.width`). Assignment via `bv[hi:lo] = val` updates underlying bits and propagates to linked children/parents.
- Formatting: `bv.to_hex()`, `bv.to_bin()`, `f"{bv:hex}"`, `f"{bv:bin}"`, `int(bv)`, `str(bv)`.
- Properties: `.width`, `.value`, `.high`, `.low`, `.name`, `.kind`, `.is_const`, `.is_linked`.
- Value equality & evaluation: `bv.value_eq(other)` compares integer values cleanly; `expr.eval(ctx: dict[str, int]) -> int` evaluates deferred expression trees by matching **leaf `var()` names**.
- Supported expression operators: `&`, `|`, `^`, `~`, `<<`, `>>` (all preserve bit width and return deferred `MapBVExpr` trees).

## Gotchas

- `bv[hi:lo]` expects `hi >= lo` (hardware convention); Python negative indices or `[lo:hi]` reversed bounds raise `IndexError` / `ValueError`.
- Assigning a negative integer or value >= `1 << width` raises `ValueError`.
- Comparing `MapBV == n` or `MapBV != n` against an `int` intentionally raises `TypeError` so equality checks are never confused with identity or symbolic constraints—use `bv.value == n`, `int(bv) == n`, or `bv.value_eq(n)`.

## Recipes

### 1. Register Field Packing & `concat` Bus Update

```python
from rpkbin.mapbv import concat, const, var

hdr = const(0xA, 4)
mode = var("mode", 4, 0b0011)
addr = var("addr", 8, 0x2F)
word = concat("word", hdr, mode, addr)  # width = 16, MSB..LSB

assert int(word) == 0xA32F
word[11:8] = 0b1100  # updates `mode` live; leaves `hdr` read-only
assert int(mode) == 0b1100 and word.to_hex() == "0xAC2F"
```

### 2. Deferred Expression Tree & Leaf Evaluation

```python
from rpkbin.mapbv import var

enable = var("enable", 4, 0b0001)
mask = var("mask", 4, 0b0101)
gated = (mask & 0b0011) | (enable << 2)
assert gated.value == 0b0101
assert gated.eval({"enable": 0, "mask": 0b0010}) == 0b0010
```
