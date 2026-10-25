# P2b 命令延遲驗收

日期：2026-10-25。Spec: https://github.com/tedliou/td-cli/issues/209 。決策依據：`docs/adr/0004-protocol-v4-single-dispatch.md`。基準：`docs/p1-latency-acceptance.md`。

## 用語

- frame：TouchDesigner 每秒更新 60 次，每次是一個 frame，間隔 16.67 ms。
- timeline：TouchDesigner 的播放時間軸；暫停時畫面時間停住，但程式照樣更新。
- SocketIO DAT：TouchDesigner 內建、Agent 用來連 Daemon 的 operator。callback 是它收到訊息時呼叫的函式。
- Agent reload：重新初始化 Agent 的 extension。Agent 會關閉 SocketIO DAT 再重連，執行紀錄留在 TouchDesigner 程序內。
- p50／p95：把所有量測值排序後，第 50 與第 95 百分位的值。

## 變更

- Protocol v4：Agent 在 `request_dispatch` 的 callback 內登記並執行 Command。`request_accepted` 與 `request_execute` 移除，公開狀態不再有 `accepted`、`running`（#210）。
- dispatch 後斷線，Request 維持 `dispatched`，等 Instance 重新註冊後的 `execution_sync` 決定：有 outcome 就記錄，有未完成紀錄就維持執行中，沒有紀錄就放回佇列最前面。30 秒內沒回來或 Daemon 重啟就變成 `unknown`（#211）。

## 方法

與 P1 相同：`tools/locked_frame_timing_acceptance.py` 在隔離的 `127.0.0.1:19982` 跑本分支的 Daemon transport，TouchDesigner 2025.32050 開 P1 的一次性專案 `.codex/frame-timing/probe.toe`（SHA-256 `25a7f3b3…ebb5`），Agent 以 `--source agent` 從本分支原始碼建出。探針改成記錄 v4 的分段：dispatch callback、callback 內的登記、執行與 outcome 送出。每輪先跑 5 次 `ops.get /project1` 暖機不計，再量 40 次。

量完後再送兩個 Request，各在一次 Agent reload 前後：

- `reload_requeue`：探針丟掉第一次送達的 dispatch，不讓 Agent 登記，並在下一個 frame reload Agent。這等於 reload 時 dispatch 還沒被讀到。
- `reload_outcome`：Agent 照常登記並執行，探針擋下 outcome 的第一次送出，並在下一個 frame reload Agent。這等於 reload 時 outcome 還沒送出。

reload 的排程用 `TDResources` 當時間參考，timeline 暫停時也會執行。

```powershell
uv run python -m tools.locked_frame_timing_acceptance --work <scratch>\ft-run --project .codex\frame-timing\probe.toe --source agent --evidence docs\evidence\v100\frame-timing-v4-running.json
uv run python -m tools.locked_frame_timing_acceptance --work <scratch>\ft-pause --project .codex\frame-timing\probe.toe --source agent --pause --evidence docs\evidence\v100\frame-timing-v4-paused.json
```

本分支是 commit `4534034` 加上本票的探針改動。使用者的 Daemon 與其他 TouchDesigner 程序全程沒有被碰到。

## 結果

timeline 執行中，40 次，單位 ms，中位數／p95：

| 分段 | P1 | 本分支 |
| --- | --- | --- |
| 整個 Request（client 送出到看到終態） | 33.3／35.1 | 16.7／17.2 |
| 送出到 Daemon 送出 `request_dispatch` | 6.4／6.9 | 6.4／7.1 |
| `request_dispatch` 到 Agent 的 callback | 6.3／6.7 | 6.4／6.8 |
| reserve、accepted、`request_execute`、authorize 這段握手 | 16.7 | 已移除 |
| callback 內登記到開始執行 | 0.0／0.0 | 0.0／0.0 |
| Command 執行 | 0.1／0.1 | 0.0／0.1 |
| outcome 送出到 Daemon 收到 | 0.3／0.5 | 0.3／0.4 |
| Daemon 收到 outcome 到 client 看到終態 | 3.7／4.1 | 3.5／4.0 |

- 整個 Request 從 33.3 ms 降到 16.7 ms，等於一個 frame。省下的就是第二段握手：Daemon 往返 5.7 ms 加上等下一個 frame 開頭的 11.0 ms。
- 登記與執行在同一個 frame：40 次的 dispatch 與執行都落在同一個 frame（frame 差 0），callback 在 frame 開始後約 0.4 ms 執行。
- 40 次中最慢一次是 35.8 ms：它的 `request_dispatch` 晚於該 frame 的 callback 時點，多等了一個 frame（15.6 ms）。p95 不受影響。
- 其他分段與 P1 相差都在 0.5 ms 內，沒有變慢的分段。

timeline 暫停，40 次：

| | P1 | 本分支 |
| --- | --- | --- |
| 整個 Request 中位數／p95 | 33.4／33.7 ms | 16.6／17.0 ms |
| 成功的 Request（含暖機與 reload） | 45／45 | 47／47 |

暫停時探針收不到 frame 開始的時間，所以這一輪沒有 frame 位置的欄位；各段時間照常量到。

Agent reload，兩輪結果相同：

| Request | 結果 | Daemon 送出的 dispatch | 耗時 |
| --- | --- | --- | --- |
| `reload_requeue` | `succeeded` | 2 次，`execution_id` 不同 | 78 ms |
| `reload_outcome` | `succeeded` | 1 次 | 62 ms |

- `reload_requeue`：Agent 重新註冊時沒有這個 Request 的紀錄，Daemon 把它放回佇列最前面，以新的 `execution_id` 再送一次，執行一次就成功。
- `reload_outcome`：Agent 重新註冊時先送出保留的 outcome，Daemon 記錄成功，沒有重送 dispatch，Command 只執行一次。

第一次暫停輪的 `reload_requeue` 等到 60 秒逾時：探針當時用 `run(delayFrames=1)` 排 reload，預設跟著 timeline，暫停時永遠不會跑。Agent 沒有斷線，Request 正確地停在 `dispatched`。探針改用 `TDResources` 後重跑兩輪，上面是重跑的結果。

## 結論

60 fps 下 Request p50 是一個 frame（16.7 ms），比 P1 的 33.3 ms 少一半，達成 spec 的目標。timeline 暫停時照常完成。Agent reload 時沒讀到的 dispatch 會重新排隊，已執行的則以保留的 outcome 完成，兩者都沒有重複執行。

原始輸出在 `docs/evidence/v100/frame-timing-v4-running.json` 與 `frame-timing-v4-paused.json`。發佈的版本字串還是 0.11.0，改成 1.0.0 屬於發版。
