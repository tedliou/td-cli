# TouchDesigner 本機診斷執行入口

研究日期：2026-08-09。目標版本為 TouchDesigner 2025.32050；問題是能否在目前已開啟的 `.toe` 中，建立只監聽 localhost、具驗證且可由外部工具提交任意 Python 的短期入口，以減少 Textport 人工往返。本文件只採 Derivative 官方文件，並將文件未明說之處標為設計推論。

## 結論

可以，但 TouchDesigner 沒有一個官方「外部 Python 附著至既有 GUI 行程並取得 live `td` module」的現成 REPL。最合適的官方構件是 **Web Server DAT**：它本身可提供 HTTP 與 WebSocket server、可指定 `Local Address`，並把請求交給 Python callback。建議做成一個暫時的 `/project1/td_diagnostic_bridge` Base COMP，只監聽 `127.0.0.1`，以隨機 bearer token 驗證，採「提交工作／輪詢結果」兩階段 API；網路 callback 只驗證與排隊，真正的 `exec` 由每幀執行的 Execute DAT 在 TouchDesigner 主執行緒完成。

### 鎖定版本實測修正

後續在 TouchDesigner 2025.32050 實作時確認，該 build 的 Web Server DAT 尚無 `Local Address` parameter；Derivative release notes 顯示此 parameter 是後續 2025.33070 才加入。因此鎖定版若使用 Web Server DAT 只能留白並監聽所有介面，不符合本研究的安全底線。實作改採 Python 標準庫 `ThreadingHTTPServer(("127.0.0.1", 9983), ...)`，背景 thread 僅處理驗證與 queue，Execute DAT 仍在 TD main thread 執行 job。此替代保留相同 HTTP 協定與撤除模型，同時能在鎖定版明確保證 loopback binding。[TouchDesigner 2025.30000 Release Notes](https://derivative.ca/UserGuide/Release_Notes/2025.30000)

這不是安全沙箱。取得 token 的本機程式可執行與 TouchDesigner 目前使用者相同權限的任意 Python，包括修改／刪除 network、讀寫檔案、啟動 subprocess，或以無窮迴圈卡死 TouchDesigner。因此它只適合作為人工明確啟用、短時間存在、可立即移除的開發診斷入口，不應隨產品發布或暴露至 LAN。

## 官方能力與限制

### Web Server DAT：唯一直接合適的內建 server

Web Server DAT 官方支援 HTTP、WebSockets、文字及 binary data，請求處理由 callbacks 決定。`Local Address` 可指定要監聽的 IP；空白會監聽所有介面，因此本案必須明確填入 `127.0.0.1`，不能留白。它也支援 TLS、mTLS，並提供 `authenticateBasic` 做 Basic authentication；但官方同時明說安全完全由使用者負責。[Web Server DAT](https://docs.derivative.ca/Web_Server_DAT)

對本機短期橋接，TLS 並不是首要邊界：loopback 綁定先阻止外部主機連入，再用每次啟動新產生的高熵 token 防止同機其他程序猜中。若需求擴大到 LAN，就不應只是把地址改成 `0.0.0.0`；應重新設計為 TLS/mTLS、憑證生命週期、明確授權與稽核。

官方頁面沒有保證 Web Server DAT callback 執行在哪個執行緒。不能據此假設 callback 可安全地直接遍歷或修改 OP network。Derivative 的 threading 指引明確說，一般原則是避免從其他執行緒讀寫主 TouchDesigner thread 的物件；常見模式是由 Execute DAT 每幀檢查並 dequeue 工作。[Python threading in TouchDesigner](https://docs.derivative.ca/Python_threading_in_TouchDesigner) 因此「callback 僅排隊、Execute DAT 執行」是保守設計推論，即使實際 callback 恰在主執行緒也仍能避免 request callback 內的重入與長時間阻塞。

### `run()` 與 Execute DAT：切回逐幀執行

Execute DAT 可在 frame start 或 frame end 執行 Python。[Execute DAT](https://docs.derivative.ca/Execute_DAT) `td.run()` 也能把字串或 callable 延至 frame end 或指定 frame 執行，並指定相對的 operator context。[Run Command Examples](https://docs.derivative.ca/Run_Command_Examples)

本案較適合一個 Execute DAT 每幀最多取出一項工作，原因是 queue、執行狀態、stdout、traceback 與結果保存都可集中管理。若只用 `run(code, endFrame=True)`，仍需另外處理結果回傳與過多工作的 backpressure。無論使用哪一種，任意 Python 都無法被可靠地「中途取消」；若程式在主執行緒無限迴圈或呼叫 blocking API，UI、timeline 與結果 API 都會一起停止。官方 threading 文件也警告，阻塞主執行緒會掉幀，甚至 hang 或 crash TouchDesigner。[Python threading in TouchDesigner](https://docs.derivative.ca/Python_threading_in_TouchDesigner)

### WebSocket DAT：方向不對

WebSocket DAT 是連向既有 WebSocket server 的 client；它的參數是 server network address/port，收到訊息後執行 callback，類別方法提供 `sendText`／`sendPong`。[WebSocket DAT](https://docs.derivative.ca/WebSocket_DAT)、[websocketDAT Class](https://docs.derivative.ca/WebsocketDAT_Class) 它不能取代本案所需的 server。若偏好 WebSocket transport，仍應使用 **Web Server DAT 的 WebSocket callbacks**；但 HTTP job API 對 CLI 更簡單，且斷線不會遺失執行結果。

### TCP/IP DAT：可做，但要自行重造太多協定

TCP/IP DAT 可當 server、指定 `Local Address`，並為每次收到的資料呼叫 callback；但 TCP 是 stream，官方提醒一次 read 的邊界是任意的，客製格式需自行累積完整 message。[TCP/IP DAT](https://docs.derivative.ca/TCP/IP_DAT) 若使用它，就得自行完成 framing、認證、錯誤 response、重試與結果查詢。Web Server DAT 已提供 HTTP parsing，較小且易從 PowerShell／Python 呼叫。

### Remote Panel 與所謂 TouchDesigner Remote：不是 Python 控制面

官方 `remotePanelClient`／`remotePanelServer` 是在兩個 TouchDesigner instance 間傳送面板影像與滑鼠／觸控互動；server component 接收的是 panel interaction data。[Palette:remotePanel](https://docs.derivative.ca/Palette%3AremotePanel) 它不是任意 Python RPC，也仍需把 palette component 放進專案，因此不適合本問題。

### 外部 Python、`td` module 與 TDI：不能附著 live session

官方 Python 文件說 `td` module 的 members 在 TouchDesigner 啟動後，自動存在於 scripts、expressions 與 Textport；外部 Python 安裝的用途是讓 TouchDesigner 尋找第三方 packages。[Python](https://docs.derivative.ca/Python)、[td Module](https://docs.derivative.ca/Td_Module) 2025.30000 起的 TDI Library 則提供 VS Code help 與 code completion；官方特別說它不包含實際 TouchDesigner object code，只是 stubs。[TDI Library](https://docs.derivative.ca/TDI_Library)

因此，從外部執行 `app.pythonExecutable` 或讓 VS Code 選取 TouchDesigner Python，並不會取得目前已開啟 `.toe` 的 `root`、`op()` 與 live OP objects。外部程序仍需要 Web Server DAT、TCP/IP DAT、Socket.IO 等明確 IPC 入口。

## 推薦的最小協定

建議橋接器只提供三個 endpoint：

1. `GET /health`：需驗證；回傳 bridge version、TouchDesigner build、目前 `.toe`、queue depth、是否 busy，不執行程式碼。
2. `POST /jobs`：JSON body 僅含 `code`、可選 `mode`（`exec` 或 `eval`）及 `contextOp`；驗證大小後建立不可預測的 job id，排入有限 queue，立刻回 `202 Accepted`。
3. `GET /jobs/<id>`：回傳 `queued/running/succeeded/failed`、以 `repr` 序列化的 value、捕捉的 stdout/stderr、完整 traceback 與開始／結束時間。

必要邊界：

- Web Server DAT 的 `localaddress = 127.0.0.1`；啟動後由外部先驗證實際 listening socket 沒有綁到 `0.0.0.0`／IPv6 all interfaces。
- 啟動時用 `secrets.token_urlsafe(32)` 產生一次性 token，僅寫入 workspace 中 ACL 限定目前 Windows 使用者的暫存檔；不把 token 保存進 `.toe`、DAT text、console 或 Git。
- 每個 endpoint 都要求 `Authorization: Bearer <token>`，以 `hmac.compare_digest` 比對；未知 path、錯誤 method 與未授權請求一律拒絕。Web Server DAT 官方只有內建 Basic helper 的說明，因此 bearer 驗證屬於 callback 內的應用層設計，而非官方內建 auth。
- 限制 request body、queue 長度、結果長度與同時執行數（固定為一）；拒絕新的工作比讓 TD 記憶體無限增長好。
- callback 不接觸除 bridge queue 外的 TD objects；Execute DAT 每幀最多執行一項。每項以新的 globals dict 執行，但必須誠實標示：Python globals 隔離不是 sandbox，程式仍能透過內建 `op()`／`root`／imports 取得整個環境。
- 每次結果包含 traceback；執行前後記錄 job id 與 source hash，不把含 secrets 的 source 自動寫入一般 log。
- 提供明顯的 `Active` toggle／停止 pulse。停止時先關閉 Web Server DAT、清空 queue、刪除 token 檔，再視需要 destroy 整個 bridge COMP。

## 一次性 bootstrap 與撤除

無論選哪個入口，都存在不可消除的一次性信任啟動問題：在尚無入口前，外部 agent 無法自行把入口注入目前 TouchDesigner 行程。最少人工操作是一次在 Textport 執行 bootstrap，或人工載入一個預先審查的 `.tox`。考量目前複製長命令曾多次失敗，較可靠流程是：

1. repo 先產生一個很小、可閱讀與測試的 bridge `.tox`（或一個本機 `.py` bootstrap 檔）。
2. 人工只在 Textport 執行一行固定命令，讀取該檔並建立／載入 `/project1/td_diagnostic_bridge`；不要把完整實作塞在命令列。
3. bootstrap 成功後，外部 agent 先呼叫 `/health`，再用 job API快速試錯。
4. 完成診斷後，外部先停 server；人工可確認 `/project1/td_diagnostic_bridge` 已移除。若本輪不希望持久化，關閉 `.toe` 時不儲存。

最可撤除的形態是所有 nodes、queue、callbacks 都在單一 Base COMP 內，且不修改專案其他 OP。token 檔與 bridge COMP 是完整的撤除清單。若要跨重開 `.toe` 使用才保存它；否則保持 session-only，減少意外留下任意程式碼入口的機率。

## 方案比較

| 方案 | 能否控制目前 `.toe` | server／驗證 | TD thread 安全 | bootstrap 與成本 | 判定 |
|---|---|---|---|---|---|
| Web Server DAT + HTTP job queue | 是 | 內建 server；loopback、TLS/mTLS、Basic 能力；bearer 由 callback 實作 | callback 排隊、Execute DAT 每幀執行 | 一次載入單一 COMP；CLI 易呼叫 | **推薦** |
| Web Server DAT + WebSocket | 是 | 同一 server，可長連線 | 同樣需 queue／主執行緒執行 | client 與 reconnect/state 較複雜 | 有串流需求才用 |
| WebSocket DAT | 是，但它是 client | 必須另有外部 server | callback 同樣需避免跨 thread TD access | 多一個 daemon，方向反轉 | 不推薦作最小入口 |
| TCP/IP DAT server | 是 | loopback 可設；auth/framing 全自建 | 同樣需 queue | 協定與錯誤處理較多 | 可行但沒有優勢 |
| remotePanel | 只能傳 UI 互動 | 專用 TD-to-TD 元件 | 不提供 Python RPC | 需第二個 TD 與 panel 配置 | 不適用 |
| 外部 Python／TDI | 否，不能附著既有行程 | 無 | 無 live OP access | 只提供 packages 或 editor stubs | 不適用 |

## 決策建議

採用「session-only Web Server DAT bridge + HTTP async jobs + Execute DAT main-thread dequeue」。先把它視為診斷工具而非產品 Agent 的一部分：由一個極短、固定且可從檔案複製的 bootstrap 啟用；成功後所有探查與修正命令均走 localhost API；每輪結束主動關閉並撤除。這能直接解決目前反覆由人類貼 Textport、回傳 traceback 的低效率，同時把暴露面限制在單機、單次 session 與單一可刪 COMP。

在開始實作前仍應訂一條不可妥協的操作規則：只對可丟棄或已備份的 `.toe` 啟用。任意 Python endpoint 沒有技術手段同時保有「任意」與「安全沙箱」；loopback 與 token 解決的是未授權連線，不解決已授權程式碼本身造成的資料損壞或 TD hang。
