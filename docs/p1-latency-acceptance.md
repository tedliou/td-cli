# P1 命令延遲驗收

日期：2026-10-24。Spec: https://github.com/tedliou/td-cli/issues/191 。決策依據：`docs/adr/0001-phased-command-latency.md`、`docs/adr/0002-execute-in-socket-callback.md`。

## 用語

- frame：TouchDesigner 每秒更新 60 次，每次是一個 frame，間隔 16.67 ms。
- timeline：TouchDesigner 的播放時間軸；暫停時畫面時間停住，但程式照樣更新。
- SocketIO DAT：TouchDesigner 內建、Agent 用來連 Daemon 的 operator。callback 是它收到訊息時呼叫的函式。
- Execute DAT：TouchDesigner 在專案啟動、每個 frame 開始等時機呼叫其中函式的 operator。
- 長輪詢（Long Poll，見 GLOSSARY.md）：client 查詢 Request 時請 Daemon 先不回答，等 Request 結束或等待時間用完再回答。
- 一次性專案：只為這次量測建出、量完即丟的 TouchDesigner 專案，不是任何使用者作品。
- p50／p95：把所有量測值排序後，第 50 與第 95 百分位的值。

## 變更

- Agent 收到 `request_execute` 後直接在 SocketIO DAT callback 內執行 Command，不再用 `run()` 排到下一個 frame（#193）。
- `GET /v3/requests/{id}` 接受 `wait_ms`（0 到 25,000）。Request 進入終態或時間用完時才回答。client 的 `wait` 每次都帶剩餘時間減 0.1 秒（#194）。Daemon 停止服務前先回應所有長輪詢。
- `td` 啟動不再讀套件 metadata，也不匯入 httpx。`__version__` 是原始碼常數，`DaemonClient` 改用標準庫 `http.client`（#195）。

## TouchDesigner 來回時間

### 方法

`tools/locked_frame_timing_acceptance.py` 在隔離的 `127.0.0.1:19982` 跑本分支的 Daemon transport（這支測試驅動程式以下稱 harness），data root 放在 session scratch 的 `LOCALAPPDATA`。TouchDesigner 2025.32050 開一個一次性專案，專案的 Execute DAT 執行 `tools/locked_frame_timing_probe.py`。探針與 harness 用同一個系統時鐘（`GetSystemTimePreciseAsFileTime`）記錄每一段的時間，也記錄每個 frame 的開始時間。每輪先跑 5 次 `ops.get /project1` 暖機不計，再量 40 次。

一次性專案的建法：

1. 以 #157 驗收用的 `probe.toe` 為底。它是官方 Samples `NewProject.toe` 經 `toeexpand` 在 `/project1` 加入 Execute DAT `probe_exec` 而成。
2. `toeexpand` 後改寫 `probe_exec.text`：保留原本 23 bytes 的標頭，接著是 big-endian int32 的文字長度與文字本身。文字以 `runpy.run_path` 載入 `tools/locked_frame_timing_probe.py`，並把 `onStart`、`onFrameStart` 轉給它。
3. 改寫 `probe_exec.parm`，打開 `start` 與 `framestart`。
4. TOC 以 LF 寫入後 `toecollapse`。產物 1,026 bytes，SHA-256 `25a7f3b38aa18b38ec432ab46fefd4cf6c8b3ee8280e0e3425af3473bd42ebb5`，放在 git 忽略的 `.codex/frame-timing/probe.toe`。

0.11.0 基準用已發佈的 Agent artifact。本分支用 `--source agent`：先在另一個一次性 TouchDesigner 程序以 `agent/build_td.py` 從本分支原始碼建出 Agent，再載入量測。

```powershell
uv run python -m tools.locked_frame_timing_acceptance --work <scratch>\run --project .codex\frame-timing\probe.toe --source agent --evidence docs\evidence\v0120\frame-timing-0.12.0-running.json
uv run python -m tools.locked_frame_timing_acceptance --work <scratch>\pause --project .codex\frame-timing\probe.toe --source agent --pause --evidence docs\evidence\v0120\frame-timing-0.12.0-paused.json
```

本分支是 commit `1fca570`。使用者的 Daemon 與其他 TouchDesigner 程序全程沒有被碰到。

### 結果

timeline 執行中，40 次，單位 ms，中位數／p95：

| 分段 | 0.11.0 | 本分支 |
| --- | --- | --- |
| 整個 Request（client 送出到看到終態） | 61.8／64.2 | 33.3／35.1 |
| 送出到 Daemon 送出 `request_dispatch` | 6.8／7.2 | 6.4／6.9 |
| `request_dispatch` 到 Agent 的 reserve callback | 8.3／16.3 | 6.3／6.7 |
| reserve 到 Daemon 收到 accepted | 0.3／0.5 | 0.3／0.5 |
| accepted 到 Daemon 送出 `request_execute` | 5.4／5.8 | 5.4／6.1 |
| `request_execute` 到 Agent 的 authorize callback | 10.9／11.2 | 11.0／11.3 |
| authorize 到 Command 開始執行 | 16.3／16.4 | 0.0／0.0 |
| Command 執行 | 0.1／0.1 | 0.1／0.1 |
| outcome 送出到 Daemon 收到 | 0.5／0.5 | 0.3／0.5 |
| Daemon 收到 outcome 到 client 看到終態 | 13.1／22.9 | 3.7／4.1 |

- authorize 到執行少了一個 frame（16.3 ms），來自 #193。
- Daemon 收到 outcome 到 client 看到終態從 13.1 ms 降到 3.7 ms，p95 從 22.9 降到 4.1 ms，來自 #194。client 不再每 50 ms 查一次。
- `request_dispatch` 到 reserve 的 p95 從 16.3 降到 6.7 ms。0.11.0 偶爾要等一整個 frame，本分支 40 次都沒有。這段本分支沒有直接改動，可能是因為長輪詢讓 Request 的送出時間點和 frame 的相對位置變了。這是改善，所以沒有再追。
- 其他分段在 1 ms 內持平，沒有變慢的分段。

timeline 暫停，40 次：

| | 0.11.0 | 本分支 |
| --- | --- | --- |
| 整個 Request 中位數／p95 | 61.3／62.5 ms | 33.4／33.7 ms |
| 成功的 Request（含暖機） | 45／45 | 45／45 |

暫停時 callback 照常執行，Request 全部完成。

## CLI 暖機時間

### 方法

本分支以 `SOURCE_DATE_EPOCH` 固定在 commit `1fca570` 建出 `dist\td\td.exe`。兩邊都連到同一個已在執行的 0.11.0 Daemon，沒有連線中的 Instance。每個命令先跑 1 次不計，再量 20 次：

```powershell
uv run python tools/latency_bench.py --td "$env:LOCALAPPDATA\Programs\touchdesigner-cli\current\td.exe" --runs 20
uv run python tools/latency_bench.py --td dist\td\td.exe --runs 20
```

原始輸出在 `docs/evidence/v0120/cli-bench-0.11.0.json` 與 `cli-bench-0.12.0.json`。本分支的版本字串還是 0.11.0，改版號屬於發版。

### 結果

| 命令 | 0.11.0 p50／p95 | P0（`docs/p0-latency-acceptance.md`） | 本分支 p50／p95 |
| --- | --- | --- | --- |
| `td --version` | 536／559 ms | 403／407 ms | 306／320 ms |
| `td --json instances list` | 1,484／1,508 ms | 469／471 ms | 317／326 ms |

P0 的數字是另一次量測，只當參考。本分支相對 0.11.0 的同次量測，`instances list` 快了約 1.2 秒。

## 沒有涵蓋的部分

- CLI 數據只量了不需要 TouchDesigner 的命令。需要 Instance 的命令，總時間約等於 CLI 啟動加上上面的 TouchDesigner 來回時間。
- 冷啟動（含自動啟動 Daemon）本輪沒有改動，沒有重量。
