# events.read 錯誤文字驗收

日期：2026-09-23。Issue: https://github.com/tedliou/td-cli/issues/124 。

## 根因與介面

官方 [OP Class](https://docs.derivative.ca/OP_Class) 定義 `errors(recurse=False) → str`（查閱 2026-09-23）。`AgentExt._events` 卻把回傳值當 list 迭代，得到逐字元 list 並只取前 100 個，錯誤不可讀且被截斷。既有測試替身回傳 list，未代表 TD 邊界。

官方未規定字串內部格式。locked 實測（[runtime-shape.json](evidence/events-error-text/runtime-shape.json)）顯示單一錯誤本身可跨多行（NameError 的 `, line 1, in <module>`、SyntaxError 的原始碼與插入符號），無可靠的訊息分隔符。因此不以換行或路徑前綴猜測切分訊息，也不補造 traceback。

`events.read` 結果改為：

- `errors`：TD 原生遞迴錯誤文字，逐字保留；無錯誤或 `include_errors=false` 時為 `""`。
- `errors_truncated`：超過 `AgentExt.MAX_ERROR_TEXT_BYTES`（16 KiB UTF-8）時為 `true`，保留前綴並截在完整字元邊界。

超界採截斷而非 `result_too_large`：錯誤是診斷附帶資訊，不應使同一次讀取的 event cursor 失敗。16 KiB 與單一 Table DAT cell 上限一致，遠低於 24 KiB outcome chunk 與 256 KiB 結果上限。

行為變更：`errors` 型別由 `list[str]` 改為 `str`。舊值為逐字元 list，無可用的既有語意可保留。Event ring、cursor、Request lifecycle、transport 與 Protocol 訊息皆不變。

## 隔離 locked 驗收

TD 2025.32050，以官方 Samples `NewProject.toe` 經 `toeexpand` 加入 Execute DAT（`file` 指向 `tools/locked_events_errors_probe.py`，LF toc）再 `toecollapse`，不載入 Agent、不連 Daemon、不開啟任何作品，結束時 `project.quit(force=True)` 不存檔。

- 形狀探測 PID 20012、正式 handler 驗收 PID 22036 均自行退出。
- 對實際 root Operator 呼叫 `AgentExt._events`：三個錯誤（Script DAT、Python SyntaxError、參數 NameError）逐字保留，`errors_truncated=false`。
- 200 個錯誤 Constant CHOP 使原生文字達 36,161 bytes：結果 16,384 bytes、為原文前綴、`errors_truncated=true`。
- 精確來源 SHA-256 與觀測值見 [acceptance.json](evidence/events-error-text/acceptance.json)。

## 自動測試

測試替身改為依官方型別回傳 `str`，並以 locked 實測字串形狀為 fixture。新增逐字保留、UTF-8 邊界截斷（含恰好等於上限不截斷）、未要求錯誤時為空字串三項測試，先紅後綠。

## 限制

此階段證明 source handler 在原生 TD 的行為；新版 Agent artifact 尚未建置、發布或安裝，部署驗收另行記錄。`warnings()`、`scriptErrors()` 與 Textport／Console 輸出不在本介面範圍。
