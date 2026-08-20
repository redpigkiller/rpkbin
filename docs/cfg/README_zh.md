# CFG

`rpkbin.cfg` 提供小型 control-flow graph model，包含 dominance、post-dominance、reachability cleanup，以及選配的 DOT export。

需要分析與 DOT 輸出時安裝選配依賴：

```bash
python -m pip install -e ".[cfg,dot]"
```

CFG 適合需要明確 blocks 與 edges 的演算法；它不是 parser，也不是完整 compiler frontend。

- 完整 API 與範例：[cfg reference](cfg.md)
- English entry：[README.md](README.md)
- Focused tests：`python -m pytest tests/cfg -q`
