# MapBV

`rpkbin.mapbv` 用來描述具名 register 與 bit view，包含 slices、constants、concatenation 與 packed layout 操作。

當 hardware-facing layout 需要可讀的 Python 表示時使用它；請保持 layout 定義明確，並用完整 reference 查閱 overlap 與 width 規則。

- 完整 API 與範例：[MapBV reference](mapbv_zh.md)
- English entry：[README.md](README.md)
- Focused tests：`python -m pytest tests/mapbv -q`
