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
