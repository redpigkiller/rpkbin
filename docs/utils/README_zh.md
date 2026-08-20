# StageTracker

`rpkbin.utils.stage_tracker` 是用於 named stages 與可觀測 workflow state 的輕量 progress-reporting helper。

當 caller 需要一致的 stage updates、但不想採用 Wave TUI 時使用它；完整 reference 說明 lifecycle 與 callback 行為。

- 完整 API：[StageTracker reference](stage_tracker_zh.md)
- English entry：[README.md](README.md)
- Focused test：`python -m pytest tests/utils/test_stage_tracker.py -q`
