# NumBV

`rpkbin.numbv` 提供 bit-true fixed-point arithmetic，支援 scalar 與 array-like values。

```python
from rpkbin.numbv import Format, scalar

fmt = Format(8, 4)
print((scalar(1.25, fmt=fmt) + scalar(0.5, fmt=fmt)).val)  # 1.75
```

當每個 pipeline stage 都需要明確 output format 時，使用 function-level API（`add`、`mul`、`sum`、`dot`、`mac`）。JAX 是 optional，backend 以 process-wide 方式選定。

- 完整 API 與 semantics：[NumBV reference](numbv_zh.md)
- English entry：[README.md](README.md)
- Focused tests：`python -m pytest tests/numbv -q`
