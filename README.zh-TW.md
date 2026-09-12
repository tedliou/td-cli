# td-cli

[English](README.md) | [正體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md)

<!-- doc-section: overview -->

td-cli 是 Codex 與 TouchDesigner Instance 之間的本機驗證控制工具。公開的 `td`
介面提供型別化 Operator／Parameter 控制、有界專案觀察、二進位匯出、批次讀取、專案中繼資料與
事件／錯誤觀察。它不開放任意 Python，也不接受遠端網路控制。

冷啟動程序服務與就緒、Request 等待共用 `td --timeout` 可見總期限（預設 30 秒）。`td-daemon start --timeout 30` 設定完整啟動期限。啟動逾時代表結果未知；再次開啟前先檢查既有程序。

<!-- doc-section: requirements -->

## 系統需求

- Windows x86-64
- TouchDesigner `2025.32050`

<!-- doc-section: install -->

## 安裝與第一次使用

在 PowerShell 安裝最新穩定 Release。安裝程式會驗證已發布的 checksum，並將執行檔加入使用者
`PATH`：

```powershell
irm https://github.com/tedliou/td-cli/releases/latest/download/install.ps1 | iex
```

開啟新的 PowerShell 視窗，確認安裝：

```powershell
td --version
```

將 `%LOCALAPPDATA%\Programs\touchdesigner-cli\current\td-agent.tox` 拖入
TouchDesigner 專案。Agent Component 上線後，列出 Instance、選擇 Online Instance，並建立一個
受支援的 Operator：

```powershell
td --json instances list
td --json --instance <selector> ops create /project1 constantTOP source
```

升級時再次執行相同安裝命令並重新啟動 Daemon。解除安裝會保留 Daemon 資料與 TouchDesigner
專案：

```powershell
irm https://github.com/tedliou/td-cli/releases/latest/download/uninstall.ps1 | iex
```

需要連線的 `td` 指令會按需在背景啟動 Daemon；`--help`、`--version` 與離線
`ops types` 型別目錄不會啟動服務。可直接將 Agent 拖入已開啟的作品，不必重開
TouchDesigner。Agent 的 Connection 頁顯示 `waiting_for_daemon`、`connecting`、
`online` 或明確的認證／註冊錯誤。Windows 採本機 `Win32_Process.Create` 啟動，
使 Daemon 與明確開啟的 TD 不受呼叫端終端機 Job 生命週期牽制；背景啟動不顯示控制台視窗。

需要明確開啟已保存作品時：

```powershell
td --json project open C:/work/design.toe --executable 'C:/Program Files/Derivative/TouchDesigner/bin/TouchDesigner.exe'
td --json ops types choptoDAT
td-agent install-skill
```

`project open` 回傳作業系統 PID，不代表 Agent 已上線，也不關閉任何既有 Instance。
`install-skill` 將隨版本內嵌於 `td-agent.exe` 的 `td-cli` skill 安裝至
`$CODEX_HOME/skills/td-cli`（預設 `~/.codex/skills/td-cli`），不需另行下載。
目的資料夾已有內容時須明確加 `--replace`，可用 `--destination` 指定其他 skill 目錄。
Skill 依任務連結 Derivative 理論與真正的 CLI help，避免猜型別、參數及數值語意。

存檔使用既有的 live `project save <目前路徑> --expected-sha256 <磁碟雜湊>`；
成功結果包含磁碟 SHA-256，不需要關閉 TD。磁碟前置條件失敗會如實回傳
`project_file_changed`，不再誤報 `protocol_incompatible`。存檔逾時保留 Request ID，
先查 outcome 與磁碟狀態再決定下一個 mutation。執行檔升級仍不會替換作品內嵌的 Agent。

<!-- doc-section: development -->

## 開發

需要 Python 3.11 與 [uv](https://docs.astral.sh/uv/)：

```powershell
uv sync --locked --python 3.11
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv lock --check
```

完整貢獻流程見 [CONTRIBUTING.md](CONTRIBUTING.md)。Protocol、Daemon runtime、RequestStore、
Agent scheduler 或 locked TouchDesigner 驗收變更，必須遵循
[td-runtime-reliability skill](.agents/skills/td-runtime-reliability/SKILL.md)。

<!-- doc-section: daemon -->

## Daemon

Daemon 是每位 Windows 使用者一個的驗證背景程序，只綁定 `127.0.0.1:9982`，狀態位於
`%LOCALAPPDATA%\touchdesigner-cli`。`td-daemon start` 會在背景程序就緒後返回，且不會留下
開啟的控制台視窗。

```powershell
uv run td-daemon start
uv run td-daemon status --json
uv run td-daemon stop
uv run td-daemon serve
```

固定配置包含 `state\daemon.db`、`state\auth.token`、`logs\daemon.log`，以及執行期間的非權威
`run\daemon.json`。只有在 Daemon 停止時刪除 `state\auth.token` 才是手動 token recovery；之後
所有 Agent Component 都必須重新連線。

Protocol v3 是唯一 runtime protocol，不提供 v1 alias 或 fallback。Request 依序經過
`queued`、`dispatched`、`accepted`、`running` 後才進入終態。Daemon 必須先持久化再派送，
每個 Instance 依 FIFO 僅允許一個已授權 Request，且每次重連都以新的 Connection ID 隔離。
授權後斷線會成為 `unknown`；td-cli 絕不自動重試，但同一 execution 保留的結果之後可將它細化為
`succeeded` 或 `failed`。

<!-- doc-section: agent-component -->

## Agent Component

`agent/` 下可審查的檔案是權威來源；`td-agent.tox` 是本機衍生檔且不進 Git。

```powershell
uv run td-agent inspect-source agent
uv run td-agent build-instructions --output path\to\td-agent.tox --source agent
uv run td-agent inspect-artifact path\to\td-agent.tox --source agent
```

Artifact inspection 需要 locked TouchDesigner build 產生的相鄰
`td-agent.tox.manifest.json`。它將 artifact 綁定至 canonical source revision、
TouchDesigner `2025.32050` 與必要 DAT／Operator topology。實際 `.tox` 建置及 Online Instance
驗證必須在鎖定版 TouchDesigner 環境完成。

Agent 在送出 `request_accepted` 前會先保留有界 outcome 容量，只執行符合 immutable execution
authorization 的工作，並保留所有 post-accept `succeeded`、`failed`、`unknown` outcome，直到
Daemon 完成記錄。上限為 64 筆、每筆 canonical outcome 256 KiB、合計 16 MiB。Command、
超過鎖定版 SocketIO DAT 單一事件實測範圍的 outcome，會拆成依序且經 identity 驗證的 24 KiB
區塊，重組完成後才提交公開結果。Command、heartbeat 與 drain timer 使用 TouchDesigner
獨立的 `TDResources` 時間參考，所有 TouchDesigner object 存取仍只在主執行緒。Extension
初始化使用官方 SocketIO Reset 參數，連線後清除暫存 auth DAT。Power Off 模式不受支援。

<!-- doc-section: offline-upgrade -->

## 升級專案內嵌 Agent

`td-agent upgrade-project` 是獨立於 runtime Protocol 的固定離線升級入口。
目前已驗證 canonical Agent 0.3.1 / 0.4.0 → 0.5.0、TouchDesigner 2025.32050；
相同 0.5.0 僅驗證、不改檔，不代表任意歷史或未來版本都受支援。
先儲存作品並關閉**所有 TouchDesigner 程序**；命令會拒絕仍有程序的情況，
不會自動關閉它們。使用新版 CLI 套件及其可信 artifact／manifest：

```powershell
$bundle = "$env:LOCALAPPDATA/Programs/touchdesigner-cli/current"
$project = (Resolve-Path ./MyProject.toe).Path
$sha = (Get-FileHash $project -Algorithm SHA256).Hash.ToLower()
& "$bundle/td-agent.exe" upgrade-project $project --artifact "$bundle/td-agent.tox" --manifest "$bundle/manifest.json" --tools-dir "C:/Program Files/Derivative/TouchDesigner/bin" --expected-sha256 $sha --timeout 90
```

命令只在暫存副本使用鎖定版本的官方工具，辨識既有 Agent、驗證其餘作品
檔案不變，建立唯一且驗證過的備份，再原子替換原檔。未知或改過的 Agent、
多重匹配、外部連結、不支援的 build、輸入變更及 round-trip 失敗均拒絕。
停用的 external-TOX 路徑作為無作用的中繼資料保留。
整個操作期間需獨占已關閉的專案；替換前失敗保留原檔，備份留供檢查。

先啟動升級後的 daemon，再開啟專案並重新查詢 Instance selector。
協議拒絕會停用連線直到下次 Agent 初始化，不重試不相容協議。
歷史 0.3.1 工具沒有此入口，首次遷移應呼叫新版 `td-agent`。
後續版本維持命令入口，明確擴充已驗證的遷移範圍。

<!-- doc-section: operator-control -->

## 基本 Operator 控制

有多個 Instance 時務必使用明確 Selector。Protocol 提供 catalog 內建 Operator 建立、檢查、
Parameter 設定與同 family 配線：

```powershell
td --json instances list
td --json --instance <selector> ops create /project1 constantTOP source --node-x -200
td --json --instance <selector> ops create /project1 nullTOP output
td --json --instance <selector> parameters set /project1/source colorr --number 0.25
td --json --instance <selector> parameters list /project1/source
td --json --instance <selector> ops connect /project1/source /project1/output
td --json --instance <selector> ops rename /project1/output renamed_output
td --json --instance <selector> ops children /project1 --op-type constantTOP
td --json --instance <selector> ops inspect /project1/source --max-items 100
```

`ops.inspect` 是 CHOP、DAT、TOP、SOP、POP、MAT 的被動有界讀取。它不下載 pixels、geometry、
POP buffers、DAT content 或 Python objects，也不主動 cook。可變長度資料受 `--max-items`
限制（預設 100、最高 1000），溢位會失敗而不截斷。

<!-- doc-section: parameter-control -->

## Parameter 控制

Parameter inspection 依 style 區分 boolean、integer、number、string、menu、單一 OP、最多 256
條路徑的 Multi-OP、Pulse、Sequence header 與不透明 Python value。Python value 只會回報不支援，
不會序列化。disabled、read-only、hidden／obsolete、型別不符及被 TouchDesigner clamp 的寫入都不會
回報成功。

```powershell
td --json --instance <selector> parameters get /project1/source colorr
td --json --instance <selector> parameters set /project1/target Targetop --operator /project1/source
td --json --instance <selector> parameters set /project1/target Targets --operators-json '["/project1/a","/project1/b"]'
td --json --instance <selector> parameters set /project1/target Gain --bind-source-operator /project1/source --bind-parameter Gain
td --json --instance <selector> parameters sequence-get /project1/target Items
td --json --instance <selector> parameters sequence-replace /project1/target Items --blocks-json '[{"name":"first","parameters":[{"parameter":"value","mode":"constant","value":1.5}]}]'
```

Bind source 只由型別化 Operator／Parameter identity 產生。Export 只接受已存在的 CHOP
Operator／channel identity。Sequence replacement 最多 128 blocks、每 block 256 Parameters；失敗時會
復原並驗證完整的 block 數量、順序、名稱、mode、value 與 source。

使用 `parameters page-create` 在 COMP 建立新的原生自訂參數頁。每頁支援 1–32 個純量 `float`、`toggle` 或 `menu` 控制。名稱以大寫 ASCII 字母開頭，後接小寫字母或數字（最多 32 字元）。浮點控制需有限的最小值、最大值與預設值，並啟用硬性範圍限制；選單需 1–32 個唯一名稱與對應標籤。已存在的頁面或參數名稱會被拒絕，不覆蓋。結果回傳驗證過的描述與值，後續以 `parameters list/get/set` 檢查與修改。建立失敗只移除本次新頁面；回復失敗或結果未知時，須先查詢再執行下一次修改。

ASCII 跳脫後的輸入 JSON，加上每個參數一份序列化目標路徑，合計限 16,384 bytes，以保留結果描述所需空間。

```powershell
td --json --instance <selector> parameters page-create --input-file controls.json
td --json --instance <selector> parameters list /project1/controls
```

```json
{"operator_path":"/project1/controls","page":"Controls","parameters":[{"name":"Gyrox","label":"Gyro X","kind":"float","default":0,"minimum":-1,"maximum":1},{"name":"Manual","label":"Manual","kind":"toggle","default":true},{"name":"Source","label":"Source","kind":"menu","default":"manual","menu_names":["manual","device"],"menu_labels":["Manual","Device"]}]}
```

<!-- doc-section: regular-connections -->

## Regular Connection

變更 graph 前先檢查所有 input／output connector；inventory 有界，溢位會失敗：

```powershell
td --json --instance <selector> ops connections /project1/source --max-connections 256
```

Connect 預設拒絕已占用 input，只有明確 `--replace` 才可替換；disconnect 必須指定精確
source/output 與 target/input。

<!-- doc-section: hierarchy-connections -->

## COMP Hierarchy Connection

Hierarchy Connection 是 Object COMP 或 Panel COMP 的上到下 parent／child connector，與 Regular
Connection 不同，也不能跨 hierarchy kind。Connect 會在 mutation 前拒絕跨 kind、非 COMP、cycle、
缺少或已占用 endpoint；`--replace` 會保存並在失敗時復原原連線。

```powershell
td --json --instance <selector> ops hierarchy connections /project1/geo1 --max-connections 256
td --json --instance <selector> ops hierarchy connect /project1/geo1 /project1/geo2
td --json --instance <selector> ops hierarchy disconnect /project1/geo1 /project1/geo2
```

root、Agent Component 及其 ancestors／descendants 都受保護。Hierarchy 讀取可批次；mutation 不可批次。

<!-- doc-section: structural-mutations -->

## 結構 mutation

Create、rename、copy、move、destroy 使用精確 path／name，拒絕 root、Agent tree、自動命名、collision
與超大 subtree。Destroy 非空或有連線的 Operator 需要明確 opt-in。Move 是已驗證的
copy-then-destroy，會改變 Operator identity。

```powershell
td --json --instance <selector> ops copy /project1/source /project1/group copied
td --json --instance <selector> ops move /project1/source /project1/group moved --allow-connected
td --json --instance <selector> ops destroy /project1/group/moved --recursive --allow-connected
```

三種操作預設最多影響 256 Operators（最高 1000）。Copy／move 會驗證精確結果，並在失敗時移除
新 copy；無法證明最終狀態時回傳獨立 rollback／uncertain-outcome error。

<!-- doc-section: trusted-tox-import -->

## Trusted TOX Import

Trusted TOX Import 只接受 allowlist root 下既存的 absolute local `.tox`，且呼叫者必須傳
`--trusted`。TOX 是可執行 TouchDesigner 內容；td-cli 不會 sandbox，也不能復原 filesystem、network、
process 或其他 graph 外副作用。

```powershell
td --json --instance <selector> ops tox import /project1/imports C:\approved\asset.tox C:\approved asset --trusted
```

它會限制並驗證目的 Operator graph，拒絕 external TOX linkage 與 VFS，也不儲存專案。Replace
先建立、獨立還原並比對 in-memory backup，再移除原目的。失敗時復原並驗證；cleanup、identity 或
rollback 無法證明時回傳明確 uncertain outcome。檔案預設上限 64 MiB，inventory 預設 256、最高
1000 Operators，所有上限都以失敗而非截斷處理。

<!-- doc-section: operator-state -->

## Common Operator state

Common state 的讀取與 atomic partial update 涵蓋 node position／size、RGB color、comment、Bypass、
Lock、Viewer 與 Expose。每個欄位都會 read back；clamp 或拒絕會 rollback 整個 patch。

```powershell
td --json --instance <selector> ops state get /project1/source
td --json --instance <selector> ops state set /project1/source --node-x -100 --node-width 140 --color 0.1 0.2 0.3 --comment "source" --bypass --no-expose
```

Comment 最多 4096 字元，座標 -32768 到 32767，正尺寸最高 32767，RGB 是 0 到 1 的 finite number。
Family-specific Display／Render／Allow Cooking、selection、current viewer、storage、任意 attribute 與
Python object 不在此介面內。

<!-- doc-section: dat-content -->

## Text／Table DAT content

Text 讀取與完整替換保留 Unicode 和空字串。Table 讀取回傳總尺寸與明確 rectangular window；replace
設定完整 table，patch 只更新既有範圍而不 resize。

```powershell
td --json --instance <selector> dat text get /project1/notes
td --json --instance <selector> dat text set /project1/notes "繁體內容"
td --json --instance <selector> dat table get /project1/grid --row-offset 0 --column-offset 0 --row-count 16 --column-count 16
td --json --instance <selector> dat table replace /project1/grid '[["name","value"],["alpha",""]]'
td --json --instance <selector> dat table patch /project1/grid '[["updated"]]' --row-offset 1 --column-offset 1
```

文字存取限 `textDAT`，表格寫入限 `tableDAT`；有界表格讀取接受 `isTable` 為真的 DAT，包含用於讀取實際 CHOP 輸出值的 CHOP to DAT。讀取遵循一般相依 cook，不強制 cook。外部 File／Sync File、protected path、非 rectangular／非 string
cell 及越界 patch 都會被拒絕。Content 上限 32 KiB UTF-8、256 rows、256 columns、4096 cells、每
cell 16 KiB。Mutation 會 read back 完整內容與尺寸，失敗時復原整份 DAT。這些 Command 不執行
DAT、不 import module、不 evaluate content，也不接受 filesystem path。

<!-- doc-section: project-save -->
## 保存目前專案

`project.save` 只保存目前已存在的本機 `.toe`。先以 `project metadata` 確認
路徑、排除其他寫入者，再取得磁碟檔案摘要：

```powershell
$projectPath = 'E:\artwork\Artwork.toe'
$digest = (Get-FileHash -LiteralPath $projectPath -Algorithm SHA256).Hash.ToLowerInvariant()
td --json --instance <selector> project save $projectPath --expected-sha256 $digest
```

路徑與 SHA-256 必須在執行前相符；這不是跨程序的原子鎖。檔案上限 64 MiB，
拒絕連結／reparse 路徑。成功結果包含實際磁碟路徑、位元組數及 SHA-256。
不另存新檔名，也不保存外部 TOX。保存後無法確認結果時回報
`project_save_outcome_unknown`；先查 retained Request 與磁碟，不自動重做。

Protocol v3 在 SocketIO 邊界以 JSON 文字保留 Command 的布林、整數及 null 型別。
CLI、Daemon 與專案內嵌 Agent 必須一起升級；v2/v3 雙向拒絕註冊。
可編輯的 `StrMenu`（例如 Select CHOP 的 `channames`）接受任意字串，
一般 `Menu` 仍只接受列出的選項名稱。

<!-- doc-section: operator-catalog -->

## 鎖定版 Operator catalog

TouchDesigner 2025.32050 catalog 涵蓋七個 family 的 680 個 built-in types：478 個預設支援、165 個
具副作用或依賴環境的 type 需要 `ops create --allow-conditional`、37 個不支援，沒有未分類的鎖定版
built-in。Custom、第三方及其他 build 不在此 inventory，會被拒絕直到對應 build probe 提供證據。
機器可讀細節與失敗證據位於
[`agent/touchdesigner-2025.32050-operators.json`](agent/touchdesigner-2025.32050-operators.json)。

每個新 TouchDesigner build 都必須重新 probe，並依 supported／conditional／unsupported／unknown
流程審查。建立與 rename 拒絕 collision；mutation 不得進入 `batch.execute`，batch 僅供有界、
read-only Command 使用。
