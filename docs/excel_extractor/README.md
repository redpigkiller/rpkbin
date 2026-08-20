# Excel Extractor

`rpkbin.excel_extractor` extracts structured records from workbooks using explicit templates and sheet/column rules.

Install its optional dependencies:

```bash
python -m pip install -e ".[excel]"
```

Use it for repeatable workbook ingestion. It is not a general spreadsheet editor or a replacement for business-specific validation.

- Full API and template examples: [Excel Extractor reference](excel_extractor.md)
- 中文入口：[README_zh.md](README_zh.md)
- Focused tests: `python -m pytest tests/excel_extractor -q`
