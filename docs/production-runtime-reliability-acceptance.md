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

## Locked TouchDesigner evidence

The disposable acceptance harness in `tools/locked_runtime_acceptance.py` ran
with TouchDesigner **2025.32050**, the root timeline paused, and no Operator
errors. Final canonical source revision
`a4b30d7e81920c2f3014ffb31705d664a7a5cfd3682bc0e9bd831fdb69eb5eea`
produced local artifact SHA-256
`f4dc954e56d6339fcb48861cf099dc6755b8c546d007771e27dc4cb225588d21`.
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
- Power Off is unsupported because it stops communication and clocks.

Maximum-input synchronous measurements (11 samples except trusted import with
3) were:

| Execution class | Locked maximum |
| --- | ---: |
| `fast_read` | 0.025 ms |
| `bounded_scan_or_export` | 2.082 ms |
| `bounded_mutation` | 18.526 ms |
| `trusted_asset_mutation` | 135.947 ms |

Every case had a zero frame delta while executing, which is expected for a
synchronous main-thread call. Default leases use three 2-second heartbeat
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

## Reproducible gates

Run the standard contribution gate plus:

```powershell
uv run pytest -q tests/test_agent_runtime.py tests/test_agent_callbacks.py tests/test_socket_transport.py
uv run python -m td_cli.agent_tool inspect-source agent
```

Locked evidence requires the disposable Execute DAT wrapper
`tools/locked_runtime_execute_dat.py`; the generated `.toe`, `.tox`, JSON
evidence, and auth material are local test artifacts and are not committed.
