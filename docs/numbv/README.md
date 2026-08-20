# NumBV

`rpkbin.numbv` provides bit-true fixed-point arithmetic for scalar and array-like values.

```python
from rpkbin.numbv import Format, scalar

fmt = Format(8, 4)
print((scalar(1.25, fmt=fmt) + scalar(0.5, fmt=fmt)).val)  # 1.75
```

Use the function-level API (`add`, `mul`, `sum`, `dot`, `mac`) when each pipeline stage needs an explicit output format. JAX is optional and the backend is selected process-wide.

- Full API and semantics: [NumBV reference](numbv.md)
- 中文入口：[README_zh.md](README_zh.md)
- Focused tests: `python -m pytest tests/numbv -q`
