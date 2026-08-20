# Wave

Wave is the package’s observable workflow layer: CLI commands, parser-driven jobs, hooks, PTY support, logs, performance summaries, and TUI/headless execution.

```bash
python -m pip install -e ".[wave]"
rpk-wave init hello --profile parser
rpk-wave run hello.wave.py --no-tui --perf
```

Use `--no-tui` in CI or batch scripts. The long reference is intentionally kept separate from this short entry point.

- Full command and API reference: [Wave reference](wave.md)
- 中文入口：[README_zh.md](README_zh.md)
- CLI overview: [CLI reference](../cli-reference.md)
- Focused tests: `python -m pytest tests/wave -q`
