# 既有自訂 Menu 選項更新驗收

日期：2026-09-28。Issue: https://github.com/tedliou/td-cli/issues/145 。

## 缺口與介面

Collective Dream Factory 需要把既有自訂 Menu `/project1/dream_controls` 的 `Scene` 改名（30 項，controller 只讀 `menuIndex`）。原本只有 `parameters page-create` 能在新建頁面時定義選項；`parameters set` 只改值、expression、export 或 bind。這是能力缺口，不是失敗的 Request。

新增 Command `parameters.menu.set` 與 `td parameters menu-set`：

- 輸入 `operator_path`、`parameter`、`menu_names`、`menu_labels`、必填的 `preserve`（`index`／`name`）。names 與 labels 各 1–32 項、等長、每項 1–128 字元；names 唯一。驗證與 `page-create` 共用 `_valid_menu_items`。
- 只接受自訂、constant mode、未設定 `menuSource` 的 `Menu` 參數。內建、`menuSource` 與非 constant mode 回傳新錯誤碼 `parameter_menu_not_writable`；其他樣式（含 StrMenu）回傳 `parameter_type_unsupported`；目前值或 default 無法依規則對應時回傳 `parameter_value_invalid`。皆在修改前拒絕。
- default 依同一規則對應；原本就不在 names 中的 default 保持不變。只在讀到的 default 與目標不同時才寫入，所以孤兒 default 在前進與回復時都不會被寫。
- 結果：新的 `menu_names`／`menu_labels`，以及 `before`／`after` 的 `value`、`index`、`default`。
- 寫入失敗時復原 names、labels、default 與值並完整讀回，重用 `parameter_write_rejected`／`parameter_rollback_failed`／`parameter_outcome_unknown`。回復前會比照 `parameters.set` 重新查找：Operator 消失、路徑不符、`id` 不同（同路徑被替換），或 Par 已不存在，都回報 `parameter_outcome_unknown`，不會寫到別的目標上。不自動重送。
- mutation，可放入 `commands execute` 計畫，不可放入 `batch execute`。Request lifecycle、FIFO、transport、Protocol 版本皆不變；舊 Agent 未登錄此 capability。

## TD 原生語意（決定寫入順序）

[runtime-shape.json](evidence/menu-options/runtime-shape.json)：TD 2025.32050 disposable project（無 Agent、無 Daemon），PID 492 自行退出。

- 設定 `menuNames` 時 TD 依索引保留目前值；明確設定過的 `default` 不跟著改，會留下不在 names 中的 default。
- 較長的 `menuNames` 會與目前 labels 配對並截短到較短者；之後設較長的 labels，補上項目的名稱取自 labels（讀回 `x,y,Z,W`）。因此寫入順序為 names → labels → names，再寫 default 與值，最後完整讀回。
- 內建參數設定 `menuNames` 會由 TD 拋出 `Custom menu parameter expected`；有 `menuSource` 的自訂 menu 會靜默忽略 names。
- 值不在 names 中時，`menuIndex` 可能為 `None`；實作將其視為無法對應並拒絕，不讓型別錯誤變成未知結果。

[default-shape.json](evidence/menu-options/default-shape.json)（PID 23492 自行退出）補充 default 語意：

- 指定不存在於 names 的 default 會原樣保存（`missing` 讀回 `missing`），不像 `val` 會被改成第 0 項。
- 從未明確設定的 default 回報目前第 0 項；names 重排後跟著變成新的第 0 項。明確設定過的 default 則不跟著變。

## 隔離 locked 驗收

TD 2025.32050，以官方 Samples `Setup/Base/NewProject.toe` 經 `toeexpand`，在 `/project1` 加入 Execute DAT（內嵌文字以 `runpy` 執行 `tools/locked_menu_options_probe.py`，TOC 以 LF 寫入），`toecollapse` exit 0、無警告、產物 954 bytes 後才啟動。不載入 Agent、不連 Daemon、不開啟或修改作品；作者的作品 TD（PID 23488）全程未觸碰。

探針以 `runpy` 載入 `agent/extension.py`，對真實 TD objects 呼叫公開 handler `OperatorControl.execute`。最新一輪使用 0.8.0 後續修正（分支 `claude/menu-set-followups`）的 working copy，SHA-256 `26e95131f8e32d27fa19b4aabca2a9a27fcb905c8a938f4b75c9f4c64c957cfe`：mutate PID 24064、reload PID 19992，觀測值與前兩輪（第一版 `9df3302` PID 20704／24884；review 修正 `59e58c3` PID 27104／26588）完全相同。

[acceptance.json](evidence/menu-options/acceptance.json)（mutate，PID 24064 自行退出）：

- 以 `parameters.page.create` 建立與作品相同的 30 項 `Scene`（names `D10,D02,…`，labels `01 D10 場景01` 形式），`parameters.set` 選到第 5 項 `D28`。
- `preserve=index` 改為 `D01…D30`、labels `01 場景01`：before `D28`/4/default `D10`，after `D05`/4/default `D01`。直接讀 TD、`parameters.list`、`parameters.get` 三者一致。
- 3 → 30 項擴充與 30 → 2 項縮減：names、labels 精確讀回，索引 1 保留。
- `preserve=name` 把 30 項倒序：值 `D28` 由索引 4 變 25，default `D10` 不變。
- 內建 Noise TOP `type`、`menuSource` menu、expression mode menu 回傳 `parameter_menu_not_writable`；StrMenu 回傳 `parameter_type_unsupported`；索引超出與名稱不存在回傳 `parameter_value_invalid`。六項皆確認狀態未變。
- 孤兒 default：names 改名後 default 留在 `b`，再以 `preserve=index` 改為 `p,q,r`：值 `z`@2 → `r`@2，default 仍為 `b`，讀回一致。
- 未明確設定的 default（回報 `a`）以 `preserve=name` 重排為 `c,a,b`：值 `b` 由索引 1 變 2，default 釘在 `a`，讀回一致。
- 刪除暫時的負面案例參數後，`project.save` 另存為 disposable `menu-options-saved.toe`（SHA-256 `c2c9d07971b59ae26470925bdb540bd896856c99de410c2365d559278cf2c04e`）。

[reload.json](evidence/menu-options/reload.json)（PID 19992 自行退出）：冷開另存的專案，`Scene`、`Reorder`、`Small`、`Orphan`、`Implicit` 的 names、labels、值、default、索引與存檔前完全一致。

寫入中途失敗、回復失敗與目標消失無法在原生 TD 安全注入，只由 `tests/test_custom_menu_options.py` 的替身測試涵蓋，沒有原生證據。替身依上述實測語意建模，涵蓋：3→3、3→30、3→2 在 labels 寫入後才失敗；寫入沒有拋例外但讀回不符；回復寫入失敗與回復讀回不符；寫入後與回復途中目標消失；Operator 被同路徑替換；Par 被刪除。

## 自動測試

`tests/test_custom_menu_options.py`（Command catalog、`CommandPlan`、Agent `OperatorControl`）的 17 項測試先全部失敗，實作後通過。review 後新增的孤兒 default（不寫入）、回復到被替換的 Operator、回復到被刪除的 Par 四項測試，在修正前的 handler 上失敗，修正後通過；其餘新增的失敗注入案例在修正前後都通過，用來補足涵蓋。`tests/test_control_cli.py` 的 CLI 組裝與拒收案例在 CLI 實作後補上。locked 驗收發現 `menuIndex` 為 `None` 的情況後，先補上會失敗的測試再修正。

完整 local gate：pytest 637 passed，另有 `tests/test_installer_licenses.py::test_installer_verifies_nested_license_files_on_same_version` 在本機失敗：PowerShell 以系統 code page 輸出 stderr，測試用 UTF-8 解碼，所以失敗。乾淨的 `origin/develop`（`2b39224`）同樣失敗，與本變更無關。ruff check、ruff format --check、mypy src、uv lock --check、`agent_tool inspect-source agent`、git diff --check 全部通過。

## 0.8.0 發布後的修正

發布後複核提出兩點，已在後續 PR 修正；0.8.0 的 Agent 仍是修正前的行為，會隨下一版發布：

- **未指定的 default：** [default-shape.json](evidence/menu-options/default-shape.json) 顯示，從未指定的 default 會回報目前第一個選項，並跟著 names 移動；明確指定過的 default 不會移動。規則需要移動未指定的 default 時，handler 必須明確寫入，而 TouchDesigner 沒有介面能把它還原成未指定。修正後，寫入前在 names 寫完時讀 default，若它已經隨 names 移動，就代表它原本未指定。只要嘗試寫入過這種 default，之後的任何失敗即使讀回完全等於原狀，也回報 `parameter_rollback_failed`，不再回報 `parameter_write_rejected`。寫入 default 本身拋出例外時也一樣：無法確定 TD 是否已經套用，所以採保守回報。不需要移動的未指定 default 不會被寫入，失敗時照常完整還原。成功的寫入本來就依規則回報新的 default，這時它變成明確指定，是預期結果。
- **回復時的目標身分：** 回復改用修改前記下的路徑與 `id`，不再從原 Operator 物件重新讀取。已刪除的 OP 包裝物件在讀取屬性時可能拋出例外，這時改為回報 `parameter_outcome_unknown`，而不是一般例外路徑。這一點只由替身測試證明，沒有原生注入證據。

## 限制

這一輪只驗證 source handler 在原生 TD 的行為。新版 Agent artifact 尚未建置、發布或安裝。作品的內嵌 Agent 仍是 0.7.0，必須用正式的 `td-agent upgrade-project` 離線升級才能使用此 Command；部署與作品回歸另行記錄。
