# MapBV

`rpkbin.mapbv` models named register and bit views, including slices, constants, concatenation, and packed layout operations.

Use it when a hardware-facing layout needs a readable Python representation. Keep the layout definition explicit and use the full reference for overlap and width rules.

- Full API and examples: [MapBV reference](mapbv.md)
- 中文入口：[README_zh.md](README_zh.md)
- Focused tests: `python -m pytest tests/mapbv -q`
