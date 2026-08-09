# td-cli

Prototype of a local, authenticated control path between Codex and a
TouchDesigner Instance. The public `td` surface provides typed Operator and
Parameter control plus bounded project observation, binary export, batch
execution, project metadata, and event/error observation. It never exposes
arbitrary Python or remote network control.

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
