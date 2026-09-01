# td-cli

[English](README.md) | [繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md)

<!-- doc-section: overview -->

td-cli 是 Codex 与 TouchDesigner Instance 之间的本机验证控制工具。公开的 `td`
接口提供类型化 Operator／Parameter 控制、有界项目观察、二进制导出、批量读取、项目元数据与
事件／错误观察。它不开放任意 Python，也不接受远端网络控制。

<!-- doc-section: requirements -->

## 系统需求

- Windows x86-64
- TouchDesigner `2025.32050`

<!-- doc-section: install -->

## 安装与第一次使用

在 PowerShell 安装最新稳定 Release。安装程序会验证已发布的 checksum，并将可执行文件加入用户
`PATH`：

```powershell
irm https://github.com/tedliou/td-cli/releases/latest/download/install.ps1 | iex
```

开启新的 PowerShell 窗口，确认安装并启动 Daemon：

```powershell
td --version
td-daemon start
```

将 `%LOCALAPPDATA%\Programs\touchdesigner-cli\current\td-agent.tox` 拖入
TouchDesigner 项目。Agent Component 上线後，列出 Instance、选择 Online Instance，并创建一个
受支援的 Operator：

```powershell
td --json instances list
td --json --instance <selector> ops create /project1 constantTOP source
```

升级时再次执行相同安装命令并重新启动 Daemon。卸载会保留 Daemon 数据与 TouchDesigner
项目：

```powershell
irm https://github.com/tedliou/td-cli/releases/latest/download/uninstall.ps1 | iex
```

<!-- doc-section: development -->

## 开发

需要 Python 3.11 与 [uv](https://docs.astral.sh/uv/)：

```powershell
uv sync --locked --python 3.11
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv lock --check
```

完整贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。Protocol、Daemon runtime、RequestStore、
Agent scheduler 或 locked TouchDesigner 验收变更，必须遵循
[td-runtime-reliability skill](.agents/skills/td-runtime-reliability/SKILL.md)。

<!-- doc-section: daemon -->

## Daemon

Daemon 是每位 Windows 用户一个的验证背景程序，只绑定 `127.0.0.1:9982`，状态位於
`%LOCALAPPDATA%\touchdesigner-cli`。

```powershell
uv run td-daemon start
uv run td-daemon status --json
uv run td-daemon stop
uv run td-daemon serve
```

固定配置包含 `state\daemon.db`、`state\auth.token`、`logs\daemon.log`，以及执行期间的非权威
`run\daemon.json`。只有在 Daemon 停止时删除 `state\auth.token` 才是手动 token recovery；之後
所有 Agent Component 都必须重新连接。

<!-- doc-section: agent-component -->

## Agent Component

`agent/` 下可审查的文件是规范来源；`td-agent.tox` 是本机派生文件且不进 Git。

```powershell
uv run td-agent inspect-source agent
uv run td-agent build-instructions --output path\to\td-agent.tox --source agent
uv run td-agent inspect-artifact path\to\td-agent.tox --source agent
```

Artifact inspection 需要 locked TouchDesigner build 产生的相邻
`td-agent.tox.manifest.json`。它将 artifact 绑定至 canonical source revision、
TouchDesigner `2025.32050` 与必要 DAT／Operator topology。实际 `.tox` 建置及 Online Instance
验证必须在固定版本 TouchDesigner 环境完成。

<!-- doc-section: operator-control -->

## 基本 Operator 控制

有多个 Instance 时务必使用明确 Selector。Protocol 提供 catalog 内建 Operator 创建、检查、
Parameter 设置与同 family 配线：

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

`ops.inspect` 是 CHOP、DAT、TOP、SOP、POP、MAT 的被动有界读取。它不下载 pixels、geometry、
POP buffers、DAT content 或 Python objects，也不主动 cook。可变长度数据受 `--max-items`
限制（默认 100、最高 1000），溢位会失败而不截断。

<!-- doc-section: parameter-control -->

## Parameter 控制

Parameter inspection 依 style 区分 boolean、integer、number、string、menu、单一 OP、最多 256
条路径的 Multi-OP、Pulse、Sequence header 与不透明 Python value。Python value 只会报告不支援，
不会序列化。disabled、read-only、hidden／obsolete、类型不符及被 TouchDesigner clamp 的写入都不会
报告成功。

```powershell
td --json --instance <selector> parameters get /project1/source colorr
td --json --instance <selector> parameters set /project1/target Targetop --operator /project1/source
td --json --instance <selector> parameters set /project1/target Targets --operators-json '["/project1/a","/project1/b"]'
td --json --instance <selector> parameters set /project1/target Gain --bind-source-operator /project1/source --bind-parameter Gain
td --json --instance <selector> parameters sequence-get /project1/target Items
td --json --instance <selector> parameters sequence-replace /project1/target Items --blocks-json '[{"name":"first","parameters":[{"parameter":"value","mode":"constant","value":1.5}]}]'
```

Bind source 只由类型化 Operator／Parameter identity 产生。Export 只接受已存在的 CHOP
Operator／channel identity。Sequence replacement 最多 128 blocks、每 block 256 Parameters；失败时会
恢复并验证完整的 block 数量、顺序、名称、mode、value 与 source。

<!-- doc-section: regular-connections -->

## Regular Connection

变更 graph 前先检查所有 input／output connector；inventory 有界，溢位会失败：

```powershell
td --json --instance <selector> ops connections /project1/source --max-connections 256
```

Connect 默认拒绝已占用 input，只有明确 `--replace` 才可替换；disconnect 必须指定精确
source/output 与 target/input。

<!-- doc-section: hierarchy-connections -->

## COMP Hierarchy Connection

Hierarchy Connection 是 Object COMP 或 Panel COMP 的上到下 parent／child connector，与 Regular
Connection 不同，也不能跨 hierarchy kind。Connect 会在 mutation 前拒绝跨 kind、非 COMP、cycle、
缺少或已占用 endpoint；`--replace` 会保存并在失败时恢复原连接。

```powershell
td --json --instance <selector> ops hierarchy connections /project1/geo1 --max-connections 256
td --json --instance <selector> ops hierarchy connect /project1/geo1 /project1/geo2
td --json --instance <selector> ops hierarchy disconnect /project1/geo1 /project1/geo2
```

root、Agent Component 及其 ancestors／descendants 都受保护。Hierarchy 读取可批量；mutation 不可批量。

<!-- doc-section: structural-mutations -->

## 结构 mutation

Create、rename、copy、move、destroy 使用精确 path／name，拒绝 root、Agent tree、自动命名、collision
与超大 subtree。Destroy 非空或有连接的 Operator 需要明确 opt-in。Move 是已验证的
copy-then-destroy，会改变 Operator identity。

```powershell
td --json --instance <selector> ops copy /project1/source /project1/group copied
td --json --instance <selector> ops move /project1/source /project1/group moved --allow-connected
td --json --instance <selector> ops destroy /project1/group/moved --recursive --allow-connected
```

三种操作默认最多影响 256 Operators（最高 1000）。Copy／move 会验证精确结果，并在失败时移除
新 copy；无法证明最终状态时回传独立 rollback／uncertain-outcome error。

<!-- doc-section: trusted-tox-import -->

## Trusted TOX Import

Trusted TOX Import 只接受 allowlist root 下既存的 absolute local `.tox`，且呼叫者必须传
`--trusted`。TOX 是可执行 TouchDesigner 内容；td-cli 不会 sandbox，也不能恢复 filesystem、network、
process 或其他 graph 外副作用。

```powershell
td --json --instance <selector> ops tox import /project1/imports C:\approved\asset.tox C:\approved asset --trusted
```

它会限制并验证目的 Operator graph，拒绝 external TOX linkage 与 VFS，也不保存项目。Replace
先创建、独立还原并比对 in-memory backup，再移除原目的。失败时恢复并验证；cleanup、identity 或
rollback 无法证明时回传明确 uncertain outcome。文件默认限制 64 MiB，inventory 默认 256、最高
1000 Operators，所有限制都以失败而非截断处理。

<!-- doc-section: operator-state -->

## Common Operator state

Common state 的读取与 atomic partial update 涵盖 node position／size、RGB color、comment、Bypass、
Lock、Viewer 与 Expose。每个栏位都会 read back；clamp 或拒绝会 rollback 整个 patch。

```powershell
td --json --instance <selector> ops state get /project1/source
td --json --instance <selector> ops state set /project1/source --node-x -100 --node-width 140 --color 0.1 0.2 0.3 --comment "source" --bypass --no-expose
```

Comment 最多 4096 字元，座标 -32768 到 32767，正尺寸最高 32767，RGB 是 0 到 1 的 finite number。
Family-specific Display／Render／Allow Cooking、selection、current viewer、storage、任意 attribute 与
Python object 不在此接口内。

<!-- doc-section: dat-content -->

## Text／Table DAT content

Text 读取与完整替换保留 Unicode 和空字串。Table 读取回传总尺寸与明确 rectangular window；replace
设置完整 table，patch 只更新既有范围而不 resize。

```powershell
td --json --instance <selector> dat text get /project1/notes
td --json --instance <selector> dat text set /project1/notes "繁体内容"
td --json --instance <selector> dat table get /project1/grid --row-offset 0 --column-offset 0 --row-count 16 --column-count 16
td --json --instance <selector> dat table replace /project1/grid '[["name","value"],["alpha",""]]'
td --json --instance <selector> dat table patch /project1/grid '[["updated"]]' --row-offset 1 --column-offset 1
```

只接受精确 `textDAT`／`tableDAT`。外部 File／Sync File、protected path、非 rectangular／非 string
cell 及越界 patch 都会被拒绝。Content 限制 32 KiB UTF-8、256 rows、256 columns、4096 cells、每
cell 16 KiB。Mutation 会 read back 完整内容与尺寸，失败时恢复整份 DAT。这些 Command 不执行
DAT、不 import module、不 evaluate content，也不接受 filesystem path。

<!-- doc-section: operator-catalog -->

## 固定版本 Operator catalog

TouchDesigner 2025.32050 catalog 涵盖七个 family 的 680 个 built-in types：478 个默认支援、165 个
具副作用或依赖环境的 type 需要 `ops create --allow-conditional`、37 个不支援，没有未分类的固定版本
built-in。Custom、第三方及其他 build 不在此 inventory，会被拒绝直到对应 build probe 提供证据。
机器可读细节与失败证据位於
[`agent/touchdesigner-2025.32050-operators.json`](agent/touchdesigner-2025.32050-operators.json)。

每个新 TouchDesigner build 都必须重新 probe，并依 supported／conditional／unsupported／unknown
流程审查。创建与 rename 拒绝 collision；mutation 不得进入 `batch.execute`，batch 仅供有界、
read-only Command 使用。
