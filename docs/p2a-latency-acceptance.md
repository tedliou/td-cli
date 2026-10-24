# P2a 原生 td 延遲驗收

日期：2026-10-25。Spec: https://github.com/tedliou/td-cli/issues/198 。決策依據：`docs/adr/0001-phased-command-latency.md`、`docs/adr/0003-native-td-client.md`。

## 用語

- 暖機：Daemon 已在執行，`td` 只付自己的啟動與一次本機 HTTP 往返。
- 冷啟動：Daemon 沒在執行，`td` 先自動啟動 Daemon、等它健康，再執行命令。
- p50／p95：把所有量測值排序後，第 50 與第 95 百分位的值。
- 暫存資料夾（scratch）：本 session 專用、量完即丟的資料夾，路徑中以 `<scratch>` 表示。
- data root：Daemon 存放狀態、權杖與記錄的使用者資料夾，預設是 `%LOCALAPPDATA%	ouchdesigner-cli`。
- WMI：Windows 管理介面；Daemon 透過它的 `Win32_Process.Create` 建立行程，讓 Daemon 不隨呼叫者的主控台結束。
- 一次性專案：只為這次量測建出、量完即丟的 TouchDesigner 專案，不是任何使用者作品。

## 變更

`td` 從凍結版 Python（PyInstaller 把 Python 程式與直譯器打包成可執行檔；onedir 是打包成一個資料夾，即 `td.exe` 加 `td-runtime\`）改成 Rust 寫的單一 `td.exe`（#199–#206）。輸出、exit code 與錯誤碼不變。Command 與計畫的驗證移到 Daemon。Daemon 不在時，`td` 呼叫同目錄的 `td-daemon.exe start`。

## 量測對象

- P1：`dist\td\`，P1 驗收時以 commit `1fca570` 建出，就是 `docs/evidence/v0120/cli-bench-0.12.0.json` 量的那一份。
- 本分支：`native\td\target\release\td.exe`，以 `cargo build --release --locked` 從本分支建出。

兩邊各複製到一個 scratch 資料夾，旁邊放同一份從本分支建出的 `td-daemon`（PyInstaller onedir）。所以兩邊的差異只來自 `td`。版本字串還是 0.11.0，改版號屬於發版。

## 暖機

### 方法

先停掉使用者的 0.11.0 Daemon。當時沒有 TouchDesigner 連線，所以沒有 Instance 受影響。改由冷啟動量測最後啟動的本分支 Daemon 服務，兩邊都連到它，沒有連線中的 Instance。每個命令先跑 1 次不計，再量 20 次：

```powershell
uv run python tools/latency_bench.py --td <scratch>\layout-p1\td.exe --runs 20
uv run python tools/latency_bench.py --td <scratch>\layout-native\td.exe --runs 20
```

原始輸出在 `docs/evidence/v0130/cli-bench-p1.json` 與 `cli-bench-native.json`。

### 結果

| 命令 | P1 p50／p95 | 本分支 p50／p95 |
| --- | --- | --- |
| `td --version` | 318／321 ms | 8.1／8.3 ms |
| `td --json instances list` | 326／333 ms | 12.3／12.6 ms |

目標「低於 50 ms」達成。P1 的數字和 P1 驗收（306／317 ms）相差約 10 ms，在不同次量測的正常範圍內。

## 冷啟動

### 方法

Daemon 經 WMI 的 `Win32_Process.Create` 啟動，不繼承呼叫者的環境變數，所以無法把 data root 換到 scratch。和 P0 一樣，用使用者的 data root。本分支沒有改 Daemon 的儲存結構。

每輪先用旁邊的 `td-daemon.exe stop` 停掉 Daemon，再用 PowerShell `Stopwatch` 量一次會自動啟動 Daemon 的 `td --json --timeout 30 instances list`。兩邊交替量。第 0 輪是新資料夾第一次執行，不計，之後量 5 輪。量完先停掉本分支的 Daemon，再啟動使用者安裝的 0.11.0 Daemon，並確認 9982 由它監聽。

原始輸出在 `docs/evidence/v0130/cold-start.json`。

### 結果

| | 最小 | 中位數 | 最大 |
| --- | --- | --- | --- |
| P1 | 4,447 ms | 4,454 ms | 4,485 ms |
| 本分支 | 4,411 ms | 4,418 ms | 4,422 ms |

本分支快約 36 ms，沒有變慢。`td` 自己的啟動少了約 300 ms，但原生 `td` 要先執行一次 `td-daemon.exe start`。那是 Python 程式，它再啟動 `td-daemon.exe serve`。P1 的 Python `td` 是在自己的行程裡直接啟動 `serve`。所以兩者大部分抵銷。這正是 ADR 0003 接受的代價。剩下的 4.4 秒大多是 WMI 建立行程，以及 Daemon 匯入伺服器套件。

### 量測時的一次失誤

第一次量測時，冷啟動用了 scratch 的 `LOCALAPPDATA`。P1 的 `td` 等 10 秒逾時，但它啟動的本分支 Daemon 隨後還是起來了，並因 WMI 不繼承環境變數，在使用者的 data root 上服務了約 1 分鐘。發現後用使用者的權杖把它停掉，重新啟動安裝版 Daemon，並確認 9982 的監聽者是 `current\td-daemon.exe`。本分支沒有改儲存結構，資料不受影響。上面的數據來自修正方法後的第二次量測。

## TouchDesigner 上的 `ops get`

`tools/locked_native_ops_get_acceptance.py` 在隔離的 `127.0.0.1:19982` 跑本分支的 Daemon transport（`tools/locked_daemon_harness.py`），開 P1 驗收用的一次性專案 `.codex/frame-timing/probe.toe`。它用 `TDCLI_TEST_ENDPOINT` 讓原生 `td` 連到這個 Daemon，同一個 `ops.get /project1` 各量 40 次：

- 經 in-process DaemonClient 的 Request（TouchDesigner 往返）。
- 整個 `td --json --instance <id> ops get /project1` 行程。

兩者的差就是 `td` 行程本身的成本。Agent 用 P1 驗收建出的 artifact（SHA-256 `f84b0a7a…`）。它的 `source_revision` 與本分支的 `agent/` 相同，因為 P2a 沒有改 Agent。

第一次嘗試時工作站已鎖定，TouchDesigner 無法完成啟動。解鎖後跑了兩次，原始輸出在 `docs/evidence/v0130/native-ops-get.json` 與 `native-ops-get-2.json`。

```powershell
uv run python -m tools.locked_native_ops_get_acceptance --work <scratch>\locked --project .codexrame-timing\probe.toe --artifact <scratch>rame-timing-src	d-agent.tox --evidence docs\evidence0130
ative-ops-get.json
```

40 次，單位 ms：

| | 第 1 次 p50／p95 | 第 2 次 p50／p95 |
| --- | --- | --- |
| Request（TouchDesigner 往返） | 62.0／63.0 | 62.0／63.0 |
| 整個 `td ops get` 行程 | 62.4／63.0 | 62.3／63.2 |
| 探針記錄的 frame 間隔中位數 | 31.3 | 31.2 |

- 整個行程與 Request 的 p50 差 −0.1 ms。`td` 自己約 12 ms 的啟動與連線，落在等下一個 frame 的時間裡，看不出額外成本。
- 兩次的 Request 都比 P1 量到的 33 ms 慢。原因是 TouchDesigner 這次每 31 ms 才呼叫一次 `onFrameStart`：timeline 照 60 fps 前進，但每隔一個 frame 才 cook 一次。P1 那次是每 16 ms 一次。兩次都是一個 Request 跨兩個 cook 出來的 frame（P1：2 × 16.7 ms；本次：2 × 31 ms）。in-process 的 Request 不經過 `td`，也同樣是 62 ms。所以變慢來自這台機器解鎖後 TouchDesigner 的 cook 頻率，不是本分支。為什麼只 cook 一半的 frame，這次沒有追。

## 沒有涵蓋的部分

- 沒有在 60 fps cook 的情況下重量 `ops get`；依 frame 數推算，整個行程約等於兩個 frame。
