# P0 命令延遲驗收

日期：2026-10-24。Spec: https://github.com/tedliou/td-cli/issues/185 。決策依據：`docs/adr/0001-phased-command-latency.md`。

## 用語

- onefile：PyInstaller 把整個 Python runtime 包進一個 exe，每次執行先解壓到暫存目錄。
- onedir：exe 旁邊放一個已解開的 runtime 目錄，執行時不用解壓。
- 持續連線（keep-alive）：同一條 HTTP 連線連續送多個請求，不必每次重新連線。
- p50／p95：把所有量測值排序後，第 50 與第 95 百分位的值。p50 是一般情況，p95 是偏慢的情況。

## 變更

- Daemon 健康且 ready 時，線上 `td` 命令不再匯入 Daemon 伺服器套件（uvicorn、FastAPI、python-socketio）。
- health 探測改由 `DaemonClient` 在共用連線上送出。結果有疑慮時才匯入啟動流程。錯誤碼不變，「不取代不健康的 runtime」的規則也不變。
- 一個命令內的 HTTP 請求共用一條持續連線，命令結束時關閉。
- `td-daemon`、`td-agent` 改用 onedir。runtime 目錄分別是 `td-daemon-runtime\`、`td-agent-runtime\`，與 `td-runtime\` 放在同一個版本目錄。release zip 名稱不變。
- 新增 `tools/latency_bench.py`。它以子行程反覆執行 `td`，量整個行程的時間，輸出 p50／p95。

## 量測環境

| 項目 | 值 |
| --- | --- |
| 作業系統 | Windows 11 Pro 10.0.26300 |
| Python | 3.11（`uv sync --locked`） |
| PyInstaller | 6.15.0 |
| 0.11.0 | 已安裝版，`%LOCALAPPDATA%\Programs\touchdesigner-cli\current\` |
| 本分支 | commit `4347f8d`，再以同一份原始碼重建 `td` |
| TouchDesigner | 沒有連線中的 Instance |

## 量測方法

本分支的建置與版面：

```powershell
$env:SOURCE_DATE_EPOCH = git show -s --format=%ct HEAD
'td','td-daemon','td-agent' | ForEach-Object { uv run pyinstaller --clean --noconfirm "packaging/$_.spec" }
'td','td-daemon','td-agent' | ForEach-Object { Copy-Item "dist\$_\*" $layout -Recurse -Force }
```

`$layout` 是一個空目錄，三個 bundle 合併後與安裝後的版本目錄相同。

暖機數據。兩邊都連到同一個已在執行的 Daemon。每個命令先跑 1 次不計，再量 20 次：

```powershell
uv run python tools/latency_bench.py --td "$env:LOCALAPPDATA\Programs\touchdesigner-cli\current\td.exe" --runs 20
uv run python tools/latency_bench.py --td "$layout\td.exe" --runs 20
```

執行檔啟動與冷啟動用 PowerShell `Measure-Command`，每項 3 次。冷啟動每次先 `td-daemon stop`，再量一次會自動啟動 Daemon 的 `td --json instances list`。兩個版本交替量。

## 結果

暖機，20 次：

| 命令 | 0.11.0 p50／p95 | 本分支 p50／p95 |
| --- | --- | --- |
| `td --version` | 532／539 ms | 403／407 ms |
| `td --json instances list` | 1,476／1,489 ms | 469／471 ms |

目標「暖機後 `td --json instances list` 低於 700 ms」達成。

`td --version` 也快了約 130 ms。本 spec 沒有改這條路徑，兩邊的 `td` 也都是 onedir。原因沒有查明，可能是建置環境不同。

執行檔啟動，3 次：

| 命令 | 0.11.0（onefile） | 本分支（onedir） |
| --- | --- | --- |
| `td-daemon --version` | 1,839–2,083 ms | 672–681 ms |
| `td-agent --version` | 1,196–1,359 ms | 175–218 ms |

冷啟動，含自動啟動 Daemon，3 次：

| 版本 | 第 1 次 | 第 2 次 | 第 3 次 |
| --- | --- | --- | --- |
| 0.11.0 | 5,150 ms | 5,116 ms | 5,113 ms |
| 本分支 | 5,590 ms | 4,515 ms | 4,502 ms |

本分支第 1 次比 0.11.0 慢 440 ms。那是新目錄第一次執行，之後兩次都快約 600 ms。只有 3 次樣本，不能排除第 1 次以外也有變慢的情況，但沒有看到持續變慢。

## 發現

- 冷啟動只少約 0.6 s，剩下的大多不在 Daemon 本身。`td-daemon.exe` 啟動只要約 0.7 s。自動啟動會先開 Windows PowerShell 5.1，再呼叫 WMI 的 `Win32_Process.Create` 建立行程。之後 Daemon 還要匯入伺服器套件，才能回應 health。這條路徑屬於 P1 以後，P0 不改。
- 暖機後的 469 ms 主要是 `td.exe` 本身的匯入，`--version` 就要 403 ms。這留給 P1 的延後匯入。
- 本次沒有量 TouchDesigner 往返，因為 P0 沒有改 Daemon 到 Agent 的路徑。
