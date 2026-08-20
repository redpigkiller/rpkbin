# StageTracker

`rpkbin.utils.stage_tracker` is a small progress-reporting helper for named stages and observable workflow state.

Use it when a caller needs consistent stage updates without adopting the Wave TUI. The full reference documents lifecycle and callback behavior.

- Full API: [StageTracker reference](stage_tracker.md)
- 中文入口：[README_zh.md](README_zh.md)
- Focused test: `python -m pytest tests/utils/test_stage_tracker.py -q`
