# P3 Frame Batch 驗收

日期：2026-10-25。Spec: https://github.com/tedliou/td-cli/issues/214 。決策依據：`docs/adr/0005-frame-batch-instead-of-session.md`。基準：`docs/p2b-latency-acceptance.md`。

## 用語

- Command Plan：`commands execute` 依序執行的一串 Command，見 GLOSSARY.md。
- Frame Batch：Command Plan 中連續的一段，以一個 `plan.execute` Request 送出，Agent 在一個 dispatch callback 內依序執行，見 GLOSSARY.md。
- dispatch：Daemon 把一個 Request 送給 Agent。
- outcome：Agent 回報的執行結果。
- frame：TouchDesigner 每秒更新 60 次，每次是一個 frame，間隔 16.67 ms。
- 執行時間：Agent 在 callback 內執行 Command 的時間，不含等待 frame。
- p95：把所有量測值排序後，第 95 百分位的值。

## 變更

- 新 Command `plan.execute`：一個 Request 帶 1–256 個 Command，Agent 依序執行，遇到第一個失敗就停，超過 8 ms 就在兩個 Command 之間停下（#215）。
- `td commands execute --frame-batch`：連續可放進 Frame Batch 的 Command 合成一段送出，下一段從第一個沒執行的 Command 開始（#216）。

## 方法

`tools/locked_frame_batch_acceptance.py` 沿用 P2b 的方法：在隔離的 `127.0.0.1:19982` 跑本分支的 Daemon transport，TouchDesigner 2025.32050 開一次性專案 `.codex/frame-timing/probe.toe`（SHA-256 `25a7f3b3…ebb5`），Agent 以 `--source agent` 從本分支原始碼建出。探針記錄每個 frame 的開始與 Agent 的 dispatch、執行、outcome 送出；Daemon 端記錄每個 dispatch 的送出與 outcome 的收到，兩邊用同一個系統時鐘。

本分支的原生 `td` 透過隔離的 Daemon 跑同樣的兩份計畫兩次，一次不加選項，一次加 `--frame-batch`：

1. 256 個 `ops.create`，建立 Constant CHOP。
2. 256 個 `parameters.set`，把每個 CHOP 的 `const0value` 設成它的編號。

一份計畫最多 256 個 Command，所以分成兩份。每次寫進自己新建的 Base COMP，跑完後讀回子 Operator 數量，並抽查第 0、127、255 個的值。

```powershell
uv run python -m tools.locked_frame_batch_acceptance --work <scratch>\fb-run --project .codex\frame-timing\probe.toe --source agent --evidence docs\evidence\v110\frame-batch-running.json
```

量測時的產品程式碼等於 commit `daa861f`。Agent artifact 在 `<scratch>\fb-run\td-agent.tox`，SHA-256 `3ea5966aac3a7eaa5a83cfd4d3d34269343132624ba937bf793e300f8a14e607`（Agent 版本字串 0.11.0，Protocol 4）。TouchDesigner 結束碼 0，timeline 執行中，cook rate 60。

## 結果

| | 逐一 | `--frame-batch` |
|---|---|---|
| 總時間 | 10,762 ms | 2,476 ms |
| 其中 256 個 `ops.create` | 5,681 ms | 2,354 ms |
| 其中 256 個 `parameters.set` | 5,081 ms | 122 ms |
| Request 數 | 512 | 55 |
| 每個 Request 的執行時間（中位數／p95／最大） | 1.08／2.46／3.56 ms | 8.93／9.27／9.69 ms |
| 執行期間的 frame 間隔（中位數／p95／最大） | 16.0／29.3／31.1 ms | 17.7／29.4／30.6 ms |
| 讀回 | 256 個 Constant CHOP，值正確 | 256 個 Constant CHOP，值正確 |

- 總時間快 4.35 倍。
- 沒有一段超過 8 ms 加一個 Command 的時間。「一個 Command 的時間」取逐一模式中最慢的單一 Command（3.56 ms），是近似值，不是逐段量出的。上限因此是 11.56 ms，Frame Batch 最慢的一段是 9.69 ms。
- 兩種模式的 frame 間隔最大值都約 31 ms。Frame Batch 沒有讓 frame 間隔變長。中位數多 1.7 ms，因為每段在 frame 內多執行約 9 ms。
- `parameters.set` 很快，256 個只用了 2 段（158 個與 98 個）。
- `ops.create` 每個約 1.7 ms，所以 8 ms 預算內每段約 5 個，256 個要 53 段。

## 每段為何約兩到三個 frame

逐一模式每個 Request 約一個 frame。Frame Batch 的建立階段每段約 44 ms，約兩到三個 frame。分段量測（中位數）：

| 分段 | 逐一 | `--frame-batch` |
|---|---|---|
| outcome 送出時在 frame 內的位置 | 2.6 ms | 11.1 ms |
| outcome 送出到 Daemon 收到 | 0.5 ms | 0.7 ms |
| Daemon 收到 outcome 到送出下一個 dispatch | 10.8 ms | 18.8 ms |
| dispatch 送出到 callback | 4.5 ms | 14.6 ms |

逐一模式的 outcome 在 frame 開頭約 2.6 ms 送出，td 與 Daemon 約 11 ms 送出下一個 dispatch，趕得上下一個 frame。Frame Batch 先執行約 9 ms，outcome 在第 11 ms 才送出，td 與 Daemon 又要約 19 ms，下一個 dispatch 錯過下一個 frame，等到再下一個 frame 才執行。

Daemon 收到 outcome 後多出的約 8 ms 花在 td 與 Daemon 之間：Frame Batch 的 Command 與結果都比單一 Command 大，Daemon 要驗證與保存整段，td 要逐行輸出每個 Command 的進度。這 8 ms 在 td 與 Daemon 內各佔多少沒有拆開量。多出的 frame 是哪一段造成的已經量到：outcome 晚送出，加上這段較長的往返。frame 間隔也沒有變長。所以判定不構成 #217 的停止條件（frame 間隔明顯超過一個 frame 且原因不明）。

## Invariant 與失敗邊界

依 `.agents/skills/td-runtime-reliability/SKILL.md` 列出本 spec 影響的 invariant。Daemon 端的 FIFO、一次一個 dispatched Request、lease、斷線與 sync 規則都沒改，`plan.execute` 在 Daemon 端是一個普通的 Request。

| Invariant 或失敗邊界 | 可觀察的狀態 | 證據 |
|---|---|---|
| 沒有紀錄就沒有執行：登記與整段執行在同一個 callback | 重送的 dispatch 回傳保存的 outcome，不再執行 | `tests/test_agent_runtime.py` 重送測試 |
| 遇到第一個失敗就停，不回復 | Request `succeeded`，`stopped_reason: failed`，前面的變更保留 | Agent runtime 與原生 `td` 測試 |
| mutation 執行時拋出例外 | 該項 `request_outcome_unknown`，`td` 的 `stopped` 指向該 Command | Agent runtime 測試 |
| 8 ms 預算只在兩個 Command 之間檢查 | `stopped_reason: frame_budget`，`td` 從第一個沒執行的接著送 | Agent runtime 與原生 `td` 測試、本次鎖定版 |
| 結果過大 | `result` 省略並標 `results_omitted`，`ok` 與 `error` 保留，Request 仍 `succeeded` | Agent runtime 測試（含剛好低於上限的情況） |
| 整段 Request `unknown`（斷線逾時、Daemon 重啟） | `stopped` 指向該段第一個 Command，段內都可能已執行，`td` 不自動重送 | 原生 `td` 測試 |
| 總逾時 | `stopped` 的 `wait_timeout`，結束碼 6，不重送 | 原生 `td` 測試 |
| 舊 Agent 或缺巢狀能力 | 送出前 `command_unsupported`，沒有 Request | lifecycle 測試 |
| 取消 | 與一般 Request 相同，Frame Batch 沒有新增取消路徑 | 不另測，Daemon 端未改 |

## 限制

- 只在 60 fps、本機迴路位址量過一次。
- `ops.create` 的每段執行數由它本身的執行時間決定；其他 Command 的每段個數會不同。
- 上面 Daemon 與 td 之間多出的約 8 ms 沒有拆開量。若要讓每段回到一個 frame，要先量清楚這段，再決定縮短 Daemon 端處理或調整預算。8 ms 預算是 ADR 0005 的決定，本驗收不更動。
- Frame Batch 的 dispatch 最多 64 KiB，本次每段遠低於此。

## 結論

`--frame-batch` 把 256 個建立加 256 個設定從 10.8 秒降到 2.5 秒，讀回的圖與逐一執行相同。每段執行時間都在 8 ms 加一個 Command 之內，frame 間隔沒有變長，達成 spec 故事 1、2。

原始輸出在 `docs/evidence/v110/frame-batch-running.json`。這些變更隨 1.0.0 發佈。
