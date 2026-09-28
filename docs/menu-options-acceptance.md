# 既有自訂 Menu 選項更新驗收

日期：2026-09-28。Issue: https://github.com/tedliou/td-cli/issues/145 。

## 缺口與介面

Collective Dream Factory 需要把既有自訂 Menu `/project1/dream_controls` 的 `Scene` 改名（30 項，controller 只讀 `menuIndex`）。原本只有 `parameters page-create` 能在新建頁面時定義選項；`parameters set` 只改值、expression、export 或 bind。這是能力缺口，不是失敗的 Request。

新增 Command `parameters.menu.set` 與 `td parameters menu-set`：

- 輸入 `operator_path`、`parameter`、`menu_names`、`menu_labels`、必填的 `preserve`（`index`／`name`）。names 與 labels 各 1–32 項、等長、每項 1–128 字元；names 唯一。驗證與 `page-create` 共用 `_valid_menu_items`。
- 只接受自訂、constant mode、未設定 `menuSource` 的 `Menu` 參數。內建、`menuSource` 與非 constant mode 回傳新錯誤碼 `parameter_menu_not_writable`；其他樣式（含 StrMenu）回傳 `parameter_type_unsupported`；目前值或 default 無法依規則對應時回傳 `parameter_value_invalid`。皆在修改前拒絕。
- default 依同一規則對應；原本就不在 names 中的 default 保持不變。
- 結果：新的 `menu_names`／`menu_labels`，以及 `before`／`after` 的 `value`、`index`、`default`。
- 寫入失敗時復原 names、labels、default 與值並完整讀回，重用 `parameter_write_rejected`／`parameter_rollback_failed`／`parameter_outcome_unknown`。不自動重送。
- mutation，可放入 `commands execute` 計畫，不可放入 `batch execute`。Request lifecycle、FIFO、transport、Protocol 版本皆不變；舊 Agent 未登錄此 capability。

## TD 原生語意（決定寫入順序）

[runtime-shape.json](evidence/menu-options/runtime-shape.json)：TD 2025.32050 disposable project（無 Agent、無 Daemon），PID 492 自行退出。

- 設定 `menuNames` 時 TD 依索引保留目前值；明確設定過的 `default` 不跟著改，會留下不在 names 中的 default。
- 較長的 `menuNames` 會與目前 labels 配對並截短到較短者；之後設較長的 labels，補上項目的名稱取自 labels（讀回 `x,y,Z,W`）。因此寫入順序為 names → labels → names，再寫 default 與值，最後完整讀回。
- 內建參數設定 `menuNames` 會由 TD 拋出 `Custom menu parameter expected`；有 `menuSource` 的自訂 menu 會靜默忽略 names。
- 值不在 names 中時，`menuIndex` 可能為 `None`；實作將其視為無法對應並拒絕，不讓型別錯誤變成未知結果。

## 隔離 locked 驗收

TD 2025.32050，以官方 Samples `Setup/Base/NewProject.toe` 經 `toeexpand`，在 `/project1` 加入 Execute DAT（內嵌文字以 `runpy` 執行 `tools/locked_menu_options_probe.py`，TOC 以 LF 寫入），`toecollapse` exit 0、無警告、產物 954 bytes 後才啟動。不載入 Agent、不連 Daemon、不開啟或修改作品；作者的作品 TD（PID 23488）全程未觸碰。

探針以 `runpy` 載入 `agent/extension.py`（working copy SHA-256 `28c69c682ce1e083acc4c0bc9d16b7bf405871ac76d47d1e485924eeaf40ad50`，commit `9df3302` 的 blob `17d8adb3ac62457da6ab360392d99514cc4f1724`），對真實 TD objects 呼叫公開 handler `OperatorControl.execute`。

[acceptance.json](evidence/menu-options/acceptance.json)（mutate，PID 20704 自行退出）：

- 以 `parameters.page.create` 建立與作品相同的 30 項 `Scene`（names `D10,D02,…`，labels `01 D10 場景01` 形式），`parameters.set` 選到第 5 項 `D28`。
- `preserve=index` 改為 `D01…D30`、labels `01 場景01`：before `D28`/4/default `D10`，after `D05`/4/default `D01`。直接讀 TD、`parameters.list`、`parameters.get` 三者一致。
- 3 → 30 項擴充與 30 → 2 項縮減：names、labels 精確讀回，索引 1 保留。
- `preserve=name` 把 30 項倒序：值 `D28` 由索引 4 變 25，default `D10` 不變。
- 內建 Noise TOP `type`、`menuSource` menu、expression mode menu 回傳 `parameter_menu_not_writable`；StrMenu 回傳 `parameter_type_unsupported`；索引超出與名稱不存在回傳 `parameter_value_invalid`。六項皆確認狀態未變。
- 刪除暫時的負面案例參數後，`project.save` 另存為 disposable `menu-options-saved.toe`（SHA-256 `32b67e4e9edda3a728988b36f2487c93666e7cd6a6c9b0502a0e60e3f2d2a33b`）。

[reload.json](evidence/menu-options/reload.json)（PID 24884 自行退出）：冷開另存的專案，`Scene`、`Reorder`、`Small` 的 names、labels、值、default、索引與存檔前完全一致。

寫入中途失敗的回復、回復失敗與目標消失無法在原生 TD 安全注入，由 `tests/test_custom_menu_options.py` 以依上述原生語意建立的替身驗證。

## 自動測試

`tests/test_custom_menu_options.py`（Command catalog、`CommandPlan`、Agent `OperatorControl`）的 17 項測試先全部失敗，實作後通過。`tests/test_control_cli.py` 的 CLI 組裝與拒收案例在 CLI 實作後補上。locked 驗收發現 `menuIndex` 為 `None` 的情況後，先補上會失敗的測試再修正。

完整 local gate：pytest 624 passed，另有 `tests/test_installer_licenses.py::test_installer_verifies_nested_license_files_on_same_version` 在本機失敗：PowerShell 以系統 code page 輸出 stderr，測試用 UTF-8 解碼，所以失敗。乾淨的 `origin/develop`（`2b39224`）同樣失敗，與本變更無關。ruff check、ruff format --check、mypy src、uv lock --check、`agent_tool inspect-source agent`、git diff --check 全部通過。

## 限制

這一輪只驗證 source handler 在原生 TD 的行為。新版 Agent artifact 尚未建置、發布或安裝。作品的內嵌 Agent 仍是 0.7.0，必須用正式的 `td-agent upgrade-project` 離線升級才能使用此 Command；部署與作品回歸另行記錄。
