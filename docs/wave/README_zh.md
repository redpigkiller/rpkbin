# Wave

Wave 是 package 的可觀測 workflow layer，包含 CLI commands、parser-driven jobs、hooks、PTY support、logs、performance summaries，以及 TUI/headless execution。

```bash
python -m pip install -e ".[wave]"
rpk-wave init hello --profile parser
rpk-wave run hello.wave.py --no-tui --perf
```

CI 或 batch script 請使用 `--no-tui`。完整 reference 刻意與這個短入口分開。

- 完整 command 與 API：[Wave reference](wave_zh.md)
- English entry：[README.md](README.md)
- CLI overview：[CLI reference](../cli-reference_zh.md)
- Focused tests：`python -m pytest tests/wave -q`
