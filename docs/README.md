# rpkbin Documentation / 文件中心

Welcome to the documentation for **rpkbin**. Please select your preferred language:

歡迎來到 **rpkbin** 文件中心，請選擇您的語言：

- **[繁體中文 (Traditional Chinese)](zh/README.md)**
  - [Wave 工作排程與監控](zh/wave/README.md)
  - [MapBV 硬體位元欄位工具](zh/mapbv/README.md)
  - [TablePack 表格壓縮編碼工具](zh/tablepack/README.md)
  - [rpk-calc 工程與定點數計算](zh/calc/README.md)
- **[English](en/README.md)**
  - [Wave Command Scheduler & Monitor](en/wave/README.md)
  - [MapBV Bitfield Library](en/mapbv/README.md)
  - [TablePack Table Codec](en/tablepack/README.md)
  - [rpk-calc Engineering and FXP Calculator](en/calc/README.md)

---

## Tools Overview / 工具概覽

| Tool / 工具 | Purpose / 用途 | Interfaces / 介面 |
| :--- | :--- | :--- |
| **Wave** | Python-defined local command scheduler, live stdout/stderr parsing, multi-check verdicts, and bounded history. / 以 Python 定義本機命令排程，即時解析輸出、多重 check 驗收與有界歷史管理。 | Python API, Web GUI, Headless CLI (`rpk-wave`) |
| **MapBV** | Lightweight bitvector library for register slicing, live bidirectional views, bus concatenation, and symbolic evaluation. / 輕量級硬體暫存器位元欄位工具，支援切片雙向連動、bus 串接與符號求值。 | Python API (`rpkbin.mapbv`) |
| **TablePack** | Schema-driven codec that packs a table of rows into compact bytes and restores it exactly; pure library, never reads files. / 以 schema 驅動的表格編解碼工具，把資料列打包成緊湊位元組並完整還原；純函式庫，不讀取檔案。 | Python API (`rpkbin.tablepack`) |
| **rpk-calc** | Interactive Python workspace for NumPy and fixed-point calculations, with GUI inspection and editing. / 互動式 Python 數值與定點數工作區，提供 GUI 檢視與編輯。 | GUI and headless CLI (`rpk-calc`) |
