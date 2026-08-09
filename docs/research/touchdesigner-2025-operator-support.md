# TouchDesigner 2025.32050 Operator 支援研究

研究日期：2026-08-09。目標是為 `td-cli` v0.1.1 擴大 Operator 建立、改名、斷線、覆蓋連線與 Parameter introspection，同時把「官方保證」、「鎖定版本觀察」與「設計推論」分開。本文件以 Derivative 官方文件為主要證據；除特別標記外，網址內容可能隨官方文件更新，因此真正的支援集合仍須由鎖定 build `2025.32050` 實機產生。

## 結論摘要

1. TouchDesigner 有七個 built-in OP families：COMP、TOP、CHOP、POP、DAT、MAT、SOP；一般 wire 只能連接相同 family。COMP 另有上下方向的 component hierarchy connectors，不能與一般左右資料 connectors 混為一談。[Operator Family](https://docs.derivative.ca/Operator_Family) [Connector Class](https://docs.derivative.ca/Connector_Class)
2. 目前專案只 allowlist `constantTOP`、`noiseTOP`、`levelTOP`、`nullTOP`，遠小於鎖定版實際能力。最小且可擴展的方向不是手寫「所有 OP」，而是把鎖定版可取得的 Python OP type inventory 固化為版本化 manifest，再逐型以 disposable COMP 做 `create` probe；公開命令只接受 manifest 中通過的 type。
3. OP 重新命名可直接設定 `operator.name`；`id` 不因 rename 改變，但 path 會改變。命令應回傳 old/new canonical path，並拒絕碰撞或 TouchDesigner 自動改名。[OP Class](https://docs.derivative.ca/OP_Class)
4. 一般 wire 可透過 `Connector.disconnect()` 斷開。官方明定：在 input connector 呼叫 `connect(target)` 會取代該 input 的既有連線；在 output connector 呼叫則會 append。因此 `ops.connect --replace` 應刻意使用 target input connector 的 replace 語意，並在失敗時回復原連線。[Connector Class](https://docs.derivative.ca/Connector_Class)
5. Parameter 清單應由實際 instance 的 `builtinPars` 與 `customPars` 取得，名稱使用 `Par.name`，型別以 `Par.style` 與 `isPulse/isMenu/isNumber/isFloat/isInt/isOP/isPython/isSequence/isString/isToggle` 描述；不能只從使用者可見 label 猜 internal name。[Par Class](https://docs.derivative.ca/Par_Class) [ParCollection Class](https://docs.derivative.ca/ParCollection_Class)
6. 所有一般 Parameter 都有 mode/expr 資料模型，但這不等於每種 style 都可被 `td-cli` 安全地以 JSON 設值。`Pulse` 應只允許 pulse；Python object、multi-OP、sequence header、export/bind 及動態 menu 需要明確能力標記，不應假稱完全支援。[Par Class](https://docs.derivative.ca/Par_Class) [Parameter](https://docs.derivative.ca/Parameter)

## 證據層級

- **官方證據**：Derivative 文件明確描述的 Python interface 或 operator family 行為。
- **鎖定版本觀察**：本 repo 與既有 TouchDesigner 2025.32050 驗收資料。現況只有四種 TOP 的 create allowlist，且 `ops.connect` 遇到 occupied input 會拒絕；這是產品現況，不是 TouchDesigner 限制。
- **推論／待驗證**：由官方 interface 推導的產品設計，必須再用 2025.32050 diagnostic bridge 或 release build probe 驗證。

## 通用 Python 能力

### 建立與辨識 type

官方示例以 `op('/project1').create(sphereSOP)` 建立 Sphere SOP，並說明 OP type 可由 Python class 傳入；個別 OP class 的 `opType` 字串可供 `COMP.create()` 使用。[Python vs Tscript Equivalents](https://docs.derivative.ca/Python_vs_Tscript_Equivalents) [scriptSOP Class](https://docs.derivative.ca/ScriptSOP_Class) [windowCOMP Class](https://docs.derivative.ca/WindowCOMP_Class)

可推得兩種 create input 均應在鎖定 build probe：Python class（如 `noiseTOP`）與其 canonical `opType` 字串。產品 protocol 可繼續使用字串，但 Agent 必須只從 versioned manifest 解析，不得對任意 global 做 `eval()`。

`COMP.create()` 只代表「可嘗試建立」，不保證 operator 能正常 cook：device、license、OS、plugin、SDK、檔案、GPU feature 或外部 runtime 仍可能使其 unsupported。OP 的 `supported` member 可反映目前 OS 支援狀態，但不能取代 create/cook probe。[OP Class](https://docs.derivative.ca/OP_Class) [Custom Operators](https://docs.derivative.ca/Custom_Operators)

### 改名

官方 OP interface 的 `name` 可讀寫；`path` 唯讀，`id` 在 rename 後不變。[OP Class](https://docs.derivative.ca/OP_Class)

建議 `ops.rename <path> <new-name>`：

- 先驗證安全且精確的 name、同 parent 無碰撞。
- 保存 old path 與 OP id，設定 `name` 後以 id 重新取得 OP，確認 `name` 與 expected path。
- 若 TD 自動修正名稱或結果不符，嘗試回復舊名並回傳 typed failure。
- rename 會改變以字串 path 保存的外部參照；官方 release notes 曾修正 `parent()` expressions 在 rename 後不更新，顯示參照更新有版本敏感性。CLI 不應承諾所有 DAT 字串或外部系統 reference 都會自動更新。[Release Notes](https://docs.derivative.ca/Release%20Notes)

### 一般 wire、斷線與 replace

`OP.inputConnectors`/`outputConnectors` 是一般 operator 左右 connectors；`COMP.inputCOMPConnectors`/`outputCOMPConnectors` 是 Object/Panel component hierarchy 的上下 connectors。`Connector.connections` 列出實際連線。[Connector Class](https://docs.derivative.ca/Connector_Class)

官方 `Connector.connect()` 的核心語意：

- 對 **input connector** 呼叫時，既有 input connection 會被 replaced。
- 對 **output connector** 呼叫時，對 target append 新 connection。
- `disconnect()` 斷開該 connector；對 output 呼叫可能斷開其所有下游，因此產品命令必須用精確端點語意避免過度斷線。[Connector Class](https://docs.derivative.ca/Connector_Class)

建議命令：

- `ops.disconnect --source ... --target ... --output-index ... --input-index ...`：四元組精確匹配後，對 target input connector 斷線；若該 input 不是指定 source，回 `connection_not_found`，不能誤斷其他 wire。
- `ops.connect` 預設維持現行 occupied-input rejection。
- `ops.connect --replace`：記錄 target input 原連線，對 target input connector 執行官方 replace，驗證新四元組；若驗證失敗，先 disconnect 再嘗試恢復原 source connector。結果回傳 `replaced` 與 previous endpoint。
- 一般 wire 預設仍要求同 family；Component hierarchy connections 另開未來命令，避免把兩套 connector 模型混在一起。[Operator Family](https://docs.derivative.ca/Operator_Family) [Component](https://docs.derivative.ca/Component)

### Parameter inventory、型別與 expression

實際 OP instance 提供 `builtinPars`、`customPars`；`ParCollection[name]` 使用精確 internal name，找不到回 `None`。[ParCollection Class](https://docs.derivative.ca/ParCollection_Class) [OP Class](https://docs.derivative.ca/OP_Class)

建議 `parameters list <op-path>` 每項至少回：

```json
{
  "name": "colorr",
  "label": "Color",
  "page": "Constant",
  "style": "Float",
  "builtin": true,
  "custom": false,
  "hidden": false,
  "read_only": false,
  "mode": "constant",
  "expression": {"supported": true, "source": ""},
  "value_kind": "float",
  "menu_names": null
}
```

`Par.style` 是最細的官方 runtime type descriptor；各 `is*` 是穩定的 coarse capabilities。`hidden=True` 代表 obsolete/irrelevant、僅為相容性保留，預設應列出但標記，寫入需拒絕或要求明確 override。[Par Class](https://docs.derivative.ca/Par_Class)

Expression 支援需區分「TouchDesigner 可設 `Par.expr`」與「CLI 願意承諾 round-trip」：

| Parameter 類型 | constant | expression | pulse | 建議 v0.1.1 狀態 |
|---|---:|---:|---:|---|
| Float / Int / Toggle | 是 | 是 | 否 | 完整支援 |
| String | 是 | 是 | 否 | 完整支援；回傳 evaluated value 與 expr 分開 |
| Menu | name 或 index | 是 | 否 | 支援；同時回 menuNames/menuLabels/menuIndex |
| OP path | 單 OP 常數可行 | 是 | 否 | 支援單 OP；multi-OP 回 capability limitation |
| Pulse / Momentary | 否 | 不應提供 | 是 | 只允許 `parameters.pulse` |
| Python object | self-contained object | 技術上有 mode | 否 | JSON 值不完整；先只讀、標 `set_supported=false` |
| Sequence header / sequence block | 結構化 | 逐 Par 可能可行 | 視成員 | 先列出 metadata，寫入延後 |
| Export / Bind mode | evaluated read | expr 不是當前驅動來源 | 否 | 讀取 mode；v0.1.1 不覆寫，除非使用者明確改 mode |

官方說明 `val` 只代表 constant value，`eval()` 才是依 constant/expression/export/bind mode 得到的 working value；設定 `val` 會切 constant mode，設定 `expr` 會切 expression mode。Parameter modes 包含 CONSTANT、EXPRESSION、EXPORT、BIND。[Par Class](https://docs.derivative.ca/Par_Class)

`tdu.parSummary(OPType)` 可描述某個 built-in OP class 的 parameters，適合生成靜態文件／manifest；但它接受 OP type 或字串而非 instance，因此 runtime `parameters list` 仍應以 instance pars 為準，才能包含 custom 與 sequence instance 狀態。[TDU Class](https://docs.derivative.ca/Tdu_Module)

## OP family support matrix

下表的「推薦核心」不是人氣統計；它以 Derivative 官方 Getting Started、Sweet Sixteen 指引、family overview，以及跨專案常見的 generator/filter/IO/debug building blocks 組成。官方明確稱 Sweet Sixteen 為各 family 最常見且有用的 OP 集合，並稱 OP Snippets 有 1000+ live examples；完整優先序應以 locked build 的 Snippets/OP Create metadata 補強，不應以非官方網路文章猜測。[Getting Started](https://docs.derivative.ca/Getting_started) [OP Snippets](https://docs.derivative.ca/OP_Snippets)

| Family | 推薦第一批 create support | family-specific constraints | 建議狀態 |
|---|---|---|---|
| TOP | Constant, Noise, Null, Level, Composite, Over, Switch, Select, Transform, Resolution, Fit, Crop, Blur, Edge, Ramp, Movie File In, Text, GLSL, Render | GPU/codec/device/file/shader 可能建立成功但 cook error；多 input TOP 需依實際 connector 數量 | 既有 4 個立即保留；其餘用 probe 擴展 |
| CHOP | Constant, LFO, Noise, Null, Math, Merge, Select, Switch, Rename, Filter, Lag, Speed, Timer, Logic, Count, Analyze, Audio Device In/Out | sample rate/time slice/audio device；device OP 可能 unsupported | 純計算核心優先；device 類 conditional |
| SOP | Box, Sphere, Grid, Circle, Line, Null, Transform, Merge, Select, Switch, Copy, Copy to Points, Geometry, File In, Convert, Group, Material | legacy CPU geometry；部分 operator 已 deprecated 或由 POP 取代 | 核心 geometry 支援；deprecated 標記 |
| DAT | Text, Table, Null, Select, Merge, Switch, JSON, Execute, CHOP Execute, OP Execute, Parameter Execute, Script, Web Client, Folder, File In/Out, Info, Convert/CHOP to/SOP to/POP to | callbacks/網路/檔案具有 side effects；DAT 內容不是 parameter | 建立可廣泛支援，side-effect OP 需 capability flag |
| MAT | Constant, Phong, PBR, GLSL, Line, Wireframe, Point Sprite, Null, Select, Switch | MAT 通常以 parameter reference 套到 Geometry，不以普通 wire 串到 SOP；shader/TOP dependencies | 全 family inventory，純 built-in 優先 |
| COMP | Base, Container, Button, Slider, Text, Geometry, Camera, Light, Environment Light, Window, Replicator, Time, Engine, USD, FBX | COMP 可含網路；Object/Panel hierarchy connectors 與一般 connectors 不同；Engine/Window/loader 有重大 side effects | Base/UI/scene 核心優先；Engine/loader conditional |
| POP | Null, Select, Switch, Merge, Point, Line, Grid/Plane, Sphere, Transform, Math, Noise, Attribute, Group, SOP to, TOP to, DAT to, Render path helpers | POP 在 2025 stable 才正式加入，locked 2025.32050 的集合早於後續 32460/32820；不可用目前 docs 的新增 OP 反推 locked build | 必須由 32050 inventory/probe 決定 |

Derivative 官方另明確指出：DAT 有一組推薦熟悉的「Sweet Sixteen DATs」、Phong 是最常用 MAT；Component 官方列出 Object、Panel 與 misc types。[DAT](https://docs.derivative.ca/DAT) [MAT](https://docs.derivative.ca/MAT) [Component](https://docs.derivative.ca/Component)

## 目前未知或不應宣稱支援的 OP

README 應列出下列 category，而不是維護一份很快過時的數百項 denylist：

1. **不在 2025.32050 inventory 的後續 OP**：例如官方 release notes 顯示 Text/Trace/Triangulate POP 在 2025.32460、Script POP 在更後續 build 出現，不能列為 2025.32050 支援。[2025.30000 Release Notes](https://docs.derivative.ca/Release_Notes/2025.30000)
2. **Experimental OP**：build 間增刪與行為變化大，除非 32050 probe manifest 明列並有實機驗收，預設 unsupported。
3. **Custom OP / third-party plugin**：type 集合依機器與 binary permission 而異；官方說首次載入 binary 會要求使用者授權。v0.1.1 不應自動 allowlist。[Custom Operators](https://docs.derivative.ca/Custom_Operators)
4. **OS/device/license dependent**：Kinect、Orbbec、Ouster、RealSense、NDI、Blackmagic、Audio Device、MIDI、DMX、Laser、VR、NVIDIA-specific、RenderStream 等。可建立不代表設備可用；manifest 應標 `conditional`。
5. **file/network/process side-effect OP**：File Out、Movie File Out、Web/Socket/UDP/TCP、Execute DATs、Engine COMP、Window COMP 等。即使 create 可行，也應以 side-effect policy 控制，而非與純 generator 同級開放。
6. **deprecated/obsolete OP**：官方例示 Field COMP 在 2022.24200 deprecated；`Par.hidden` 也標示 backward-compatibility parameters。預設不推薦新建。[Field COMP](https://docs.derivative.ca/Field_COMP) [Par Class](https://docs.derivative.ca/Par_Class)
7. **需特殊初始化或內部 template 的 OP**：部分 OP Create Menu 項目可能建立附帶 docked DAT、default setup 或 palette-like內容；純 `COMP.create(type)` 是否等同 UI create 必須逐型驗證。

## 2025.32050 inventory/probe 計畫

這是達成「盡可能全部支援」而不瞎猜的最小可靠流程：

1. 在 locked TD diagnostic bridge 中枚舉 `td` module 可用的 OP classes，篩選具 `opType`/family 的 built-in types；另從 OP Create Dialog/TDI metadata 交叉核對。2025 release notes 指出 TDI Library 包含所有 built-in TD objects/classes/functions 的說明，適合作為第二來源。[2025.30000 Release Notes](https://docs.derivative.ca/Release_Notes/2025.30000)
2. 對每種 type 在 disposable Base COMP 中建立唯一名稱，記錄：create success、實際 `opType`、family、`supported`、input/output connector count、parameter summary、warnings/errors；立即 destroy。
3. 對純 built-in、無 side effect、supported 且 create 結果精確的 type 標 `supported`；device/file/network/process/experimental/custom 標 `conditional`；create exception 或結果不精確標 `unsupported` 並保存 error class/message。
4. 固化 `touchdesigner-2025.32050-operators.json`，包含 probe script revision 與 build；CI 只驗 schema/生成器 deterministic，不在 GitHub runner 啟動 TD。實機 probe 僅在本機 release validation 執行。
5. README 從 manifest 生成或摘要 family/count，並列 unknown categories；不要手工複製數百 type 名稱造成 drift。

Manifest 每項建議 schema：

```json
{
  "op_type": "noiseTOP",
  "family": "TOP",
  "status": "supported",
  "supported_on_os": true,
  "inputs": 0,
  "outputs": 1,
  "side_effect_class": "pure",
  "experimental": false,
  "create_verified": true,
  "notes": []
}
```

## 最小實作順序

1. **先做通用且與 OP type 無關的命令**：`ops.rename`、精確 `ops.disconnect`、`ops.connect --replace`、`parameters.list`。它們都建立在官方共通 OP/Connector/Par interface 上，能立即覆蓋七 families。
2. **建立單一 operator catalog module**：載入 locked-build manifest，供 Protocol 驗證、Agent create、capabilities、CLI help、README 共同使用，消除現行 Literal allowlist 的重複來源。
3. **本機產生 2025.32050 manifest**：先將無 side effect 的 built-in types加入 supported，再逐批擴展 conditional types；不因「create 成功」就宣稱可用。
4. **測試重點放在 public interface 與 rollback**：rename collision/rollback、disconnect 精確端點、replace 成功與恢復、parameter type capability mapping、manifest schema。移除只鎖死舊四項 allowlist 文字而未保護行為的墓碑測試。
5. **實機 acceptance**：每 family 至少 generator + filter + multi-input（若 family 有一般 wire），Parameter styles 各一例，replace/rollback 各一例；POP 另確認 type 確實存在於 32050。

## 仍需實機回答的問題

- `2025.32050` 的完整 built-in Python OP type 清單與各 type 的 `supported` 值。
- 哪些 UI OP Create entries 在 `COMP.create()` 時會自動附帶 docked/default operators，以及 CLI create 是否應保留該 setup。
- output connector 的 `disconnect()` 是否會一次移除全部 downstream（官方只說 disconnect connector，故產品應避免依賴此不夠精確的方向）。
- 對每種 `Par.style`，設定 `expr` 後是否都可穩定 round-trip；尤其 Pulse、Python、OP-list、sequence 與 dynamic menu。
- rename 對 parameter OP references、DAT script literal paths、parent shortcuts、exports/binds 的實際更新範圍。
- POP 在 `2025.32050` 的正式 type subset；不得使用後續 32460/32820 文件清單替代 probe。

以上 unknown 應成為 release acceptance evidence，而不是以推論填入 README 的「已支援」。
