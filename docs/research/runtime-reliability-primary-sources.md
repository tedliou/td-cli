# TouchDesigner 通訊與命令執行可靠性：第一方證據基線

研究日期：2026-09-01。適用於本專案鎖定的 Python 3.11、TouchDesigner
2025.32050、Socket.IO v4／`python-socketio` 5.x。本文只採正式規格、官方文件、
原始論文與 IEEE Computer Society 的 SWEBOK；專案對照以目前 `develop` 工作樹為準。

## 結論

生產級目標不應宣稱「恰好一次」。Socket.IO 官方只保證抵達訊息的順序，預設抵達
語意是 at-most-once；網路斷開時，發送方無法由傳輸狀態判定 TouchDesigner mutation
是否已執行。正規設計是：**Request ID、持久化後派送、每個 TouchDesigner Instance
單一 in-flight、Agent 端去重與結果保留、connection generation 隔離，以及斷線後的
`unknown` 終態**。重試只能重送同一 Request ID 以查回既有結果；不得以新 Request ID
自動重做未知 mutation。

目前方向大致符合此模型，但有三個必須先處理的實作風險：

1. `RequestStore` 以 `check_same_thread=False` 共用一條 connection；FastAPI 官方說普通
   `def` endpoint 會在線程池執行，而 Python 3.11 官方要求跨執行緒共用時由應用程式
   序列化寫入。現有讀取 endpoint 與 event-loop 寫入可能同時使用同一 connection。
2. `isolation_level=None` 代表 SQLite autocommit；Python 官方說 connection context manager
   在沒有開啟 transaction 時是 no-op。因此 `recover()` 的多筆轉態不是一個 transaction，
   `update()` 的 read-modify-write 也不是原子狀態轉移。
3. `python-socketio` 官方註明對同一 connection 的 `emit()` 不是 concurrency-safe；目前 dispatch、
   draining 與其他 background task 沒有一個共同的 per-connection outbound serializer。

建議先把 persistence 做成單一擁有者，並讓每一個 Request 狀態轉移由條件式 SQL 在明確
transaction 中原子完成，再談更多重試或 recovery 功能。這是修正資料一致性邊界，不是
加入 fallback。

## 1. TouchDesigner 執行緒與 Socket.IO 邊界

### 官方證據

- Derivative 的 [SocketIO DAT](https://docs.derivative.ca/SocketIO_DAT) 是 Socket.IO client，
  支援 v3/v4 server；`onReceiveEvent` 逐訊息 callback，且 **不支援 acknowledgement
  callbacks**。因此不能把 Socket.IO emit ACK 當作完成證明，必須維持明確的
  `request_accepted`、`request_result`、`result_recorded` 應用層事件。
- Derivative 的 [Python threading in TouchDesigner](https://docs.derivative.ca/Python_threading_in_TouchDesigner)
  明確警告：主 TD thread 必須持續運行，阻塞會掉幀，嚴重時 hang/crash；一般原則是
  secondary thread 不讀寫主 thread 的 TD objects，常見正規模式是由 Execute DAT 每幀
  dequeue 工作。
- [Execute DAT](https://docs.derivative.ca/Execute_DAT) 可在 frame start/end 執行；這是把
  TD object 操作固定於 TD runtime 節點的官方構件。
- Derivative 的 [Thread Manager](https://docs.derivative.ca/Thread_Manager)／
  [ThreadManager Extension](https://docs.derivative.ca/ThreadManager_Ext) 是官方 worker-to-main
  task 構件；若採用仍須自行設定有限 queue/backpressure，不能把無上限預設當作 production
  容量策略。
- [RFC 6455 §5.5.2](https://www.rfc-editor.org/rfc/rfc6455.html#section-5.5.2) 規定收到
  Ping 必須儘快回 Pong，但它只證明 endpoint 能回應控制訊框，不證明 TD application
  command 能執行。Socket.IO/TCP heartbeat 與目前 application heartbeat 應視為不同訊號。

### 對本專案的設計約束

- 保留「Socket callback 驗證 envelope → 在 TD 主執行緒執行 → 結果保留至 daemon 確認」；
  不以背景 Python thread 操作 TD objects，也不引入第二條隱藏執行路徑。
- `onReceiveEvent` 目前同步呼叫 `agent.accept()` 並執行完整 Command。這能維持單執行緒，
  但較慢 Command 會同時延遲 timeline、application heartbeat 與結果送出。應以鎖定版
  TouchDesigner 實測建立每類 Command 的最大 wall time/frame stall budget；超限時拒絕
  較大的輸入或拆成明確、有界的 Command，而不是用無法安全取消的 timeout thread。
- transport heartbeat 只能判斷連線；application heartbeat 必須包含 connection ID，且由
  monotonic clock 判定逾時。超時後拒絕新 Request、把已派送者標成 `unknown`，不能自動重派。
- SocketIO DAT 不支援 ack callback，故 Agent 保留 `pending_results` 並等 `result_recorded`
  是必要的 end-to-end 完成協定，不是冗餘 fallback。

## 2. 失聯、重試、去重與結果語意

### 正式／原始證據

- Socket.IO 官方 [Delivery guarantees](https://socket.io/docs/v4/delivery-guarantees/)
  說明：抵達的事件有順序保證，但預設僅 at-most-once；連線中斷時不保證對端收到，server
  也不替斷線 client 保存漏接事件。額外保證必須由 application 實作，官方示例正是事件
  unique ID、持久化、client offset 與 reconnect replay。
- Socket.IO 官方 [Connection state recovery](https://socket.io/docs/v4/connection-state-recovery)
  明說 recovery 不一定成功，失敗時仍須同步 client/server state。因此它不能取代 Request
  store、Agent 去重或 `unknown`。
- `python-socketio` 5.x 官方
  [`AsyncServer.emit()`](https://python-socketio.readthedocs.io/en/stable/api.html#socketio.AsyncServer.emit)
  說明同一 connection 的 concurrent emit 可能讓多封包訊息順序錯亂，建議用 Lock；因此
  per-Instance 單一 in-flight 尚不足以涵蓋 heartbeat/draining/result ACK，所有對同一 SID 的
  outbound event 都應共用 serializer。
- [RFC 9110 §9.2.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.2)
  規定：非冪等 method 不應自動重試，除非 client 知道語意實際冪等，或能確認原請求從未
  套用。相同原則適用於承載 mutation 的 Socket.IO event。
- Saltzer、Reed、Clark 的原始論文
  [End-to-End Arguments in System Design](https://web.mit.edu/saltzer/www/publications/endtoend/endtoend.pdf)
  指出 duplicate suppression、crash recovery、delivery acknowledgement 的完整正確性需要
  endpoint/application knowledge；低層機制至多是效能增強。
- Birrell、Nelson 的原始 RPC 論文
  [Implementing Remote Procedure Calls](https://birrell.org/andrew/papers/ImplementingRPC.pdf)
  把 machine/communication failure 下的 call semantics 列為 RPC 核心問題，並以 call ID／
  sequence number 消除重送封包造成的重複；它不把 timeout 等同於 remote procedure 未執行。
- Ongaro、Ousterhout 的原始 [Raft 論文 §6](https://raft.github.io/raft.pdf) 明列「commit 後、
  reply 前 crash」會使 client retry 再執行；其解法是每個 command 的唯一 serial，以及保存
  每個 client 最新 serial/result。這正是相同 Request ID 回播既有結果、而非建立新 ID 的依據。

### 可接受的 Request 狀態模型

`queued → dispatched → running → succeeded|failed`

- daemon crash：`queued → daemon_shutdown`；`dispatched|running → unknown`。
- Instance disconnect/application heartbeat timeout：當代 connection 的
  `dispatched|running → unknown`；舊 connection 的遲到事件不能推進新 generation。
- 相同 Request ID + 相同 canonical Command：回傳原 snapshot/result；相同 ID + 不同 Command：
  衝突。Agent 也必須以相同規則去重。
- `unknown` 是可查詢的終態，不是 retryable failure。若操作者選擇再次 mutation，應是明確的
  新 Request，且 CLI 必須呈現風險，不能由 daemon 自動進行。

若未引入可包住 TD graph side effects 的真正 transaction/undo protocol，就不可把上述模型
改名為 exactly-once。傳輸去重最多提供「同一 Agent runtime 對同一 Request ID 不重複執行」；
Agent/TD process 在執行後、保存結果前 crash，仍是不可消除的不確定窗口。

## 3. `asyncio` 與生命週期管理

- Python 3.11 [TaskGroup](https://docs.python.org/3.11/library/asyncio-task.html#task-groups)
  會在 scope 結束時等待所有 task，任一 task 非取消例外時取消並等待其餘 task；適合管理
  heartbeat monitor、registration deadline、offline expiry、cleanup 等 Daemon-owned tasks。
- Python 3.11 [Task cancellation](https://docs.python.org/3.11/library/asyncio-task.html#task-cancellation)
  建議用 `try/finally` 做 cleanup，若捕捉 `CancelledError` 通常應在 cleanup 後重新拋出。
- [`asyncio.timeout()`](https://docs.python.org/3.11/library/asyncio-task.html#timeouts)
  是 wait budget，不是 remote outcome oracle；它藉 cancellation 中止本地等待，不能撤銷已送到
  TouchDesigner 的 mutation。
- [`loop.call_soon_threadsafe()`](https://docs.python.org/3.11/library/asyncio-eventloop.html#asyncio.loop.call_soon_threadsafe)
  是其他 OS thread 排程 callback 進 event loop 的官方入口；`asyncio` primitives 本身不支援
  OS thread 同步。
- Python 3.11 [Developing with asyncio](https://docs.python.org/3.11/library/asyncio-dev.html#running-blocking-code)
  要求 blocking code 不直接跑在 event-loop thread，應交由 executor；這適用於同步 SQLite I/O。

因此不應讓 `sio.start_background_task()` 建立的 task 游離於 Daemon lifecycle。建議由一個
runtime owner 集中記錄 task，shutdown 時先停止接單、通知 draining、在 deadline 內等待，
再取消並 await 剩餘 task；每個 task 的 cancellation path 都須可測且不得吞例外。
Python 3.11 文件亦警告 event loop 對未另存 strong reference 的 task 只保留弱引用，因此
fire-and-forget task 必須由 owner 保存，或納入 `TaskGroup`。

## 4. SQLite 持久性與併發

### 官方證據

- Python 3.11 [`sqlite3.connect`](https://docs.python.org/3.11/library/sqlite3.html#sqlite3.connect)
  說明 `check_same_thread=False` 後，跨 thread 寫入可能必須由使用者序列化以避免資料損壞。
- FastAPI 官方 [Path operation functions](https://fastapi.tiangolo.com/async/#path-operation-functions)
  說明普通 `def` endpoint 會在 external threadpool 執行。
- Python 3.11 [Transaction control](https://docs.python.org/3.11/library/sqlite3.html#transaction-control)
  說明 `isolation_level=None` 不會隱式開 transaction，而是 SQLite autocommit。
- Python 3.11 [Connection context manager](https://docs.python.org/3.11/library/sqlite3.html#how-to-use-the-connection-context-manager)
  說明它只 commit/rollback 已開啟 transaction；沒有 transaction 時是 no-op。
- SQLite 官方 [WAL](https://www.sqlite.org/wal.html) 說明 WAL 可讓 reader 與 writer 併行，
  但同一時間仍只有一個 writer；`PRAGMA journal_mode=WAL` 的回傳值才是實際模式，設定可能失敗。
- SQLite 官方 [Transactions](https://www.sqlite.org/lang_transaction.html) 說明同一 database
  同時只能有一個 write transaction；`BEGIN IMMEDIATE` 會立刻開始 write transaction。

### 最小正規修正

1. 讓 `RequestStore` connection 有單一執行緒 owner。正規且可控的方案是由一個專用、
   `max_workers=1` 的 executor 建立並獨占 connection，所有 DB operation 由 async caller
   排入並 await；不要在 event-loop 與 FastAPI 任意 worker thread 間共用同一 connection，
   也不要把 blocking SQLite I/O 直接搬到 event-loop thread。
2. 以明確 `BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK` 包住 schema migration、startup recovery 與
   任何多步狀態轉移。不要在 `isolation_level=None` 下依賴 `with connection:`。
3. 把 read-modify-write 改成條件式 `UPDATE ... WHERE request_id=? AND status IN (...)`，檢查
   rowcount 後讀回；由資料庫原子強制合法轉態，避免遲到 result 覆寫已完成狀態。
4. 啟動時驗證 `journal_mode` 實際回傳 `wal`、`foreign_keys=1`、`quick_check=ok`；保留
   `synchronous=FULL` 與 bounded `busy_timeout`，並對 busy/corrupt/newer-schema 採 fail closed，
   不另建空 DB 或靜默降級。

## 5. 測試原則與成本邊界

IEEE Computer Society 的 [SWEBOK Guide v4.0a，Software Testing](https://ieeecs-media.computer.org/media/education/swebok/swebok-v4.pdf)
提供下列直接準則：unit test 驗證可獨立測試元素；integration test 驗證元素、外部介面與
環境間互動；state-transition testing 依狀態與轉移導出案例；完整 path testing 通常因 loop
不可行；selection/minimization 應在維持 fault-detection effectiveness 時縮小 suite，並移除
冗餘案例；risk-based testing 以 product risk 排定焦點。

套用到本專案：

- 單元測試：Request 狀態轉移、相同 ID/不同 payload 衝突、connection generation guard、
  Agent 去重/result retention、SQLite transaction rollback/cancellation cleanup。
- transport integration：真實 ASGI + `python-socketio` client 驗證 register/heartbeat/FIFO/
  disconnect/reconnect/shutdown；以協定可觀察事件同步，不以固定 `sleep` 當 oracle。
- locked-runtime acceptance：只把無法由 substitute 證明的高風險 seam 放到 TD 2025.32050：
  SocketIO DAT 實際 callback、TD graph mutation、主 thread/frame stall、Agent reload、Daemon
  restart、process kill、結果 replay。這些測試不能被 mock integration 取代。
- production acceptance 同時量測 Derivative 官方 [Perform CHOP](https://docs.derivative.ca/Perform_CHOP)
  可觀察的 FPS/frame time/dropped frames/cook 狀態，以及 queue depth、Command latency、
  disconnect/unknown 計數；不能只有「最終 API 回傳成功」。
- 每一個 reliability test 必須對應一項 invariant 與一個 failure mode。相同狀態邊在不同
  endpoint/Command 上不重複枚舉；低風險純 mapping 以 equivalence partition/boundary case
  取樣，不做全組合爆炸。
- `sleep` 僅可用來觸發真實 deadline；assert 應等待明確 Event/response/state，並有單一較寬的
  測試 deadline。對 retry/backoff 的時間邏輯用可注入 monotonic clock 做 deterministic unit test。

## 6. 複雜度與模組切分

- Parnas 原始論文 [On the Criteria To Be Used in Decomposing Systems into Modules](https://doi.org/10.1145/361598.361623)
  主張模組應隱藏可能變更的設計決策，而非按處理步驟切割；這直接支持讓 Request lifecycle
  owner 隱藏 queues/in-flight/connection generation，storage 隱藏 transaction，HTTP/Socket.IO
  僅作 adapter。
- McCabe 原始論文 [A Complexity Measure](https://doi.org/10.1109/TSE.1976.233837)
  以 control-flow graph 定義 cyclomatic complexity；它量測 decision structure 而非程式行數，
  並將獨立路徑數連到 basis-path testing。應把它當 review trigger，不當品質總分或硬性拆函式器。
- SWEBOK 同時指出 exhaustive path coverage 通常不可行。複雜度門檻應用來找出「狀態轉移與
  transport 細節混在同一函式」的熱點，再依 Parnas 的隱藏決策重構；不可為了壓數字製造
  forwarding wrappers、重複 abstraction 或更多測試。

本專案最值得深化的三個 module seam 是：

1. `RequestLifecycle`：唯一擁有合法轉態、per-Instance FIFO/in-flight、generation guard。
2. `RequestStore`：唯一擁有 schema、transaction、conditional update、recovery/retention。
3. transport adapters：只做 authentication、schema decode/encode、event routing，不自行推導
   lifecycle 或修補結果 shape。

是否拆模組應以「能否把一項會變動且高風險的決策藏在單一介面後」判定；不是以檔案長度判定。

## 限制與不可宣稱事項

- Derivative 官方文件沒有保證 SocketIO DAT callback 的精確 OS thread；本文只採其 callback
  介面與官方 threading 原則。主 thread/frame-stall 結論須在鎖定版做 disposable live probe。
- WebSocket Ping/Pong、Socket.IO connect、`request_accepted` 都不是 Command 完成證明。
- SQLite `FULL`、WAL 與 transaction 只能保護 Daemon store，不能原子化 TouchDesigner graph。
- timeout/cancellation 只能停止本地等待；若 Command 已派送，除非 TD 回傳可驗證終態，結果
  必須是 `unknown`，不能包裝成 failed/retryable。
- 本研究建立原則基線，未對每個 Command 做 WCET、frame-stall 或 crash-injection 量測；這些
  數字只能由鎖定版 acceptance 得出，不能由一般文件推測。
