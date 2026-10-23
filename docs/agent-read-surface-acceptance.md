# Agent 讀取介面與大型結構操作驗收

日期：2026-10-24。Issues：#176、#171、#170、#166、#167、#168、#164、#153、#169、#159。

## 範圍

| Issue | 介面 |
| --- | --- |
| #176 | `batch execute` 每項回 `ok`／`result` 或 `ok`／`error`，整體成功並附 `failed_count`；需要 capability `batch.execute:item_results` |
| #171 | `instances list` 每個 Instance 附 `project`（`name`、`folder`），由註冊與 heartbeat 更新 |
| #170 | `ops inspect` 支援 COMP：`child_count`、`inputs`／`outputs`（`{index, operator, family}`）、hierarchy connector 數量、custom pages 的參數名稱 |
| #166 | `dat text get` 接受任何 `isText` DAT（含 Execute 系列 callback），回報 `op_type` |
| #167 | `dat text get --offset`：以 UTF-8 byte 分段讀取，附 `next_offset`、`total_utf8_bytes`、`text_sha256`；需要 `dat.text.get:offset` |
| #168 | `chop values get`：目前值與 `frame`，或 `--sample-offset`／`--sample-count` 的 sample 範圍；最多 4096 個值 |
| #164、#153 | `parameters list --include-values`、`--non-default-only`、`--names`、`--no-menu-options`；需要 `parameters.list:options` |
| #169 | `project snapshot --paginate`／`--cursor`／`--include-connections`；需要 `project.snapshot:options` |
| #159 | `ops destroy`／`copy`／`move --inventory summary`：`max_operators` 最高 10,000，回 `operator_count`、`type_counts`、`inventory_sha256` 與邊界連線；需要 `ops.<name>:inventory_summary` |

Protocol 訊息、Request lifecycle 狀態機與 transport 都不變。新選項的預設值不上 wire，舊 Agent 收到新選項時由 Daemon 以 `command_unsupported` 拒絕（capability gate，`tests/test_lifecycle.py` 既有覆蓋）。

## Locked 驗收（真實 Daemon transport 與 SocketIO DAT）

`tools/locked_agent_read_surface_acceptance.py`（共用 `tools/locked_daemon_harness.py`，`locked_palette_tox_acceptance.py` 也改用它）：本分支的 `create_transport_app` 在隔離的 `127.0.0.1:19982` 執行，data root 在 session scratch 的 `LOCALAPPDATA`。專案用的是 #157 驗收時的 disposable `probe.toe`，其 Execute DAT 執行 `tools/locked_menu_limit_probe.py`。使用者的 Daemon 與其他 TD 全程未觸碰；驗收結束時機器上沒有殘留的 TouchDesigner 程序。

Candidate Agent 以 `agent/build_td.py` 在 disposable TD 中建置（scratch `LOCALAPPDATA`，TD PID 9964），來源是 review 修正後 commit `c1cc443` 的 source：source revision `0430404efb0c0cd985c77bb293c479eadec5d61b18d957a011cd62127b1b78f2`，SHA-256 `45c9391630b9631f202146206346d3c5e9491d6ab13ec78c264653d59e0bb77c`，`td_cli.agent_tool inspect-artifact` 回報 valid。manifest 版本仍是 0.10.0，版本號在發版時調整。

[candidate-agent.json](evidence/agent-read-surface/candidate-agent.json)（TD PID 7388，exit 0，Daemon thread 正常停止）：共 63 個 Request，8 個失敗的都是預期的拒絕。

| 步驟 | 結果 |
| --- | --- |
| `instances list` | `project` 為 `{"name": "mlprobe.toe", "folder": "E:/td-cli/.codex/read-surface"}`，capabilities 含本輪新增的 7 項 |
| `batch execute`：`ops.get /project1` 與不存在的路徑 | `succeeded`，`failed_count` 1，第二項 `operator_not_found` |
| `ops inspect` baseCOMP（in1 inTOP、out1 outTOP、out2 outCHOP、Controls 頁） | `child_count` 3；`inputs` `[{0, in1, TOP}]`；`outputs` `[{0, out1, TOP}, {1, out2, CHOP}]`；`hierarchy_outputs` 1；`custom_pages` `Controls: [Gain, Enabled]` |
| `dat text get` executeDAT／chopexecuteDAT／parameterexecuteDAT | 讀到預設 callback（940／1820／1717 bytes），`op_type` 正確；tableDAT 仍是 `dat_type_mismatch` |
| `dat text get --offset`，220 bytes 的 CJK、emoji 混合文字，`max_bytes` 40 | 6 段，每段 ≤ 40 bytes，串接後與原文相同，每段 `text_sha256` 一致；offset 落在字元中間回 `invalid_arguments` |
| `chop values get` constantCHOP（tx 1.5、ty -2） | `values` `{tx: 1.5, ty: -2.0}`、`frame` 399；`--channels ty` 只回 ty；不存在的 channel 回 `chop_channel_not_found`；textDAT 回 `operator_family_unsupported` |
| `chop values get` patternCHOP，offset 0、count 8 | 8 個 sample、`sample_rate` 60、`total_samples` 1000；offset 超過結尾回空陣列 |
| `parameters list` constantCHOP | 完整 26 項（14,451 bytes）；`--non-default-only` 只回 `const0name`、`const0value`、`const1name`、`const1value`（2,739 bytes），值與設定相同，確認 TD `Par.isDefault` 可用；`--names` 附值；`--no-menu-options` 的 6 個 menu 參數為 `null`；未知名稱回 `parameter_not_found` |
| `project snapshot` 5 個 Operator，`max_operators` 2 | 不分頁回 `result_too_large`；`--paginate` 分 3 頁依序讀完，最後 `next_cursor` 為 `null`；`--include-connections` 回報 src0 → sink；刪除 cursor 指向的下一個 Operator 後續讀回 `snapshot_cursor_stale` |
| kantanMapper（4,077 個 Operator）以 `--inventory summary` 匯入 | 1.1 秒，`inventory_sha256` `95f15b03…44c6`，與 #157 驗收相同 |
| `ops copy --inventory summary --max-operators 5000` | 0.7 秒，4,077 個，`type_counts` 與匯入相同，`unreplicated_connections` 為空 |
| `ops move --inventory summary` 到另一個 parent，保留名稱 | 0.5 秒，`inventory_sha256` `1f7548d0…bf04` |
| `ops destroy` kantanMapper，full、上限 1,000 | `result_too_large`，沒有 mutation |
| `ops destroy --inventory summary` 搬移後的副本與原件 | 各約 0.2／0.5 秒，結果約 2 KB；副本的 digest 與 move 相同；完成後 parent 為空 |

### 驗收找到並修正的問題

第一輪 candidate 在真實 SocketIO DAT 上暴露出 fake graph 測不到的問題：Agent wire 會省略 dict 中的 `null`。

- 最後一段文字少了 `next_offset`。
- 非有限值的 CHOP channel 從 `values` 消失。
- `--no-menu-options` 的 `null` 被 normalizer 補成 `[]`。
- 非分頁 snapshot 少了 `next_cursor`。

修正（commit `e1a6643`）：

- CHOP 結果附依序排列的 `channels`。
- `parameters list` 省略 menu 時回 `include_menu_options: false`。
- 每個 snapshot 都回 `next_cursor`。
- Catalog normalizer 補回上述 `null`；COMP connector 的 `operator`／`family` 也一併補回。
- 另外，`frame` 改從 `td` module 取得 `absTime`。extension 的 `builtins` 中沒有它，第一輪的值是 `null`。

Review 曾建議依決議字面，把「mode 不是 constant」的參數也列為非預設。實測 candidate 後，constantCHOP 多出 `autoexportroot`（`parent()`）與 `rate`（`me.time.rate`）。這兩個參數的預設本身就是 expression，TD 回報 `isDefault`，列出來違背「只列偏離預設值的參數」的目的。所以 `--non-default-only` 仍以 TD 的 `isDefault` 為準：它同時涵蓋值、mode 與 expression，被改成 expression 驅動的參數仍會列出（單元測試覆蓋）。本文件的結果來自最後一次 candidate。

## 自動測試

- `tests/test_agent_runtime.py`：batch 每項結果；COMP 介面摘要；Execute 系列 DAT 讀取；文字分段與字元邊界；CHOP 目前值、範圍、channel 篩選、非有限值與上限；parameters list 的值、非預設、名稱篩選與省略 menu；snapshot 分頁、cursor 過期與錯誤 cursor、連線；summary destroy／move 的 digest、只回邊界連線，以及邊界連線超過上限時在修改前失敗。
- `tests/test_protocol.py`：各選項的預設值不上 wire、`required_capabilities`、輸入上限（full ≤ 1,000、summary ≤ 10,000）、Agent wire 省略 `null` 後的 normalizer 還原。
- `tests/test_control_cli.py`：`chop values get`、`parameters list`、`project snapshot` 與 `--inventory` 的 CLI 選項。
- `tests/test_socket_transport.py`、`tests/test_agent_callbacks.py`：Instance `project` 的註冊、heartbeat 更新與長度上限。

## 限制

- TouchDesigner 沒有網路修改計數，snapshot 分頁之間的修改只會在 cursor 指向的下一個 Operator 被移動或刪除時偵測到（`snapshot_cursor_stale`）。
- summary 模式的邊界連線上限是 512 條；超過時在修改前回 `result_too_large`。
- 作品內嵌的 Agent 需要升級到包含本變更的版本才能使用新選項。
