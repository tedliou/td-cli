# 自訂 Menu 256 項上限驗收

日期：2026-09-30。Issue: https://github.com/tedliou/td-cli/issues/151 。

## 缺口

Collective Dream Factory 的 `/project1/dream_controls` `Scene` 需要 38 項。CLI／Daemon 0.8.0 的 Command catalog 把 `parameters.menu.set` 與 `parameters.page.create` 的 menu 定義限制為 32 項，所以 `td parameters menu-set` 與 `commands execute` 都在送出前以 `invalid_arguments` 拒絕（`details` 為空，沒有 Request）。Daemon 的 `SubmitRequest` 用同一個 `Command` 模型，所以 CLI 與 Daemon 必須同版。Agent 沒有項目數上限，TouchDesigner 也沒有。

## 介面

- `MAX_MENU_ITEMS` 32 → 256：兩個 Command 的 menu names／labels 各 1–256 項；每項 1–128 字元、names 唯一、等長不變。
- `parameters.menu.set` 新增 names 加 labels 的 `json.dumps(ensure_ascii=True)` ≤ 65,536 bytes（上界包含在內）。outcome 以同一種編碼回傳 names／labels 一次，所以最壞約 66 KB，遠低於 256 KiB 的 outcome 上限，TD 已改好的 mutation 不會因為 outcome 過大而被回報為 `unknown`。`parameters.page.create` 沿用既有的整頁 16,384 bytes 預算。
- Command 或計畫驗證失敗時，`invalid_arguments` 的 `details` 包含 `validation_errors`（最多 8 筆：`location`、`type`、`message`，不回傳輸入值）與 `validation_errors_truncated`。在計畫中，`location` 以 `["commands", <index>, ...]` 開頭。
- Protocol、Request lifecycle、transport、Agent 都不變。

## Locked 驗收（真實 Daemon transport 與 SocketIO DAT）

`tools/locked_menu_limit_acceptance.py`（commit `df058b6`，產品程式碼與 `f98bc96` 相同）執行：

- 以本分支的 `create_transport_app` 在 `127.0.0.1:19982` 啟動隔離的 acceptance Daemon。data root 位於 scratch `LOCALAPPDATA`（使用者 temp 目錄；放在 E: 時，`secure_layout` 會因 ACL 過寬而拒絕，這是既有的保護）。
- disposable project 以官方 Samples `NewProject.toe` 加 Execute DAT 建成：TOC 以 LF 寫入，`toecollapse` exit 0、無警告，產物 954 bytes。TD 2025.32050 PID 28048 以 scratch `LOCALAPPDATA` 啟動，執行 `tools/locked_menu_limit_probe.py`，`loadTox` 載入**已發布的 Agent 0.8.0 artifact**（`%LOCALAPPDATA%\Programs\touchdesigner-cli\current\td-agent.tox`，SHA-256 `625de7737adab5d5c1e5792c6945c6fba2e10c4b65b544a5d9f1e76f434a420b`，source commit `a7cd009`，與作品內嵌的 Agent 同版）。
- scratch 內沒有 token，所以 Agent 停在 `waiting_for_daemon`，socket 未啟用。probe 把 `socketio1.par.url` 改為 19982 之後，harness 才建立 token 並啟動 Daemon，Agent 隨即連線（Instance online，agent_version 0.8.0）。使用者的 Daemon（9982）、data root 與作品 TD（PID 2780）全程未觸碰。
- 所有 Command 都由 `DaemonClient` 經 HTTP → Daemon → SocketIO DAT → Agent 執行。harness 寫入 `done` 後，TD 自行退出（exit 0），acceptance Daemon 的 thread 也已停止。

[acceptance-agent-0.8.0.json](evidence/menu-limit/acceptance-agent-0.8.0.json)：共 19 個 Request，全部 `succeeded`，每個約 0.1 秒。

| 步驟 | 項目 | names＋labels JSON | dispatch Command | menu.set 結果 | before → after（值@索引） |
| --- | --- | --- | --- | --- | --- |
| 30 → 38（作品的 38 個 labels），`index` | 38 | 1,441 | 1,514 | 1,576 | `D05`@4 → `D05`@4 |
| 38 → 256，`name` | 256 | 10,141 | 9,777 | 9,839 | `D05`@4 → `D05`@4 |
| 256 項 CJK，剛好在預算上，`index` | 256 | 65,536 | 65,173 | 65,245 | `D05`@4 → `D05xxxxx`@4 |
| 256 項 astral（每字元 12 bytes），剛好在預算上，`index` | 256 | 65,536 | 65,173 | 65,255 | 保持 @4 |
| 256 → 38，`index` | 38 | 1,441 | 1,514 | 1,586 | `D05xxxxx`@4 → `D05`@4 |

每一步的 menu.set 結果中，names／labels 都與輸入相同；`parameters.list` 讀回的 `Scene` names／labels 與輸入完全一致；`parameters.get` 的值等於結果的 `after.value`。預算上的 `parameters.list` 結果為 80,062 bytes，以分塊 outcome 傳回。最後以 `ops.destroy` 刪除測試 COMP。

同一個 harness 以本分支的真實 CLI 執行 257 項的 `td --json parameters menu-set --input-file` 與 `td --json commands execute --input-file`，兩者都在連線前 exit 2，回傳 `invalid_arguments`。`validation_errors` 分別為 `["menu_names"]`／`["menu_labels"]` 與 `["commands",0,"menu_names"]`／`["commands",0,"menu_labels"]`，類型都是 `too_long`，訊息為「at most 256 items … not 257」。沒有產生 Request。

## 自動測試

`tests/test_menu_item_limit.py`：33／38／256 項在 Command、`CommandPlan` 兩條路徑都接受；257 項拒絕；65,536 bytes 接受、65,537 bytes 拒絕；`page-create` 256／257；`read_plan` 與 CLI（`CliRunner`）的 `validation_errors` 位置、類型、訊息，以及不回傳輸入值；details 最多 8 筆並標示截斷。新測試在實作前因為缺少 `MAX_MENU_JSON_BYTES` 而失敗，實作後通過。`tests/test_custom_menu_options.py` 原本的 32 項邊界案例改為 257。

完整 local gate：pytest 660 passed，另有 1 個既有的本機失敗 `tests/test_installer_licenses.py::test_installer_verifies_nested_license_files_on_same_version`（PowerShell 以系統 code page 輸出 stderr，與本變更無關，0.8.0 驗收時已記錄）。ruff check、ruff format --check、mypy src、uv lock --check、`agent_tool inspect-source agent`、git diff --check 全部通過。

## 限制

- 只用已發布的 Agent 0.8.0 artifact 驗證。本分支沒有修改 Agent；下一版的 Agent artifact（包含 develop 上 #150 的 handler 修正）會在發版 staging 時另行建置與驗證。
- 作品需要的是 CLI 與 Daemon 升級到含本變更的版本。作品內嵌的 Agent 0.8.0 已經能執行 38 項，不需要 `td-agent upgrade-project`，也不需要關閉 TD。
