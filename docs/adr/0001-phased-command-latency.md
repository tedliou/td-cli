---
Status: accepted
---

# 命令延遲分階段處理，破壞性變更只進 1.0.0

暖機後一個線上 `td` 命令約 1.6 秒。TouchDesigner 內實際執行不到 0.01 ms。延遲幾乎都來自 CLI 啟動、多餘匯入、HTTP 往返與四段握手。

我們把改善拆成四個階段。每個階段各自發版，各是一張 spec、一條分支、一個 PR：

| 階段 | 版本 | 內容 | 相容性 |
| --- | --- | --- | --- |
| P0 | 0.11.1 | 去掉多餘匯入、重用連線、onedir 打包、延遲 bench | 保留 Protocol v3 與全部不變式 |
| P1 | 0.12.0 | 減少往返與 frame 等待 | 保留 Protocol v3 與全部不變式 |
| P2a | 0.13.0 | 原生 `td` client | 保留 Protocol v3 與全部不變式 |
| P2b | 1.0.0 | Protocol v4、自有傳輸 | 破壞性更新 |
| P3 | 1.1.0 | 常駐 session、同一 frame 內批次 | 新增功能 |

「全部不變式」指 `td-runtime-reliability` skill 列出的 Request 與 Instance 規則。frame 是 TouchDesigner 每秒重複數十次的一輪計算與繪製。

P2 原本是一個階段。P1 量完後拆成兩段：暖機後 `td --json instances list` 還要 317 ms，幾乎全是 Python 啟動；TouchDesigner 來回只剩 33 ms。原生 client 不需要改 Protocol，照本 ADR「不破壞相容的改善不等 1.0.0」的規則先做（見 ADR 0003）。P2b 的 spec 等 P2a 量完再寫。

P2 與 P3 的 spec 要等 P1 的 frame 時戳探針量完才寫。原因是官方文件沒有寫兩件事：SocketIO DAT 收到訊息時，callback（收到事件時呼叫的函式）在 frame 內什麼時候執行；`emit()`（送出訊息的呼叫）是立刻送出，還是等 frame 結束才送。

被拒絕的替代方案：

- 把 loopback TCP（本機自己連自己的 TCP）換成 named pipe（Windows 的本機行程間管道）：兩者差異在微秒到 1 ms，遠小於 frame 等待，不值得換。
- 由 td-cli 調高專案的 `cookRate`（每秒 frame 數）：那是使用者專案的設定。td-cli 只回報實際 frame 週期，不替使用者改。
- 一次做完 P0 到 P3：中途拿不到前後對照數據。不破壞相容的改善也會被 1.0.0 的遷移卡住。
