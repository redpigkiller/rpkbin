# CFG

`rpkbin.cfg` provides a small control-flow graph model with dominance, post-dominance, reachability cleanup, and optional DOT export.

Install the optional analysis dependencies when needed:

```bash
python -m pip install -e ".[cfg,dot]"
```

Use CFG when an algorithm needs explicit blocks and edges. It is a graph utility, not a parser or a complete compiler frontend.

- Full API and examples: [cfg reference](cfg.md)
- 中文入口：[README_zh.md](README_zh.md)
- Focused tests: `python -m pytest tests/cfg -q`
