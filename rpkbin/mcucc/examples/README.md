# 虛構目標範例

[五分鐘快速開始](../README.md) 有完整指令，依序載入目標、檢查指令形式、編譯並比對兩種執行結果。快速開始的命令從 `rpkbin/mcucc` 執行，目標和來源位於 `examples`。兩個輸入檔都可直接用原檔名傳給 CLI。

| 檔案 | 用途 |
| --- | --- |
| [target.py.txt](target.py.txt) | Python 撰寫的虛構目標與模擬器繫結，宣告為 `TARGET`、`SIM`。 |
| [program.py.txt](program.py.txt) | 受限 Python 來源，定義 `add2(a: u8, b: u8) -> u8`。 |
| [lint.tsv](lint.tsv) | `lint-target` 印到 stdout 的完整可用性矩陣。 |
| [program.asm](program.asm) | 編譯加上 `--no-comments` 時產生的組語。 |
| [report.json](report.json) | 編譯加上 `--report` 時印到 stderr 的 JSON。 |

`small` 有兩個 8 位元暫存器 `a`、`b`。ABI 依序把引數放進 `a`、`b`，並把結果放在 `a`。`add2` 的結果以 8 位元回繞，例如 `250 + 9` 會得到 `3`。它使用一個加法指令和一個返回指令，合計 2 bytes。報告顯示空的 `slots`，因此這個函式沒有使用私有槽。快速開始仍會傳入必填的 `--spill-pool`。

目標描述提供兩種加法形式，以及搬移、跳躍、呼叫、返回。模擬器的 `frames` 空間用來存放呼叫的返回位址。差異測試的 `--window 256` 涵蓋此目標整個 `data` 空間。要新增運算或移植到自己的目標，可參考 [Target 參考](../doc/target.md)。
