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

- `externaltox` 或 `subcompname` 非空、由 expression／export／bind 驅動，或留有 expression／bind 來源時，算外部連結；VFS 非空仍拒絕。這些檢查涵蓋整個載入的 TOX，包括 `root_child` 捨棄的外層。匯入不清除、不改寫任何參數。Palette 元件本身沒有外部連結與 VFS，執行期不依賴網路。
- `tox_verification_failed.details`：`check`（`operator_limit`、`operator_type`、`operator_name`、`external_tox`、`vfs`、`load_shape`、`root_child`、`inspection`）與 `subject`（`source`、`destination`、`installed`），視情況附 `relative_path`（`source` 相對於載入的 TOX root，最長 512 字元）、`op_type`、`parameter` 與 `mode`、`limit`。不回傳參數值或 expression。Agent 其他錯誤的 `details` 不變。
- `root_child`（`--root-child`）：安裝載入 root 底下同名的直屬 COMP。
- `inventory`（`--inventory`）：`full` 時 `max_operators` ≤ 1,000（預設與行為不變）；`summary` 時 ≤ 10,000，結果以 `inventory_sha256` 與 `type_counts` 取代 `inventory`。digest 是 `full` 各列（`relative_path`、`name`、`op_type`、`family`，依 `relative_path` 排序，root 名稱為目標名稱）的 canonical JSON SHA-256。驗證、備份、回滾仍比對完整 inventory。
- 預設值（`root_child` 為空、`inventory=full`）不放進 wire，新 CLI 搭配舊 Daemon 時一般匯入照常。
- 兩個選項各自對應 Agent capability `ops.tox.import:root_child`、`ops.tox.import:inventory_summary`。Daemon 在 admission 與 dispatch 時要求 Agent 已宣告，否則回 `command_unsupported`，不會讓舊 Agent 默默忽略選項。
- `ops.connect`／`ops.disconnect`：COMP 的一般 connector 以其 `inOP`／`outOP` 的 family 比對。行為變更：COMP 對 COMP 原本一律接受，現在 family 不同或 connector 沒有 In／Out Operator 時以 `operator_family_mismatch` 拒絕。
- Protocol 訊息、Request lifecycle 狀態機、transport 都不變。

## Locked 驗收（真實 Daemon transport 與 SocketIO DAT）

`tools/locked_palette_tox_acceptance.py`：本分支的 `create_transport_app` 在隔離的 `127.0.0.1:19982` 執行，data root 在 scratch `LOCALAPPDATA`。disposable project 以官方 `Samples/Setup/Base/NewProject.toe` 加 Execute DAT（`file` 指向 `tools/locked_menu_limit_probe.py`，TOC 以 LF 寫入）建成，`toecollapse` exit 0、無警告，產物 882 bytes。作者的作品 TD（PID 23080）與使用者的 Daemon 全程未觸碰。

Candidate Agent 由 commit `8754971` 的 source 以 `agent/build_td.py` 在 disposable TD 建置：source revision `27bda295989b3cf3ccd080e849a986e7319b748c39bb02f29b8fcc0fda87b981`，SHA-256 `fb07649262ed1a91875a2c04a8829751384a7acddaa6f801177fb13b26bf3811`，`inspect-artifact` valid。manifest 版本仍是 0.9.0，版本號在發版時調整。

[candidate-agent.json](evidence/palette-tox/candidate-agent.json)（TD PID 13640，exit 0）：41 個 Request，失敗的都是預期的拒絕。所有拒絕的 `details.subject` 都是 `source`。

| 步驟 | 結果 |
| --- | --- |
| projectorBlend，`max_operators=10` | `operator_limit`，`limit=10` |
| `root_child=missingChild` | `root_child`，`relative_path=missingChild` |
| 內含 `externaltox=C:/tdcli_missing/linked.tox` 的 TOX（由 `binary.export` 產生） | `external_tox`，`relative_path=linked`，`op_type=baseCOMP`，`parameter=externaltox`，`mode=constant` |
| `root_child=inner`，外層另一個子 COMP `sibling` 的 `externaltox` 以 expression 求值為空字串 | `external_tox`，`relative_path=sibling`，`mode=expression`（外層 root 自己的 `externaltox` 經 `saveByteArray` 不會保留，所以以子 COMP 驗證） |
| kantanMapper 預設 256 上限 | `operator_limit`，`limit=256` |
| 拒絕後 `/project1/projection` children | `[]` |
| projectorBlend，`--root-child projectorBlend` | 55 個 Operator，inventory 的相對路徑與 vendor `toeexpand` 列出的內層元件完全相同，約 0.2 秒 |
| kantanMapper，`--root-child kantanMapper --inventory summary --max-operators 5000` | 4,077 個 Operator，等於 `toeexpand` 列出的數量，`inventory_sha256` `95f15b03…44c6`，約 0.9 秒 |
| `parameters.list` | projectorBlend 與 kantanMapper 的自訂參數都列出（結果 96 KB／89 KB） |
| `parameters.set`→`get` | `Projector1overlap2`=240、kantanMapper `w`=1920、`h`=1080 讀回一致 |
| `ops.children --op-type outTOP` | projectorBlend `out1`；kantanMapper `out1`、`out2` |
| `ops.connect` | kantanMapper→projectorBlend（COMP→COMP）、projectorBlend→`nullTOP`（COMP→TOP）、`constantTOP`→projectorBlend（TOP→COMP），`ops.connections` 讀回 |
| `ops.inspect` | 兩個元件內部的 `out1` TOP 都可被動讀取 |
| `ops.destroy` projectorBlend | 成功 |
| `ops.destroy` kantanMapper（上限 1,000） | `result_too_large`，沒有 mutation |

[legacy-agent-0.9.0.json](evidence/palette-tox/legacy-agent-0.9.0.json)（已安裝的官方 0.9.0 `td-agent.tox`，SHA-256 `4f1bba67…51543a8`，TD PID 27664）：一般匯入重現 `tox_verification_failed` 且 `details` 為空；帶 `root_child` 的 Request 在 admission 被拒（`command_unsupported`），沒有建立 Request。

## 自動測試

- `tests/test_agent_runtime.py`：Palette 形狀（包裝、`icon`、`enableexternaltox=on` 的巢狀 COMP）可匯入；拒絕的 `details`（常數、expression、bind、constant 但留有 expr 的連結，VFS，型別，名稱）；外層 linkage 在 `root_child` 下仍被拒；`root_child` 下的路徑相對於 TOX root；snapshot 遍歷例外轉為 `inspection`；`--replace` 時既有目的地的問題標為 `destination`；`root_child` 搭配 `--replace` 的成功與回滾；`operator_limit`；snapshot 使用呼叫端上限（1,201 個 Operator）；`root_child` 不存在或不是 COMP；`summary` 的 digest 與 `type_counts`；outcome 攜帶 details；COMP connector family（TOP↔COMP、COMP↔COMP 不符、沒有 In／Out Operator、disconnect）。
- `tests/test_protocol.py`：`full` 上限 1,000、`summary` 上限 10,000、`root_child` 名稱規則、`inventory` 列舉；預設值不上 wire；`required_capabilities`。
- `tests/test_lifecycle.py`：Agent 未宣告 `root_child`／`inventory_summary` capability 時 admission 拒絕且不寫入 store；排隊中的 Request 在 Agent 以較少 capability 重連後，於 dispatch 失敗為 `command_unsupported`。
- `tests/test_control_cli.py`：`--root-child`、`--inventory`。

## 限制

- `ops.destroy`、`ops.copy`、`ops.move` 仍是 1,000 個 Operator 上限，所以 kantanMapper 這種 4,077 個 Operator 的元件無法用 td-cli 刪除或搬移（會以 `result_too_large` 失敗，不改圖）。需要時改用 `ops.tox.import --replace` 換版，或另開 issue 擴充。
- 一次匯入會多次實例化 TOX 內容（載入暫存區、複製到目的地，`--replace` 另外還原備份），callback 可能執行多次。kantanMapper 的 `Project` 參數（`kantan.json`）在執行期讀寫專案資料夾內的檔案；這類 graph 外副作用可能重複，td-cli 不回滾。
- harness 以 `toeexpand` 比對時必須在短路徑下展開：kantanMapper 巢狀很深，基底路徑太長時 `toeexpand` 會因 MAX_PATH 默默略過檔案（它的 exit code 本來就是 1）。harness 改用短的暫存目錄，並檢查 TOC 列出的檔案都存在。
- 作品內嵌的 Agent 需要升級到包含本變更的版本才能使用 `--root-child`、`--inventory summary` 與 COMP connector 配線；`td-agent upgrade-project` 需要先關閉 TD。
