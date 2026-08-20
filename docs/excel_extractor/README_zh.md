# Excel Extractor

`rpkbin.excel_extractor` 依照明確的 template、sheet 與欄位規則，從 workbook 擷取結構化 records。

安裝選配依賴：

```bash
python -m pip install -e ".[excel]"
```

它適合可重複的 workbook ingestion；不是通用 spreadsheet editor，也不取代業務專用 validation。

- 完整 API 與 template 範例：[Excel Extractor reference](excel_extractor.md)
- English entry：[README.md](README.md)
- Focused tests：`python -m pytest tests/excel_extractor -q`
