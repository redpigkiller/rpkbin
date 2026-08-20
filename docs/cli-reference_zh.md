# rpk-wave CLI reference

`rpk-wave` 是 `pyproject.toml` 宣告的 console entry point。請先用 `python -m pip install -e ".[wave]"` 安裝。

## `rpk-wave init NAME`

在目前目錄建立 `NAME.wave.py`。

| Option | 預設 | 說明 |
| --- | --- | --- |
| `--profile minimal\|parser\|full\|pty` | `minimal` | 選擇 starter sections；`parser` 加入 stateless parser，`full` 加入 stateful parser 與 hooks，`pty` 加入 POSIX PTY 範例。 |
| `--force`、`-f` | 關閉 | 覆寫已存在的 generated file。 |

```bash
rpk-wave init hello --profile parser
```

目標檔案已存在且未指定 `--force` 時會回傳 exit code `1`。

## `rpk-wave run WAVE_FILE`

載入 Python wave file、啟動其中註冊的 jobs；當 stdin 與 stdout 都是 interactive 時開啟全螢幕 TUI，否則以 headless mode 執行。

| Option | 預設 | 說明 |
| --- | --- | --- |
| `--no-tui` | 關閉 | 強制 CI/headless execution。 |
| `--workers N` | wave file 設定 | 覆寫最大併發 workers；`N` 必須至少為 `1`。 |
| `--perf` | 關閉 | 結束時列印 parser、hook 與 TUI refresh 的簡短診斷。 |
| `--tui-profile lite\|normal\|heavy` | 未設定 | 調整 TUI refresh rate 與保留的 log 上限。 |

```bash
rpk-wave run hello.wave.py --no-tui --workers 4
```

Session 成功時回傳 `0`，batch 失敗時回傳 non-zero code。確切 code 由 session/job outcome 決定；請檢查 job status 與 logs 找出原因。

## `rpk-wave export-docs DEST`

將 package 內的 Wave 文件複製到 `DEST/wave/`。

| Option | 預設 | 說明 |
| --- | --- | --- |
| `--force`、`-f` | 關閉 | 允許複製到已有內容的 destination directory。 |

```bash
rpk-wave export-docs ./generated-docs
```

找不到 packaged docs，或 destination 已有內容但未指定 `--force` 時會回傳 exit code `1`。

## Headless controls

在 interactive headless terminal 中，REPL 支援 `status`、`show <job>`、`logs <job>`、`data <job>`、`events <job>`、`pause`、`resume`、`stop`、`cancel`、`skip`、`rerun`、`send-line`、`send-key`、`send-signal`、`watch` 與 `exit`。CI/non-interactive mode 會等待完成，不會開啟 REPL。
