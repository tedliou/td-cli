---
Status: accepted
---

# Protocol v4 在 dispatch callback 內登記並執行，保留 SocketIO DAT

P1 之後，60 fps 下一個 Request 的 TouchDesigner 往返是 33 ms。frame 時戳探針（`docs/p1-latency-acceptance.md`）把它拆成：送出到 `request_dispatch` 6.4 ms、等 frame 開頭的 reserve callback 6.3 ms、`request_accepted` 0.3 ms、Daemon 寫入後送出 `request_execute` 5.4 ms、再等下一個 frame 開頭的 authorize callback 11.0 ms，其餘 4 ms 是執行與回報。一半的時間花在第二段握手。

決定：

- Protocol v4 的正常路徑只有兩個訊息：Daemon 送 `request_dispatch`，Agent 回 outcome（沿用分塊）。Agent 在同一個 `request_dispatch` callback 內寫入執行紀錄並執行 Command。`request_accepted` 與 `request_execute` 移除。Agent 在執行前仍可回 `request_rejected`（`command_wire_invalid`、`request_id_conflict`、`instance_draining`、`execution_capacity_full`）。
- Daemon 在 dispatch 時發出 `execution_id` 並開始 lease。公開的 Request 狀態移除 `accepted` 與 `running`：Daemon 看不到執行開始的時刻，`dispatched` 表示「已送給 Agent，可能正在執行」。
- 登記與執行在同一個主 thread callback 內完成，所以 Agent 沒有某個 Request 的紀錄，就證明它沒有執行過。Agent 只執行 Connection ID 等於自己目前連線的 dispatch。
- dispatch 後斷線，Request 維持 `dispatched`，等 Agent 重新註冊後的 `execution_sync`：有 outcome 紀錄就記錄 outcome；沒有紀錄就放回佇列最前面；Instance 在保留期內沒回來就變成 `unknown`。Daemon 重啟時，`dispatched` 的 Request 變成 `unknown`（`request_outcome_unknown`），之後 Agent 重送的 outcome 仍可修正它；`queued` 照舊是 `daemon_shutdown`。
- Daemon 只接受 v4 Agent；v3 Agent 註冊時收到 `protocol_incompatible`。不保留 v3 路徑。
- 傳輸維持 SocketIO DAT 與 python-socketio。24 KiB 分塊與 256 KiB outcome 上限不變。

本決定取代 ADR 0001 中 P2b 的「自有傳輸」與規劃中的「背景 thread socket」，並延伸 ADR 0002：執行點從 `request_execute` callback 移到 `request_dispatch` callback。

被拒絕的替代方案：

- Agent 用背景 thread 自己收 socket：TouchDesigner 物件只能在主 thread 存取，執行還是要等 frame 開頭，和 SocketIO DAT callback 的時點相同，延遲不會變短。代價是 Agent reload、專案關閉、TouchDesigner 當掉時都要正確收掉 thread 與 socket。
- Daemon 自寫 HTTP 與長度前綴 TCP，取代 FastAPI、uvicorn 與 python-socketio：暖機延遲已在 frame 等待之下；冷啟動中這些匯入的佔比沒有量過。要做時另立 spec，先量再決定。
- 斷線時把 `dispatched` 一律標成 `unknown`：實作較簡單，但 Agent 短暫重連就會讓沒執行過的 Request 也要使用者自行判斷；「沒有紀錄即沒有執行」已能安全地重新排隊。
