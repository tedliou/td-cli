# td-cli

[English](README.md) | [正體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md)

<!-- doc-section: overview -->

Prototype of a local, authenticated control path between Codex and a
TouchDesigner Instance. The public `td` surface provides typed Operator and
Parameter control plus bounded project observation, binary export, batch
execution, project metadata, and event/error observation. It never exposes
arbitrary Python or remote network control.

Cold process-service startup shares the visible `td --timeout` command budget (default 30 seconds), including readiness and Request waiting. `td-daemon start --timeout 30` sets its total startup budget. A launch timeout is an unknown outcome: inspect existing processes before opening again.

<!-- doc-section: requirements -->

## Requirements

- Windows x86-64
- TouchDesigner `2025.32050`

<!-- doc-section: install -->

## Install and first use

Install the latest stable Release from PowerShell. The installer verifies the
published checksums and adds the executables to your user `PATH`:

```powershell
irm https://github.com/tedliou/td-cli/releases/latest/download/install.ps1 | iex
```

Open a new PowerShell window, confirm the installation:

```powershell
td --version
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

The Daemon starts quietly when an online `td` command first needs it; `--help`,
`--version`, and the offline `ops types` catalog do not start it. Drag the Agent
into an already-open project; you do not need to reopen TouchDesigner. The
Agent's Connection page reports `waiting_for_daemon`, `connecting`, `online`, or
an explicit authentication/registration error. Windows uses local
`Win32_Process.Create` so the Daemon and explicitly launched TD are independent
of the invoking terminal's job. No startup console window is shown.

To open a saved project explicitly, without tying it to the shell lifetime:

```powershell
td --json project open C:/work/design.toe --executable 'C:/Program Files/Derivative/TouchDesigner/bin/TouchDesigner.exe'
td --json ops types choptoDAT
td-agent install-skill
```

`project open` returns an OS PID, not an online Agent guarantee, and never closes
an existing Instance. `install-skill` installs the bundled, versioned `td-cli`
skill to `$CODEX_HOME/skills/td-cli` (default `~/.codex/skills/td-cli`). Existing
files require explicit `--replace`; `--destination` selects another skill folder.
The skill links task-specific Derivative theory and actual CLI help. Its package
is included in `td-agent.exe`; no separate network download is needed.

Saving uses the existing live `project save <current-path> --expected-sha256
<disk-hash>` command. A successful result includes the on-disk digest; closing TD
is not required. A rejected disk precondition remains `project_file_changed`,
not `protocol_incompatible`. Keep the Request ID when a save times out, inspect
its outcome and disk state before making another mutation. An executable upgrade
still does not replace the Agent embedded in a saved project.

<!-- doc-section: development -->

## Development

Python 3.11 and [uv](https://docs.astral.sh/uv/) are required.

```powershell
uv sync --locked --python 3.11
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv lock --check
uv run python -m td_cli.agent_tool inspect-source agent
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the complete contribution workflow. Changes to the
Protocol, Daemon runtime, RequestStore, Agent scheduler, or locked TouchDesigner acceptance must
follow the [td-runtime-reliability skill](.agents/skills/td-runtime-reliability/SKILL.md).

<!-- doc-section: daemon -->

## Daemon

The Daemon is one authenticated background process per Windows user. It binds
only to `127.0.0.1:9982` and stores state under
`%LOCALAPPDATA%\touchdesigner-cli`. `td-daemon start` returns after the
background process is ready and does not leave a console window open.

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

Protocol v3 is the only runtime protocol; there is no v2 alias or fallback. A
Request moves through `queued`, `dispatched`, `accepted`, and `running` before a
terminal outcome. The Daemon persists before dispatch, permits one authorized
Request per Instance in FIFO order, and isolates every reconnect with a new
Connection ID. A disconnect after authorization becomes `unknown`; td-cli never
automatically retries it, though the same retained execution outcome may later
refine it to `succeeded` or `failed`.

<!-- doc-section: agent-component -->

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

The Agent reserves bounded outcome capacity before `request_accepted`, executes
only after a matching immutable execution authorization, and retains every
post-accept `succeeded`, `failed`, or `unknown` outcome until the Daemon records
it. Retention is limited to 64 records, 256 KiB per canonical outcome, and 16
MiB total. Outcomes above the locked SocketIO DAT's proven single-event envelope
are sent as ordered, identity-checked 24 KiB chunks and are reassembled before
the public result is committed. Command, heartbeat, and drain timers use TouchDesigner's independent
`TDResources` time reference while all TouchDesigner object access remains on
the main thread. Extension initialization uses the official SocketIO Reset
parameter, and clears the transient auth DAT after connection. Power Off mode is
not supported.

<!-- doc-section: offline-upgrade -->

## Upgrade the embedded Agent

`td-agent upgrade-project` is the fixed offline upgrade entry point, independent
of runtime Protocol. Its verified migration is canonical Agent 0.3.1 / 0.4.0 → 0.5.0
on TouchDesigner 2025.32050; identical 0.5.0 is a no-op. Arbitrary historical
or future versions are not implied. Save your work and close **all TouchDesigner
processes** first: the command refuses active processes and never closes them.
Use the new CLI bundle and its trusted artifact and manifest:

```powershell
$bundle = "$env:LOCALAPPDATA/Programs/touchdesigner-cli/current"
$project = (Resolve-Path ./MyProject.toe).Path
$sha = (Get-FileHash $project -Algorithm SHA256).Hash.ToLower()
& "$bundle/td-agent.exe" upgrade-project $project --artifact "$bundle/td-agent.tox" --manifest "$bundle/manifest.json" --tools-dir "C:/Program Files/Derivative/TouchDesigner/bin" --expected-sha256 $sha --timeout 90
```

The command uses locked vendor tools in scratch space, identifies the known
Agent, verifies every unrelated project file, creates a unique verified backup,
and atomically replaces the project. Unknown or modified Agents, ambiguous
matches, external linkage, unsupported builds, changed inputs and failed round
trips are rejected. Disabled external-TOX paths remain inert metadata.
Exclusive ownership of the closed project is required throughout. Pre-replacement
failure preserves the original; backups remain available for review.

Start the upgraded daemon before reopening the project and rediscovering the
Instance selector. Protocol rejection disables the connection until the next
Agent initialization; it does not retry incompatible protocols. Historical 0.3.1
tools lack this entry point: use the new `td-agent` for the first migration.
Future releases retain the command and explicitly extend the tested migration matrix.

<!-- doc-section: operator-control -->

## Basic network control

List the Instances, select an Online Instance, and use an explicit Selector
whenever more than one is available. Protocol v3 can create cataloged built-in
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
td --json --instance <selector> ops inspect /project1/source --max-items 100
td --json --instance <selector> parameters get /project1/source colorr
```

`ops.inspect` is a passive, batchable Operator Family Inspection for CHOP, DAT,
TOP, SOP, POP, and MAT. Its `family` discriminator selects a strict typed
`details` object; common cached memory, cook timing, Display, and Render
metadata is returned alongside it. Variable-length CHOP channel and SOP
attribute/group names are bounded by `--max-items` (default 100, maximum 1000)
and overflow fails without truncation. It never downloads pixels, geometry, POP
buffers, DAT content, or arbitrary Python objects and never explicitly cooks an
Operator. Existing dedicated Commands remain the content and mutation seams.

<!-- doc-section: parameter-control -->

Parameter inspection is style-driven and distinguishes booleans, integers,
numbers, strings, menus, single-OP references, bounded ordered Multi-OP
references, Pulse, Sequence headers, and opaque Python values. OP writes accept
only exact canonical paths (or `null`); Multi-OP writes accept at most 256 exact
paths. Python values are reported as explicitly unsupported without serializing
the object. Disabled, read-only, hidden/obsolete, mismatched, and clamped writes
are rejected before success is reported.

```powershell
td --json --instance <selector> parameters set /project1/target Targetop --operator /project1/source
td --json --instance <selector> parameters set /project1/target Targets --operators-json '["/project1/a","/project1/b"]'
td --json --instance <selector> parameters set /project1/target Gain --bind-source-operator /project1/source --bind-parameter Gain
td --json --instance <selector> parameters sequence-get /project1/target Items
td --json --instance <selector> parameters sequence-replace /project1/target Items --blocks-json '[{"name":"first","parameters":[{"parameter":"value","mode":"constant","value":1.5}]}]'
```

Bind sources are generated solely from a typed Operator/Parameter identity.
Export mode accepts a typed CHOP Operator/channel identity only when that exact
export already exists in TouchDesigner; Protocol v3 does not synthesize CHOP
export tables or emulate an export with an expression. Sequence replacement is
bounded to 128 blocks and 256 Parameters per block, reads back the complete
ordered state, and restores the prior block count, order, names, modes, values,
and sources if any mutation is rejected.

Create a new native custom parameter page on a COMP with `parameters page-create`. Definitions support 1–32 scalar `float`, `toggle`, or `menu` controls. Names start with an uppercase ASCII letter followed by lowercase letters/digits (maximum 32 characters). Floats use finite minimum/maximum/default values and hard clamps; menus have 1–32 unique names and matching labels. Existing pages or parameter names are rejected without replacement. The result includes verified descriptors and values; inspect/edit later with `parameters list/get/set`. Failed creation removes only the new page; a failed rollback or unknown outcome requires inspection before further mutation.

The ASCII-escaped input JSON plus one serialized target path per parameter is limited to 16,384 bytes to reserve space for result metadata.

```powershell
td --json --instance <selector> parameters page-create --input-file controls.json
td --json --instance <selector> parameters list /project1/controls
```

```json
{"operator_path":"/project1/controls","page":"Controls","parameters":[{"name":"Gyrox","label":"Gyro X","kind":"float","default":0,"minimum":-1,"maximum":1},{"name":"Manual","label":"Manual","kind":"toggle","default":true},{"name":"Source","label":"Source","kind":"menu","default":"manual","menu_names":["manual","device"],"menu_labels":["Manual","Device"]}]}
```

<!-- doc-section: regular-connections -->

Inspect every regular input and output connector before changing a graph. The
inventory is bounded and fails rather than returning a truncated topology:

```powershell
td --json --instance <selector> ops connections /project1/source --max-connections 256
```

<!-- doc-section: hierarchy-connections -->

COMP Hierarchy Connections are a separate top-to-bottom connector model for
compatible Object COMPs or compatible Panel COMPs. Inventory is bounded and
reports the runtime hierarchy kind, every input, every output, and every exact
endpoint. Connect rejects cross-kind, non-COMP, cyclic, missing, or occupied
endpoints before mutation. `--replace` snapshots the prior input and restores
it if the requested replacement cannot be verified:

```powershell
td --json --instance <selector> ops hierarchy connections /project1/geo1 --max-connections 256
td --json --instance <selector> ops hierarchy connect /project1/geo1 /project1/geo2
td --json --instance <selector> ops hierarchy disconnect /project1/geo1 /project1/geo2
```

Hierarchy reads are batchable; hierarchy mutations are not. The root, Agent
Component, its ancestors, and descendants are protected. Distinct occupied,
incompatible-kind, cycle, mutation-failed, rollback-failed, and
uncertain-outcome errors preserve honest state.

<!-- doc-section: structural-mutations -->

Structural mutations use exact paths and names. They reject the root, the
Agent Component and its ancestors, automatic TouchDesigner names, collisions,
and oversized subtrees. Destruction requires explicit opt-in for non-empty or
connected Operators, including COMP Hierarchy Connections. Copy reports
boundary wires that are not replicated and marks hierarchy edges explicitly.
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

<!-- doc-section: trusted-tox-import -->

Trusted TOX Import accepts one existing absolute local `.tox` beneath an
explicit allowlist root. The caller must pass `--trusted`: a TOX is executable
TouchDesigner project content and may run callbacks while loading. td-cli does
not sandbox it and cannot undo filesystem, network, process, or other
out-of-graph side effects. It does bound and verify the destination Operator
graph, rejects external TOX linkage and VFS content, and never saves the
project:

```powershell
td --json --instance <selector> ops tox import /project1/imports C:\approved\asset.tox C:\approved asset --trusted
```

Collisions are rejected unless `--replace` is supplied. Replacement first
creates an in-memory backup and independently restores and compares it in an
isolated temporary namespace. Only then may it remove the old destination. A
failed commit restores and verifies that backup; cleanup, disappearance, and
unprovable identity failures use distinct rollback or uncertain-outcome
errors. Files default to a 64 MiB maximum and inventories to 256 Operators
(maximum 1000); every bound fails rather than truncates.

<!-- doc-section: operator-state -->

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

<!-- doc-section: dat-content -->

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

Text access requires `textDAT`; table writes require `tableDAT`. Bounded table
reads accept any DAT with table-formatted data (`isTable`), including CHOP to DAT
for reading actual CHOP output values. Reads use normal dependency cooking, without
forcing cooks. Mutation rejects a
non-empty File parameter or enabled Sync File mode, root and Agent Component
protected paths, non-rectangular/non-string cells, and patches outside current
dimensions. Content is limited to 32 KiB of UTF-8, with at most 256 rows, 256
columns, 4096 cells, and 16 KiB per cell. Reads fail instead of truncating when
their explicit byte limit is exceeded. Every mutation reads back the exact
complete content and dimensions, then restores and verifies the entire prior
DAT on failure; distinct unavailable, non-writable, rollback-failed, and
uncertain-outcome errors preserve honest state. These Commands never execute
DATs, import modules, evaluate content, or accept filesystem paths.

<!-- doc-section: project-save -->
## Save the current project

`project.save` writes the existing current local `.toe` only. First inspect
`project metadata`, exclude other writers, then compute the disk digest:

```powershell
$projectPath = 'E:\artwork\Artwork.toe'
$digest = (Get-FileHash -LiteralPath $projectPath -Algorithm SHA256).Hash.ToLowerInvariant()
td --json --instance <selector> project save $projectPath --expected-sha256 $digest
```

The path and SHA-256 must match at preflight. This is not an atomic lock against
other processes. Files are bounded to 64 MiB; linked/reparse paths are rejected.
The command returns the actual disk path, byte count and SHA-256. It does not
save external TOX files or choose a new project name. A post-save verification
failure is `project_save_outcome_unknown`; inspect the retained Request and disk
before deciding what to do. Never automatically repeat an uncertain save.

Protocol v3 preserves boolean, integer and null Command values as JSON text at
the SocketIO boundary. Upgrade the CLI, Daemon and embedded Agent together;
v2/v3 registrations are rejected in both directions. Editable `StrMenu`
parameters such as Select CHOP `channames` accept arbitrary strings; ordinary
`Menu` parameters still require an advertised menu name.

<!-- doc-section: operator-catalog -->

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
