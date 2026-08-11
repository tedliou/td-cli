# td-cli

Prototype of a local, authenticated control path between Codex and a
TouchDesigner Instance. The public `td` surface provides typed Operator and
Parameter control plus bounded project observation, binary export, batch
execution, project metadata, and event/error observation. It never exposes
arbitrary Python or remote network control.

## Requirements

- Windows x86-64
- TouchDesigner `2025.32050`

## Install and first use

Install the latest stable Release from PowerShell. The installer verifies the
published checksums and adds the executables to your user `PATH`:

```powershell
irm https://github.com/tedliou/td-cli/releases/latest/download/install.ps1 | iex
```

Open a new PowerShell window, confirm the installation, then start the Daemon:

```powershell
td --version
td-daemon start
```

Drag
`%LOCALAPPDATA%\Programs\touchdesigner-cli\current\td-agent.tox` into the
TouchDesigner project. Once the Agent Component is connected, list the
Instances, select an Online Instance, and create a supported Operator:

```powershell
td --json instances list
td --json --instance <selector> ops create /project1 constantTOP source
```

Use the same install command to upgrade to the latest stable Release, then
restart the Daemon. To uninstall the executables while preserving Daemon data
and TouchDesigner projects:

```powershell
irm https://github.com/tedliou/td-cli/releases/latest/download/uninstall.ps1 | iex
```

## Development

Python 3.11 and [uv](https://docs.astral.sh/uv/) are required.

```powershell
uv sync --python 3.11
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## Daemon

The Daemon is one authenticated background process per Windows user. It binds
only to `127.0.0.1:9982` and stores state under
`%LOCALAPPDATA%\touchdesigner-cli`.

```powershell
uv run td-daemon start
uv run td-daemon status --json
uv run td-daemon stop
uv run td-daemon serve
```

The fixed layout contains `state\daemon.db`, `state\auth.token`,
`logs\daemon.log`, and the non-authoritative `run\daemon.json` while running.
Deleting `state\auth.token` while the Daemon is stopped performs manual token
recovery; every Agent Component must reconnect afterward.

## Agent Component

Reviewable files under `agent/` are canonical. `td-agent.tox` is a derived local
artifact and is ignored by Git.

```powershell
uv run td-agent inspect-source agent
uv run td-agent build-instructions --output path\to\td-agent.tox --source agent
uv run td-agent inspect-artifact path\to\td-agent.tox --source agent
```

Artifact inspection requires the adjacent
`td-agent.tox.manifest.json` written by the locked TouchDesigner build. It ties
the artifact to the canonical source revision, TouchDesigner `2025.32050`, and
the required DAT/operator topology. Actual `.tox` creation and Online Instance
validation are performed locally in the locked TouchDesigner environment.

## Basic network control

List the Instances, select an Online Instance, and use an explicit Selector
whenever more than one is available. Protocol v1 can create cataloged built-in
Operators, inspect and configure their Parameters, and edit same-family wiring:

```powershell
td --json instances list
td --json --instance <selector> ops create /project1 constantTOP source --node-x -200
td --json --instance <selector> ops create /project1 constantTOP replacement --node-x -200 --node-y 150
td --json --instance <selector> ops create /project1 nullTOP output
td --json --instance <selector> parameters set /project1/source colorr --number 0.25
td --json --instance <selector> parameters list /project1/source
td --json --instance <selector> ops connect /project1/source /project1/output
td --json --instance <selector> ops rename /project1/output renamed_output
td --json --instance <selector> ops connect /project1/replacement /project1/renamed_output --replace
td --json --instance <selector> ops disconnect /project1/replacement /project1/renamed_output
td --json --instance <selector> ops children /project1 --op-type constantTOP
td --json --instance <selector> parameters get /project1/source colorr
```

Inspect every regular input and output connector before changing a graph. The
inventory is bounded and fails rather than returning a truncated topology:

```powershell
td --json --instance <selector> ops connections /project1/source --max-connections 256
```

Structural mutations use exact paths and names. They reject the root, the
Agent Component and its ancestors, automatic TouchDesigner names, collisions,
and oversized subtrees. Destruction requires explicit opt-in for non-empty or
connected Operators. Copy reports boundary wires that are not replicated.
Move is a verified copy-then-destroy operation, changes Operator identity, and
requires explicit opt-in before detaching boundary wires:

```powershell
td --json --instance <selector> ops copy /project1/source /project1/group copied
td --json --instance <selector> ops move /project1/source /project1/group moved --allow-connected
td --json --instance <selector> ops destroy /project1/group/moved --recursive --allow-connected
```

All three mutations default to a maximum affected subtree of 256 Operators
(`--max-operators`, maximum 1000). `ops copy --include-docked` is required to
copy externally docked Operators. Copy and move verify the exact result and
remove the created copy on failure; a distinct rollback or uncertain-outcome
error is returned when the requested final state cannot be proven. Neither
operation promises to rewrite DAT string literals, external systems, or every
path-bearing expression/reference.

Common Operator state has its own read and atomic partial-update Commands. The
locked common subset is node position, size, RGB color, comment, and the
Bypass, Lock, Viewer, and Expose flags. Every requested field is read back;
TouchDesigner clamping or rejection rolls the whole patch back. Root and Agent
Component protection is identical to structural mutation:

```powershell
td --json --instance <selector> ops state get /project1/source
td --json --instance <selector> ops state set /project1/source --node-x -100 --node-width 140 --color 0.1 0.2 0.3 --comment "source" --bypass --no-expose
```

The update accepts at most a 4096-character comment, coordinates from -32768
through 32767, positive dimensions up to 32767, and finite RGB components from
0 through 1. Family-specific Display, Render, and Allow Cooking semantics,
transient selection/current-viewer state, storage, arbitrary attributes, and
Python objects are not exposed by these Commands. Distinct unavailable,
failed, rollback-failed, and uncertain-outcome errors preserve honest state.

Text DAT and Table DAT contents use separate typed Commands. Text reads and
whole-content replacement preserve Unicode and empty text. Table reads return
the total dimensions plus an explicit bounded rectangular window; replacement
sets the complete table (including dimensions), while patch updates an exact
rectangle without resizing:

```powershell
td --json --instance <selector> dat text get /project1/notes
td --json --instance <selector> dat text set /project1/notes "繁體內容"
td --json --instance <selector> dat table get /project1/grid --row-offset 0 --column-offset 0 --row-count 16 --column-count 16
td --json --instance <selector> dat table replace /project1/grid '[["name","value"],["alpha",""]]'
td --json --instance <selector> dat table patch /project1/grid '[["updated"]]' --row-offset 1 --column-offset 1
```

Only exact `textDAT` and `tableDAT` Operators are accepted. Mutation rejects a
non-empty File parameter or enabled Sync File mode, root and Agent Component
protected paths, non-rectangular/non-string cells, and patches outside current
dimensions. Content is limited to 32 KiB of UTF-8, with at most 256 rows, 256
columns, 4096 cells, and 16 KiB per cell. Reads fail instead of truncating when
their explicit byte limit is exceeded. Every mutation reads back the exact
complete content and dimensions, then restores and verifies the entire prior
DAT on failure; distinct unavailable, non-writable, rollback-failed, and
uncertain-outcome errors preserve honest state. These Commands never execute
DATs, import modules, evaluate content, or accept filesystem paths.

The locked TouchDesigner 2025.32050 catalog covers 680 built-in types across all
seven Operator families: 478 are supported by default, 165 side-effect or
environment-dependent types require `ops create --allow-conditional`, 37 are
unsupported, and none of those locked built-ins remain unknown. Custom,
third-party, and later-build OP types are outside this inventory and are
rejected as unknown until a matching locked-build probe classifies them. The
machine-readable details and failure evidence are in
[`agent/touchdesigner-2025.32050-operators.json`](agent/touchdesigner-2025.32050-operators.json).

Unsupported types are: `audioenvelopeCHOP`, `audiomixCHOP`,
`audiopitchshiftCHOP`, `bandeqCHOP`, `clipblender67CHOP`,
`clipblenderosCHOP`, `engineoutCHOP`, `engineoutDAT`, `engineoutPOP`,
`engineoutTOP`, `etherdreamCHOP`, `fontSOP`, `graphCOMP`, `heliosdacCHOP`,
`indicesDAT`, `legacyoscillatorCHOP`, `networkCOMP`, `parametriceqCHOP`,
`passfilterCHOP`, `phonemeCHOP`, `pitchCHOP`, `pointMAT`, `realsenseCHOP`,
`scanCHOP`, `shaderSOP`, `sharedmeminMAT`, `sharedmemoutMAT`, `spectrumCHOP`,
`svgTOP`, `touchinMAT`, `touchoutMAT`, `udtinDAT`, `udtoutDAT`, `webDAT`,
`xblendCHOP`, `xclipblenderCHOP`, and `xdeformSOP`. They remain rejected until
their non-default creation requirements can be proven and implemented with a
typed adapter. Each future locked TouchDesigner build will be re-probed so new
or changed types enter the same supported/conditional/unsupported/unknown
review path.

Create and rename reject collisions instead of accepting TouchDesigner's
automatic naming. Connect rejects occupied inputs unless `--replace` is
explicit; disconnect always names the exact source/output and target/input.
Network mutations are not allowed inside `batch.execute`, while read-only
`parameters.list` is batchable.
