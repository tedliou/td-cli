# 官方 Palette TOX 匯入驗收

日期：2026-10-07。Issue: https://github.com/tedliou/td-cli/issues/157 。

## 缺口與根因

Collective Dream Factory 要匯入 TouchDesigner 2025.32050 官方 Palette 的 `Mapping/projectorBlend.tox` 與 `Mapping/kantanMapper.tox`。td／Agent 0.9.0 都回 `tox_verification_failed`，`details` 為空。在 disposable TD 中逐項執行 canonical `OperatorControl` 的驗證後確認：

1. `_require_tox_snapshot` 把 `enableexternaltox=on` 視為外部連結。這是 TD 2025 COMP 的預設值，`.tox` 只存非預設值，所以巢狀 COMP 載入後都是 on；`externaltox` 為空時這個開關不起作用。projectorBlend 的 `projectorBlend`、`docsHelper` 與 kantanMapper 的 60 個以上 COMP 都因此被拒。
2. kantanMapper 有 4,080 個 Operator（內層 4,077）。`max_operators` 上限 1,000，snapshot 另外寫死 1,000，完整 inventory 也會超過 256 KiB 的 outcome 上限。
3. 型別全部在 catalog 的 supported／conditional 內，名稱都合法，沒有 VFS，也沒有非空的 `externaltox`。
4. Palette TOX 的 root 是包裝，內含 `icon`（opviewerTOP）與同名的元件。TD Palette 的 drag 腳本（`ui/dialogs/palette`）在 Derivative 資料夾時，若 `component.op(component.name)` 存在就放入內層元件。
5. 驗收時另外發現 `ops.connect` 比對的是兩端 Operator 的 family，所以 TOP 接不進元件的 In TOP，元件的 Out TOP 也接不到 TOP。

## 介面

- 只有非空的 `externaltox` 或 `subcompname` 算外部連結；VFS 非空仍拒絕。匯入不清除、不改寫任何參數。Palette 元件本身沒有外部連結與 VFS，執行期不依賴網路。
- `tox_verification_failed.details`：`check`（`operator_limit`、`operator_type`、`operator_name`、`external_tox`、`vfs`、`load_shape`、`root_child`、`inspection`），視情況附 `relative_path`（相對於受驗證元件，最長 512 字元）、`op_type`、`parameter`、`limit`。不回傳參數值。Agent 其他錯誤的 `details` 不變。
- `root_child`（`--root-child`）：安裝載入 root 底下同名的直屬 COMP。
- `inventory`（`--inventory`）：`full` 時 `max_operators` ≤ 1,000（預設與行為不變）；`summary` 時 ≤ 10,000，結果以 `inventory_sha256`（排序後 inventory 的 canonical JSON SHA-256）與 `type_counts` 取代 `inventory`。驗證、備份、回滾仍比對完整 inventory。
- 兩個選項各自對應 Agent capability `ops.tox.import:root_child`、`ops.tox.import:inventory_summary`。Daemon 在 admission 與 dispatch 時要求 Agent 已宣告，舊版 Agent 會在送出前得到 `command_unsupported`，不會默默忽略選項。
- `ops.connect`／`ops.disconnect`：COMP 的一般 connector 以其 `inOP`／`outOP` 的 family 比對。
- Protocol 訊息、Request lifecycle 狀態機、transport 都不變。

## Locked 驗收（真實 Daemon transport 與 SocketIO DAT）

`tools/locked_palette_tox_acceptance.py`：本分支的 `create_transport_app` 在隔離的 `127.0.0.1:19982` 執行，data root 在 scratch `LOCALAPPDATA`。disposable project 以官方 `Samples/Setup/Base/NewProject.toe` 加 Execute DAT（`file` 指向 `tools/locked_menu_limit_probe.py`，TOC 以 LF 寫入）建成，`toecollapse` exit 0、無警告，產物 882 bytes。作者的作品 TD（PID 23080）與使用者的 Daemon 全程未觸碰。

Candidate Agent 由 commit `771ff9e` 的 source 以 `agent/build_td.py` 在 disposable TD 建置：source revision `a6614ca1271b2ebaf1e1c779029091034a665513491c88d28ba40ce85ecb0c3d`，SHA-256 `173a65da98f224f6ae0e7cc782f963dd8edd9aed409793f7c00a7efdd214efff`，`inspect-artifact` valid。manifest 版本仍是 0.9.0，版本號在發版時調整。

[candidate-agent.json](evidence/palette-tox/candidate-agent.json)（TD PID 24180，exit 0）：34 個 Request，失敗的都是預期的拒絕。

| 步驟 | 結果 |
| --- | --- |
| projectorBlend，`max_operators=10` | `operator_limit`，`limit=10` |
| `root_child=missingChild` | `root_child`，`relative_path=missingChild` |
| 內含 `externaltox=C:/tdcli_missing/linked.tox` 的 TOX（由 `binary.export` 產生） | `external_tox`，`relative_path=linked`，`op_type=baseCOMP`，`parameter=externaltox` |
| kantanMapper 預設 256 上限 | `operator_limit`，`limit=256` |
| 拒絕後 `/project1/projection` children | `[]` |
| projectorBlend，`--root-child projectorBlend` | 55 個 Operator，inventory 的相對路徑與 vendor `toeexpand` 列出的內層元件完全相同，約 0.1 秒 |
| kantanMapper，`--root-child kantanMapper --inventory summary --max-operators 5000` | 4,077 個 Operator，等於 `toeexpand` 列出的數量，結果 2,405 bytes，約 0.95 秒 |
| `parameters.list` | projectorBlend 與 kantanMapper 的自訂參數都列出（結果 96 KB／89 KB） |
| `parameters.set`→`get` | `Projector1overlap2`=240、kantanMapper `w`=1920、`h`=1080 讀回一致 |
| `ops.children --op-type outTOP` | projectorBlend `out1`；kantanMapper `out1`、`out2` |
| `ops.connect` | kantanMapper→projectorBlend（COMP→COMP）、projectorBlend→`nullTOP`（COMP→TOP）、`constantTOP`→projectorBlend（TOP→COMP），`ops.connections` 讀回 |
| `ops.inspect` | 兩個元件內部的 `out1` TOP 都可被動讀取 |
| `ops.destroy` projectorBlend | 成功 |
| `ops.destroy` kantanMapper（上限 1,000） | `result_too_large`，沒有 mutation |

[legacy-agent-0.9.0.json](evidence/palette-tox/legacy-agent-0.9.0.json)（已安裝的官方 0.9.0 `td-agent.tox`，SHA-256 `4f1bba67…51543a8`，TD PID 7312）：一般匯入重現 `tox_verification_failed` 且 `details` 為空；帶 `root_child` 的 Request 在 admission 被拒（`command_unsupported`），沒有建立 Request。

## 自動測試

- `tests/test_agent_runtime.py`：Palette 形狀（包裝、`icon`、`enableexternaltox=on` 的巢狀 COMP）可匯入；五種拒絕的 `details`；`operator_limit`；snapshot 使用呼叫端上限（1,201 個 Operator）；`root_child` 安裝內層並捨棄包裝；`root_child` 不存在或不是 COMP；`summary` 的 digest 與 `type_counts`；outcome 攜帶 details；COMP connector family。
- `tests/test_protocol.py`：`full` 上限 1,000、`summary` 上限 10,000、`root_child` 名稱規則、`inventory` 列舉；`required_capabilities`。
- `tests/test_lifecycle.py`：Agent 未宣告 `ops.tox.import:root_child` 時 admission 拒絕且不寫入 store。
- `tests/test_control_cli.py`：`--root-child`、`--inventory`。

## 限制

- `ops.destroy`、`ops.copy`、`ops.move` 仍是 1,000 個 Operator 上限，所以 kantanMapper 這種 4,077 個 Operator 的元件無法用 td-cli 刪除或搬移（會以 `result_too_large` 失敗，不改圖）。需要時改用 `ops.tox.import --replace` 換版，或另開 issue 擴充。
- kantanMapper 的 `Project` 參數（`kantan.json`）在執行期讀寫專案資料夾內的檔案。這是 TOX 自己的 graph 外副作用，td-cli 不管理。
- 作品內嵌的 Agent 需要升級到包含本變更的版本才能使用 `--root-child`、`--inventory summary` 與 COMP connector 配線；`td-agent upgrade-project` 需要先關閉 TD。
