---
Status: accepted
---

# P3 只做 frame 批次，不做常駐 session

ADR 0001 的 P3 原本有兩項：常駐 `td session`，以及同一 frame 內批次。P2a 與 P2b 量完後，前提變了。

- 原生 `td ops get` 整個行程 62.3 ms，其中 Request 本身 62.0 ms（`docs/p2a-latency-acceptance.md`）。行程啟動與 HTTP 只佔約 0.3 ms，常駐 session 沒有東西可省。
- Protocol v4 下一個 Request 是一個 frame，60 fps 下 16.7 ms（`docs/p2b-latency-acceptance.md`）。`commands execute` 每個 Command 是一個 Request，256 個 Command 至少要 256 個 frame，約 4.3 秒。

決定：

- 不做常駐 session。要在一個行程內送多個 Command，用既有的 `commands execute`。
- 新增 Command `plan.execute`：一個 Request 帶一段有序的 Command（Frame Batch）。Agent 在 `request_dispatch` 的 callback 內依序執行，遇到第一個失敗就停，不回復已完成的變更。登記與執行仍在同一個 callback，所以「沒有紀錄即沒有執行」照舊成立，整段只有一個 outcome。
- 每段執行超過 8 ms，Agent 就在兩個 Command 之間停下，其餘的回報為未執行。半個 60 fps frame 的預算讓 TouchDesigner 不會因為批次而明顯卡住；單一 Command 本身無法中斷。
- 只有 `fast_read` 與 `bounded_mutation` 兩類 Command 能放進 Frame Batch。可信資產匯入、掃描與匯出照舊一個 Request 一個，因為它們的 lease 與結果大小是單獨估的。`batch.execute` 與 `plan.execute` 不能放進 Frame Batch。
- 一段 Frame Batch 的 Command 文字合計不超過 64 KiB。鎖定版 SocketIO DAT 已證實收得到約 65 KB 的單一 dispatch（`docs/menu-item-limit-acceptance.md`），更大的沒有量過。
- `commands execute --frame-batch` 把計畫切成 Frame Batch 依序送出，前一段的結果決定下一段從哪裡開始。不加選項時行為不變。每個 Command 的進度行仍逐一輸出，Request ID 是所屬 Frame Batch 的 ID，另附在段內的位置。
- Protocol 版本維持 4。`plan.execute` 是新的 Agent 能力，舊 Agent 收到 `command_unsupported`。

本決定取代 ADR 0001 中 P3 的常駐 session，並推翻 `docs/ordered-command-run-spec.md` 的「選 client 端編排而不在 Agent 端批次執行 mutation」：當時 Agent 的登記與執行分在兩個 callback，批次中斷後無法判斷做到哪裡；v4 已把兩者放進同一個 callback。

被拒絕的替代方案：

- 常駐 `td session` 從 stdin 讀 JSONL：只省約 0.3 ms，代理每次呼叫工具還是要起新行程，用不上長駐的管道。
- Daemon 一次送出多個 Request 讓它們在同一個 frame 執行：每個 Command 保留自己的 Request ID，但要放寬「每個 Instance 一次一個 dispatched Request」，lease、斷線與 sync 都要改成多筆，風險遠大於收益。
- 一段 Frame Batch 跨多個 frame 執行到完：要在 callback 之外繼續執行，斷線與 reload 時的狀態會變成「執行到一半」，失去「沒有紀錄即沒有執行」的保證。
- 不設時間預算，一次執行到完：256 個 mutation 可能讓 TouchDesigner 停住數百 ms，使用者的作品會掉 frame。
