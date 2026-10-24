---
Status: accepted
---

# 命令延遲分階段處理，破壞性變更只進 1.0.0

暖機後一個線上 `td` 命令約 1.6 秒，TouchDesigner 內實際執行不到 0.01 ms，延遲幾乎全在 CLI 啟動、多餘匯入、HTTP 往返與四段握手。我們把改善拆成四個各自發版的階段，每個階段是一張 spec、一條分支、一個 PR：P0（0.11.1）只修多餘成本，P1（0.12.0）減少往返與 frame 等待，兩者都保留 Protocol v3 與 `td-runtime-reliability` 列出的全部不變式；P2（1.0.0）才做 Protocol v4、原生 client 與自有傳輸這類破壞性變更；P3（1.1.0）加常駐 session 與 frame 內批次。P2 與 P3 的 spec 要等 P1 的 frame 時戳探針在鎖定版 TouchDesigner 量完才寫，因為 SocketIO DAT callback 的 frame 時點與 `emit()` 何時送出官方沒有寫。

被拒絕的替代方案：

- 把 loopback TCP 換成 named pipe：本機 IPC 差異在微秒到 1 ms，遠小於 frame 等待，不值得換。
- 由 td-cli 調高專案 `cookRate`：那是使用者專案的設定。td-cli 只回報實際 frame 週期，不替使用者改。
- 一次做完 P0 到 P3：無法在中途取得前後對照數據，也會讓不破壞相容的改善被 1.0.0 的 migration 擋住。
