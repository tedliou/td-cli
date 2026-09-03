# Production runtime reliability acceptance

## Scope and authorities

This gate covers Protocol v2, Daemon persistence and lifecycle ownership, the
TouchDesigner Agent scheduler, SocketIO transport, extension initialization,
disconnect recovery, and outcome replay. The design baseline and testing-cost
rules are recorded in `docs/research/runtime-reliability-primary-sources.md`.

The locked-runtime implementation follows Derivative's documented Extension
`onInitTD`/`onDestroyTD` lifecycle, SocketIO DAT `Active` and `Reset`
parameters, and independent `run(..., delayRef=op.TDResources)` scheduling:

- https://docs.derivative.ca/Extensions
- https://docs.derivative.ca/SocketIO_DAT
- https://docs.derivative.ca/Run_Command_Examples

Socket.IO guarantees event ordering but defaults to at-most-once arrival, so
the application retains outcomes until an explicit Daemon acknowledgment and
replays them after reconnect. It does not retry an authorized mutation:
https://socket.io/docs/v4/delivery-guarantees/

## 2026-09-03 outcome transport correction

The v0.3.0 Agent used a direct SocketIO DAT event for an outcome that fit in
one chunk, but every outcome has either a null `result` or null `error`.
TouchDesigner 2025.32050 rejected that raw dictionary in `emitOutcome`, so the
Request remained `running` and the per-Instance FIFO correctly held later
Requests in `queued`. Reconnect happened to succeed because retained outcomes
were already forced through the JSON-string chunk path.

The corrected Agent has one outbound outcome path: every immediate and replayed
outcome uses the existing bounded JSON chunks. A small outcome is still one
Socket.IO event. The Daemon continues to accept the v0.3.0 direct event for
Protocol 2 receive compatibility, but the Agent does not use it as a fallback.

Locked TouchDesigner 2025.32050 rebuilt source revision
`3d37625b83d9410dc63f8048f64527f38d53589bea36387b04b4aa065c7217da`
as artifact SHA-256
`66c2ec1b1f3faea180929c20c5ba861789221164d011e735a7b3f729d75f935b`.
With the root timeline paused, the corrected artifact produced these outcomes:

- `ops children /project1` and `project metadata` both completed on their first
  Connection generation; Daemon dispatch-to-outcome time was 31 ms for each;
- create, inspect, and destroy of `/project1/outcome_fix_probe` all succeeded,
  and the Agent retained zero records after acknowledgment;
- a 50 ms Daemon process kill during a 1,000-Operator trusted import did not
  retry the mutation; reconnect replay refined the same Request ID to
  `succeeded`, after which the 1,000-Operator probe was removed;
- ten Agent reinitializations left no Operator errors, and the 6.5-second
  window still emitted exactly three heartbeats;
- with one Online Instance, the Daemon used 31.25 ms CPU over 10 seconds
  (0.312% of one core), retained a 69.31 MiB working set with zero measured
  growth, and added no polling loop; and
- the frozen Daemon completed `start`, `status`, `stop`, and restart twice with
  no new visible top-level window and a zero `MainWindowHandle` on the serve
  process.

The maximum locked execution measurements were 0.019 ms for `fast_read`,
2.126 ms for `bounded_scan_or_export`, 18.353 ms for `bounded_mutation`, and
123.707 ms for `trusted_asset_mutation`. They are within the previously
recorded margins; the transport correction adds no event for small outcomes
and removes one runtime branch.

## Locked TouchDesigner evidence

The disposable acceptance harness in `tools/locked_runtime_acceptance.py` ran
with TouchDesigner **2025.32050**, the root timeline paused, and no Operator
errors. Final canonical source revision
`a4b30d7e81920c2f3014ffb31705d664a7a5cfd3682bc0e9bd831fdb69eb5eea`
produced local artifact SHA-256
`b0b87a53200f1f5470a12237a8519958429a1481a8ca7db5fe0e316f08cf7785`.
It proved:

- the scheduled probe progressed on `TDResources` time while the timeline was
  paused and all TouchDesigner access remained on the main thread;
- the source-built artifact loaded with the SocketIO DAT inactive, no auth
  table content persisted, and the runtime activated and cleared auth after
  connection;
- one immutable execution authorization produced exactly one graph mutation;
  a second call with the same execution identity was a no-op;
- ten consecutive Extension reinitializations ended Online with one current
  Connection, an empty auth table, no Operator errors, and exactly three
  heartbeat emissions in a 6.5-second observation window (one scheduler loop);
- saving the Online disposable project and expanding that `.toe` with official
  `toeexpand` produced a 19-byte empty Table DAT encoding with no 64-hex token;
- Power Off is unsupported because it stops communication and clocks.

Maximum-input synchronous measurements (11 samples except trusted import with
3) were:

| Execution class | Cold | Warm median | Maximum | Perform FPS before/after | 60 FPS budgets at maximum |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fast_read` | 0.020 ms | 0.004 ms | 0.020 ms | 23 / 23 | 0.001 |
| `bounded_scan_or_export` | 2.130 ms | 1.602 ms | 2.130 ms | 23 / 23 | 0.128 |
| `bounded_mutation` | 16.183 ms | 18.355 ms | 18.437 ms | 23 / 23 | 1.106 |
| `trusted_asset_mutation` | 89.107 ms | 133.029 ms | 133.494 ms | 23 / 23 | 8.010 |

Every case had a zero frame delta while executing: the main thread could not
advance during the synchronous call. The locked Perform CHOP was forcibly
cooked immediately before and after each class. It exposed `fps=23` but no
separate dropped-frame channel while the root timeline was paused. Therefore
the report records the directly observed FPS and derives blocked 60 FPS frame
budgets from wall time; it does not claim a rendered dropped-frame count.
Default leases use three 2-second heartbeat
intervals plus `ceil(10 * locked_max_seconds)`: 7 seconds for the first three
classes and 8 seconds for trusted asset mutation. This derives the fault
containment budget from observed legal work while allowing three heartbeat
intervals rather than preserving arbitrary 30/120-second values.

## Crash and transport evidence

A legal 1,000-Operator trusted TOX outcome serialized to 74,695 bytes. The
locked SocketIO DAT delivered registration but did not deliver that single
large event. The transport now sends canonical outcomes in ordered,
identity-checked chunks of at most 24 KiB; the Daemon accepts at most 16 chunks
and 256 KiB total, rejects gaps/reordering/identity changes, and discards partial
assemblies on disconnect.

The Daemon was forcibly terminated 50 ms after submission of that same trusted
import. The Agent retained the completed outcome and rebound it from the old to
the new Connection ID. After Daemon restart, four ordered chunks refined the
persisted Request from `unknown` to `succeeded`, preserved the complete 1,000
entry inventory, removed the Agent record only after acknowledgment, and left
the Instance Online. No automatic mutation retry occurred.

An orderly `td-daemon stop` then produced a null Agent Connection ID and an
empty retained-record set. `td-daemon start` reconnected the same runtime with
a new Connection ID and returned the Instance to Online. This exercised the
`daemon_draining`, unregister, independent resume timer, and official SocketIO
reconnect path while the root timeline remained paused.

## Reproducible gates

The locked commands used for the final artifact and runtime evidence were:

```powershell
$tdProcess = Start-Process `
  'C:\Program Files\Derivative\TouchDesigner\bin\TouchDesigner.exe' `
  "$env:TEMP\td-cli-runtime-acceptance\Setfps.toe" -PassThru
Get-Content .tmp-locked-runtime-acceptance.json
Get-Content .tmp-locked-runtime-reinit.json
uv run td-daemon stop
uv run td-daemon status
uv run td-daemon start
uv run td --json instances list
Copy-Item td-agent.tox $env:TEMP\td-cli-artifact-inspect-final\artifact.tox
Push-Location $env:TEMP\td-cli-artifact-inspect-final
& 'C:\Program Files\Derivative\TouchDesigner\bin\toeexpand.exe' .\artifact.tox
Pop-Location
$artifactAuth = Get-ChildItem $env:TEMP\td-cli-artifact-inspect-final `
  -Recurse -File | Where-Object Name -eq 'auth_table.table'
$artifactAuth.Length
[Convert]::ToHexString([IO.File]::ReadAllBytes($artifactAuth.FullName))
Get-ChildItem $env:TEMP\td-cli-artifact-inspect-final -Recurse -File |
  Select-String '\b[0-9a-fA-F]{64}\b'
```

The exact 50 ms Daemon crash probe for the artifact above was:

```powershell
$requestId = '0199a111-2222-7333-8444-555566667778'
uv run td --json --instance 82d2 ops tox import `
  /project1/runtime_acceptance E:\td-cli\.tmp-runtime-acceptance-source.tox `
  E:\td-cli crash_replay_final_2 --trusted --replace --max-operators 1000 `
  --no-wait --request-id $requestId
Start-Sleep -Milliseconds 50
$state = Get-Content `
  $env:LOCALAPPDATA\touchdesigner-cli\run\daemon.json -Raw | ConvertFrom-Json
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $($state.pid)"
if ($process.CommandLine -notmatch 'td_cli\.daemon\.cli serve') {
  throw 'Daemon PID identity check failed'
}
Stop-Process -Id $state.pid
uv run td-daemon start
uv run td --json requests get $requestId
```

It returned `succeeded` with 1,000 inventory entries, then the Agent evidence
showed zero retained records and the Instance Online on the new Connection ID.
The repeatable automated queued/dispatched/running process boundary is:

```powershell
uv run pytest -q tests/test_process_crash_integration.py
```

Run the standard contribution gate plus:

```powershell
uv run pytest -q tests/test_agent_runtime.py tests/test_agent_callbacks.py tests/test_socket_transport.py
uv run python -m td_cli.agent_tool inspect-source agent
```

The final release gate prepares only verification whose `artifact_sha256`
matches both the structural manifest and artifact bytes, builds all three clean
executables, runs their clean-working-directory version smokes, and packages
the four documented ZIP layouts:

```powershell
$sourceCommit = git rev-parse HEAD
$sourceEpoch = git show -s --format=%ct HEAD
uv run python scripts/prepare_agent_stage.py --artifact td-agent.tox `
  --source agent --source-commit $sourceCommit `
  --verification .tmp-locked-verification.json `
  --output $env:TEMP\td-cli-agent-stage-final-3
uv run python scripts/build_release.py `
  --agent-stage $env:TEMP\td-cli-agent-stage-final-3 `
  --output $env:TEMP\td-cli-release-final-3 `
  --source-epoch $sourceEpoch --source-commit $sourceCommit
Get-FileHash $env:TEMP\td-cli-release-final-3\*.zip -Algorithm SHA256
```

Locked evidence requires the disposable Execute DAT wrapper
`tools/locked_runtime_execute_dat.py`; the generated `.toe`, `.tox`, JSON
evidence, and auth material are local test artifacts and are not committed.

## Limitations

- Evidence is from one Windows host and locked TouchDesigner 2025.32050; a
  different build must rerun the gate rather than reuse these numbers.
- Perform CHOP exposed FPS but no dropped-frame channel while the root timeline
  was paused, so blocked frame budgets are derived from measured main-thread
  wall time and the 60 FPS project rate rather than claimed as rendered drops.
- Power Off deliberately remains unsupported because it stops clocks and
  communication; no fallback transport or scheduler exists.
- Generated acceptance projects and JSON are disposable local evidence. The
  canonical committed evidence is this report plus the deterministic automated
  tests; Release publication remains a separate maintainer action.
