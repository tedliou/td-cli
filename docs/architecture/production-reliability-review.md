# 生產級通訊與執行可靠性審查

日期：2026-09-01

範圍：CLI、Daemon HTTP／Socket.IO、SQLite Request store、TouchDesigner Agent
Component、測試與 CI。官方／權威依據集中於
[`../research/runtime-reliability-primary-sources.md`](../research/runtime-reliability-primary-sources.md)。

## 判定

目前設計已有正確骨架：Request 先持久化再派送、每個 TouchDesigner Instance
僅一個 in-flight、FIFO、Connection ID 隔離舊連線、Agent 保留未確認結果，以及失聯後
標記 `unknown` 而不自動重做 mutation。這些不變量必須保留。

但目前尚不能宣稱生產級穩定。三項可直接重現的資料／副作用語意缺陷，以及 Daemon
狀態轉移與 TouchDesigner 主執行緒的結構性風險，必須先修正。不要以增加 retry、延長
timeout、吞例外或另走 fallback 通道處理。

## 缺陷與最小正規解法

| 優先級 | 缺陷與有效證據 | 建議解法 | 完成證據 |
| --- | --- | --- | --- |
| P0 | **Request ID 未綁定 Instance。** `app.py` 的重複提交只比較 canonical Command，未比較 `instance_id`。最小重現：相同 ID／Command 改投另一 Instance，回 `200`，內容仍指向第一個 Instance。 | 冪等身分應涵蓋完整不可變 envelope：`request_id + instance_id + canonical command`。同 ID 只要 Instance 或 Command 不同，一律 `409 request_id_conflict`；Daemon 與 Agent 共用同一規則。 | management integration test 覆蓋同／異 Instance 與同／異 Command 的四格狀態表。 |
| P0 | **mutation batch 會留下不可定位的部分副作用。** `parameters.set`／`parameters.pulse` 可進 `batch.execute`；第二項 runtime write 失敗時，第一項已成功，整體只回單一錯誤。最小重現得到 `parameter_write_rejected`，第一值已由 `0.0` 變 `1.0`。舊 acceptance 雖稱 batch 非原子，但呼叫端仍無逐項終態。 | 成本最低且可控的作法是把所有 mutation 移出 batch，只允許 read-only Command。若產品確實需要非原子批次，須另立明確 Interface，回傳每項結果與 `partial`，且禁止把整批描述為交易；不要加入通用 rollback framework。 | protocol test 鎖定 batchable 集合全為 read-only；locked TD 驗證中途 read failure 不產生任何 mutation。 |
| P0 | **SQLite 轉態不是原子交易。** connection 使用 `isolation_level=None`，但 `recover()`／`update()` 仍依賴 `with connection:`；官方文件指出 autocommit 下 context manager 不會替它開 transaction。`update()` 又是分離的 read-modify-write。 | 讓 `RequestStore` 成為單一 connection owner；以單一專用 worker 序列化 DB 操作。schema、recovery 與多步轉態使用明確 `BEGIN IMMEDIATE`／commit／rollback；狀態轉移改成帶舊狀態條件的原子 `UPDATE`，用 rowcount 判定競爭。啟動時驗證 WAL 實際生效。 | 真實 subprocess 強制終止 Daemon，再啟動驗證 queued／dispatched／running recovery；併發 submit/result/cleanup 故障注入不得遺失或倒退狀態。 |
| P1 | **同一 SQLite connection 跨 event loop 與 FastAPI thread pool。** 普通 `def` query endpoint 會進 thread pool，Socket.IO／submit 則從 event loop 存取；`check_same_thread=False` 只解除檢查，不提供應用層轉態序列化。 | 與上一項合併修正，由 `RequestStore` 的小 Interface 隱藏執行緒與 transaction；HTTP／Socket.IO adapter 不直接拿 connection。 | 壓測 GET polling 與結果寫入並行，以合法狀態不變量、SQLite error 計數與完整 Request 數作 oracle。 |
| P1 | **`request_accepted` 實際在 Command 執行完成後才送出。** `socket_callbacks.py` 先同步呼叫 `agent.accept()`（內含完整 TD mutation），回來後才 emit accepted；`started_at` 因而不是開始時間。長 Command 同時阻塞 callback、timeline 與 application heartbeat，固定 6 秒 heartbeat 可造成假 Offline／`unknown`。 | 將 Agent 執行深化成 `begin(request) -> accepted` 與 `execute(request_id) -> retained outcome`：先驗證、記錄去重狀態並回 accepted，再在 TD 主執行緒的正式 Execute DAT／end-frame seam 執行。以鎖定版量測為每類 Command 設有界 execution lease；Daemon 在 lease 內不以缺 heartbeat 判死，超時仍只能是 `unknown`。不可用背景 thread 操作 TD objects。 | TD 2025.32050 實機量測每類 Command wall time、frame stall、FPS/drop；測試 accepted 時序、執行中斷線、lease 到期與 result replay。 |
| P1 | **Agent 去重資料無界且確認後可再次執行同 ID。** `pending_results` 收到確認後會刪除，但 `seen_commands` 從不清理；最小重現 1,000 次已確認 Request 後為 `pending=0, seen=1000`。相同 ID 已確認後再次進 `accept()` 也會落回執行路徑。 | Daemon 已是持久去重 owner，且 server-to-client transport 不會自動重送，因此最小修正是 `result_recorded` 後同時刪除 canonical command；只在 `pending_results` 尚未確認時保留去重資料。若威脅模型要求防禦 Daemon 之外的重播，再加固定容量 tombstone，命中時只拒絕、絕不重做；不要複製一份七日結果庫到 Agent。 | 長時間 soak test 後記憶體有界；pending duplicate 回播原結果，acknowledged ID 不會由正常 Daemon 再派送。 |
| P1 | **同一 SID 的 outbound emit 沒有共同序列化。** dispatch、heartbeat reply、draining、registration 等可由不同 task 發送；`python-socketio` 官方明載同 connection 的 concurrent `emit()` 非 concurrency-safe。背景 task 亦未集中追蹤／await。 | Runtime 為每個 connection 擁有 outbound serializer（例如 `asyncio.Lock`）與所有 lifecycle task；所有 emit 經同一方法。用 `TaskGroup` 或等價 owner 在 shutdown 時 cancel 並 await，例外不得成為游離 task。 | 故障注入同時觸發 heartbeat、dispatch、drain、replacement registration，驗證封包順序、無遺留 task、無未處理例外。 |
| P1 | **timeline 暫停會卡住 drain 後恢復。** `resumeAfterDraining` 以 `run(delayMilliSeconds=...)` 排程但沒有 `delayRef`；Derivative 官方說預設 delay 在 timeline pause 時不前進。乾淨 drain 會先把 SocketIO DAT `active=False`，因此 pause 中可能無法重新啟用。 | 依官方模式指定獨立時間參考（鎖定版驗證 `delayRef=op.TDResources`），並將 pause／play 納入 daemon restart acceptance。不要用額外 reconnect fallback 掩蓋 timer 錯誤。 | TD timeline 暫停時 stop/start Daemon；Agent 必須在有界時間內重新 Online，Instance ID 保持、Connection ID 更新。 |
| P2 | **transport 承擔 contract normalization。** `_normalize_command_result()` cyclomatic complexity 32，且列舉大量 Command 特例；新增 Command 容易漏掉 transport 特例，使 Pydantic contract 與 wire 修補漂移。 | 把結果正規化放回 `COMMAND_CATALOG` 的 Interface，由每個 Command definition 擁有 input/result facts；transport 只 decode wire sentinel 並路由。這是已有多個 caller/test 的真實 seam，不新增通用 framework。 | 每個 Command definition 的 contract test；刪除 transport 的 command-name 分支後，既有 locked-wire cases 全過。 |
| P2 | **Daemon runtime 是過淺的 closure 集群。** `create_transport_app()` cyclomatic complexity 67、168 statements，混合 registration、queue、in-flight、heartbeat、disconnect、shutdown 與 persistence。這是狀態機難以窮舉的訊號，不只是檔案長。 | 建立一個深的 `RequestLifecycle` module，唯一擁有合法轉態、per-Instance FIFO、in-flight、generation guard 與 task；HTTP／Socket.IO 保持 adapter。避免逐 handler class、repository interface 或只有一個 adapter 的假 seam。 | 測試只經 `RequestLifecycle` Interface 驗證狀態轉移；transport tests 留 schema/auth/event wiring，不重複整套狀態矩陣。 |
| P2 | **可觀測性不足。** Daemon log 目前主要只有 start/stop；Request transition、connection generation、disconnect 原因、queue depth、latency、SQLite busy/corruption 與 unknown 計數沒有一致事件。 | 在 lifecycle/store 實際決策點輸出有界結構化事件：request/instance/connection ID、from/to status、code、latency、queue depth；永不記 command payload、token 或 TD content。健康檢查需反映 store/task owner 是否健康。 | 失聯與 crash acceptance 可由 log 重建單一 Request 時序；敏感值 redaction test 與 log rotation 維持通過。 |

## 測試投資順序

1. **先補 P0 狀態轉移測試**：它們便宜、deterministic，且目前 398 項綠燈仍漏掉實際缺陷。
2. **新增真正的 crash integration**：目前 restart test 經 TestClient 正常關閉，`close()` 本身會先
   `recover()`，不能證明 abrupt process death。以 subprocess、明確 ready event 與強制 terminate
   驗證 WAL/recovery；不以固定 sleep 判斷完成。
3. **Socket.IO 故障矩陣只測獨特狀態邊**：dispatch 前、accepted 後、mutation 後 result 前、
   result 後 ack 前，各做一次 disconnect；再測舊 generation 遲到事件。不同 Command 不重複同一矩陣。
4. **鎖定版 TD acceptance 只負責 substitute 證明不了的事**：主執行緒、SocketIO DAT callback、
   timeline pause、真正 graph mutation、Agent reload、Daemon kill/restart、frame stall。低風險純 mapping
   留在單元測試。
5. **移除 CI 重複執行**：workflow 已跑完整 `pytest -q`，隨後又重跑三個已包含的測試檔；改成一次
   suite，或拆成互斥 marker/job。這不降低 fault detection，只移除冗餘成本。

## 不做的事

- 不宣稱 exactly-once；TD process 在 mutation 後、結果保存前 crash，仍存在不可消除的 `unknown`。
- 不自動重試未知 mutation、不建立第二條 WebSocket/TCP fallback、不以加長 timeout 取代 execution lease。
- 不把所有 Command 做全組合、全 path 或 rollback；依 Request lifecycle 與高風險 mutation 分配成本。
- 不為降低複雜度數字製造 forwarding wrapper。模組切分只隱藏會變動且高風險的設計決策。

## 建議交付順序

1. P0：Request identity、read-only batch、transactional `RequestStore`。
2. P1：Agent accept/execute 時序與有界去重；connection emit/task owner；pause-safe drain。
3. P2：抽出 `RequestLifecycle`、集中 Command result contract、補可觀測性、精簡 CI。
4. 全部自動門檻通過後，才在 TouchDesigner 2025.32050 重建 Agent Artifact，執行上述 live
   failure matrix；以量測結果設定 execution lease 與 frame budget，不先猜常數。

不建議目前另建通用 testing skill：本輪原則已可直接落在 repo 文件與 CI，且專案剛移除／忽略
project-local Codex 設定。等上述故障矩陣至少被第二個專案或第二輪 release 重用，再把穩定的
流程抽成 skill，避免先建立只有一個 caller 的淺 Interface。
