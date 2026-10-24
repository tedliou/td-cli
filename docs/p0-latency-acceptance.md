# P0 命令延遲驗收

日期：2026-10-24。Spec: https://github.com/tedliou/td-cli/issues/185 。決策依據：`docs/adr/0001-phased-command-latency.md`。

## 變更

- Daemon 健康且 ready 時，`td` 的線上命令不再匯入 Daemon 伺服器套件（uvicorn、FastAPI、python-socketio）。health 探測改由 `DaemonClient` 在共用連線上送出，結果有疑慮時才匯入啟動流程，錯誤碼與「不取代不健康 runtime」的規則不變。
- 一個命令內的 HTTP 請求共用一條 keep-alive 連線，命令結束時關閉。
- `td-daemon`、`td-agent` 改用 onedir，runtime 目錄分別是 `td-daemon-runtime\`、`td-agent-runtime\`，與 `td-runtime\` 並存在同一個版本目錄。release zip 名稱不變。
- 新增 `tools/latency_bench.py`，以子行程量整個 `td` 行程的 wall time，輸出 p50／p95。

## 量測方法

- 機器：Windows 11 Pro，Python 3.11，Daemon 已在執行，沒有連線中的 TouchDesigner Instance。
- 0.11.0：`%LOCALAPPDATA%\Programs\touchdesigner-cli\current` 的已安裝版。
- 本分支：`pyinstaller` 建出三個 bundle，複製到同一個目錄，模擬安裝後的版面。
- 暖機數據：`uv run python tools/latency_bench.py --td <td.exe> --runs 20`，每個命令先跑 1 次不計。兩邊都連到同一個 Daemon。
- 冷啟動與 `--version`：PowerShell `Measure-Command`，每項 3 次。冷啟動每次先 `td-daemon stop`，再量一次會自動啟動 Daemon 的 `td --json instances list`。

## 結果

暖機，20 次：

| 命令 | 0.11.0 p50／p95 | 本分支 p50／p95 |
| --- | --- | --- |
| `td --version` | 532／539 ms | 403／407 ms |
| `td --json instances list` | 1,476／1,489 ms | 469／471 ms |

目標「暖機後 `td --json instances list` 低於 700 ms」達成。

執行檔啟動，3 次：

| 命令 | 0.11.0（onefile） | 本分支（onedir） |
| --- | --- | --- |
| `td-daemon --version` | 1,839–2,083 ms | 672–681 ms |
| `td-agent --version` | 1,196–1,359 ms | 175–218 ms |

冷啟動（含自動啟動 Daemon），3 次：

| 版本 | 時間 |
| --- | --- |
| 0.11.0 | 5,113–5,150 ms |
| 本分支 | 4,502–5,590 ms（第一次 5,590 ms 含新目錄的首次載入，之後 4,502–4,515 ms） |

## 發現

- 冷啟動只少約 0.6 s。剩下的大部分不在 Daemon 本身：`td-daemon.exe` 啟動只要約 0.7 s，但自動啟動經由 Windows PowerShell 5.1 呼叫 WMI `Win32_Process.Create`，加上 Daemon 匯入伺服器套件後才回應 health。這條路徑屬於 P1 以後的範圍，P0 不改。
- 暖機後的 469 ms 主要是 `td.exe` 本身的匯入（`--version` 已要 403 ms），留給 P1 的延後匯入處理。
- 本次沒有量 TouchDesigner 往返。P0 沒有改 Daemon 到 Agent 的路徑。
