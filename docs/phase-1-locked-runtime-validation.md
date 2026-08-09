# Phase 1 locked-runtime validation

Validation date: 2026-08-09

Platform: Windows 11, TouchDesigner 2025.32050

Baseline commit: `8a6894f4cc3acc2e33cf1a12ec1a3df8c46d74e6`

Agent version: `0.1.0.dev0`

Protocol: v1

## Decision

Phase 1 is ready to proceed. The canonical Agent Component builds, inspects,
loads, authenticates, registers, heartbeats, handles a diagnostic Request,
reconnects with a new Connection ID, remains retained while Offline, supports
an explicit update rollback, and drains cleanly in the locked runtime.

Validation initially exposed two related TouchDesigner runtime contract bugs.
They were fixed and retested in the same validation loop:

- `socketioDAT.emit` accepts a payload in TouchDesigner 2025.32050 only through
  the `data=` keyword. Positional payloads raised `td.tdError` before an event
  left TouchDesigner.
- A newly created Table DAT starts with an empty row. The builder now clears the
  server-event table before appending event names; otherwise incoming events
  were not subscribed.

No bearer token or authorization header was printed or copied into this
report. Local log inspection replaced the token with `<REDACTED>` before
display. Connection and Instance IDs below are non-secret runtime identifiers.

## Reproducible commands

The localhost diagnostic bridge was already installed in the disposable
development project and remained bound to `127.0.0.1` throughout.

```powershell
uv run td-daemon start
uv run td-daemon status --json
uv run --python 3.11 python tools/td_diagnostic_bridge/client.py health

$revision = uv run python -c "import json; from pathlib import Path; from td_cli.agent_tool import source_revision; m=json.loads(Path('agent/manifest.json').read_text()); print(source_revision(Path('agent'), m['required_files']))"
$code = "m=__import__('runpy').run_path(r'E:\td-cli\agent\build_td.py', init_globals=globals()); result=m['build'](r'E:\td-cli\agent', r'E:\td-cli\td-agent.tox', '$revision')"
uv run --python 3.11 python tools/td_diagnostic_bridge/client.py exec --code $code --timeout 30

uv run td-agent-tool inspect-source agent
uv run td-agent-tool inspect-artifact td-agent.tox --source agent
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run python -m compileall -q src agent tests tools
uv lock --check
git diff --check
```

Management and Request probes used Python's `urllib.request` with the bearer
token read directly from
`%LOCALAPPDATA%\touchdesigner-cli\state\auth.token`. The token stayed in the
process and was never included in output. TouchDesigner state probes were sent
through `tools/td_diagnostic_bridge/client.py exec`.

## Artifact and diagnosis evidence

Final canonical evidence after restoring the development environment:

- Source revision:
  `0863daf21f79fc59d1c8cd048a061752f31120ee459511e4a4606e38ba4ddad9`
- Final artifact SHA-256:
  `9258d9de317660ef751ad4d23309a7fbb06c19801b03a055bb342af4b787cc17`
- TouchDesigner build: `2025.32050`
- Required child topology:
  `agent_extension`, `agent_manifest`, `auth_table`, `events_table`,
  `heartbeat_execute`, `socket_callbacks`, `socketio1`
- `td-agent-tool inspect-source`: `valid: true`
- `td-agent-tool inspect-artifact`: `valid: true`
- Auth table: one two-column row; its value was not displayed
- SocketIO DAT: connected and active, canonical callback DAT selected
- Agent Component: `ext.Agent` initialized, Online, no callback script errors,
  and no recursive operator errors

A copied sidecar with source revision `stale-validation-probe` was inspected
against canonical source. Inspection exited `1` with:

```text
artifact is stale relative to canonical source
```

The copied artifact and sidecar were then deleted. This probe did not alter the
canonical local artifact.

## Runtime evidence

### Authentication and protocol negotiation

- An unauthenticated `GET /v1/health` returned `404`, disclosing no health
  information.
- The authenticated health endpoint returned ready state, release
  `0.1.0.dev0`, and Protocol v1.
- TouchDesigner's authentication input established the Socket.IO connection.
- Registration selected Protocol v1 and advertised only `diagnostic.ping`.

### Registration, heartbeat, and Request

The Agent Component registered as Online with:

- Instance ID: `2110dd68-0d08-4f78-a0ea-644efa301536`
- Initial verified Connection ID:
  `6df926b9-f7aa-4917-b98d-cdccd2f7cc73`

Two authenticated Instance snapshots three seconds apart showed
`last_heartbeat_at` increasing. A UUIDv7 Request carrying
`diagnostic.ping(message="live-phase-1", sequence=1)` was accepted with HTTP
`201` and reached:

```json
{"error":null,"result":{"message":"live-phase-1"},"status":"succeeded"}
```

### Offline retention and reconnect

The SocketIO DAT was disabled and left disconnected beyond the six-second
application heartbeat timeout. The Daemon retained the Instance as Offline and
reported a non-null `offline_expires_at`. Re-enabling the DAT before the
30-second retention expired preserved the Instance ID and registered a new
Connection ID:

- Previous Connection ID: `6df926b9-f7aa-4917-b98d-cdccd2f7cc73`
- Reconnected Connection ID: `52578bb8-fc70-48d7-8d19-deb496bd0245`
- Final state: Online, `offline_expires_at: null`

### Update and rollback

The current artifact was loaded into a separate previous-install holder before
building and loading a candidate artifact. The previous Agent Component stayed
present until the candidate reached Online state. Both represented the same
TouchDesigner Instance runtime session.

The candidate was then removed. The preserved previous Agent Component was
reconnected and returned Online with Connection ID
`7d376f8a-bc58-453a-b61a-5104f211725b`, without recursive errors. Candidate
operators and candidate files were deleted, and the canonical root Agent
Component was rebuilt and restored.

### Orderly draining

The formal lifecycle command was used:

```powershell
uv run td-daemon stop
uv run td-daemon status --json
```

The stop command exited `0`; stopped status exited `3`. Before restoring the
development environment, the Agent reported:

```json
{
  "callback_script_errors": "",
  "connection_id": null,
  "draining": true,
  "errors": [],
  "pending_results": 0,
  "socket_active": false
}
```

This proves the Agent received `daemon_draining`, emitted its final heartbeat
and unregister events, and disabled its SocketIO DAT without pending results.

The Daemon was then restarted and the canonical Agent Component rebuilt. Final
development state is Daemon running, diagnostic bridge healthy, Agent Online,
`draining: false`, SocketIO active, and no recursive errors.

## Check results

- `48 passed` with one existing Starlette deprecation warning
- Ruff lint: passed
- Ruff format check: passed
- Compile check: passed
- uv lock check: passed
- Canonical source inspection: passed
- Locked-runtime artifact inspection: passed
- Git diff check: passed
