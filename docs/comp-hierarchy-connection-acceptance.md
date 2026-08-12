# COMP hierarchy connection control acceptance

Date: 2026-08-12

Platform: Windows 11, Python 3.11, TouchDesigner 2025.32050 official build

## Locked-runtime contract

TouchDesigner exposes regular left/right data connectors through
`OP.inputConnectors` and `OP.outputConnectors`. COMP hierarchy connectors are a
separate top/bottom model exposed through `COMP.inputCOMPConnectors` and
`COMP.outputCOMPConnectors`; the public Commands do not combine the two models.

`ops.hierarchy.connections` returns the exact canonical COMP path, runtime
hierarchy kind, every connector index and description, nullable singular input
source, ordered output fan-out, and exact connection count. Reads are bounded
to an explicit maximum of 1 through 1000 edges and fail with
`result_too_large` rather than truncate.

`ops.hierarchy.connect` and `ops.hierarchy.disconnect` require existing exact
COMP paths, connector indexes from 0 through 255, the same parent network, and
the same `object` or `panel` hierarchy kind. Cross-kind, non-COMP,
non-hierarchy COMP, missing, cyclic, occupied, and Agent-tree endpoints are
rejected before mutation. Connect treats an already-existing exact edge as a
verified no-op. Replacement requires explicit `replace=true`, snapshots the
prior exact source, verifies both connector directions, and restores the prior
edge on failure. Disconnect verifies the exact edge before and after mutation.

Structural destroy and move treat hierarchy edges like regular edges for
explicit connection authorization. Copy reports hierarchy boundary edges as
unreplicated with `connection_kind: hierarchy`; move reports them as detached.

## TouchDesigner 2025.32050 inventory

The locked TDI `Connector` class documents regular and component connectors as
two distinct types. Connecting an input replaces its single source; connecting
an output appends a target. Live `Geometry`, `Camera`, and `Light` COMPs exposed
one `object` input named `parent` and one output. Live `Container` and `Button`
COMPs exposed one `panel` input and one output. A Base COMP was neither Object
nor Panel and had no compatible hierarchy input; a Text DAT had no COMP
hierarchy interface.

An Object output successfully fanned out to two Object inputs. Reconnecting one
input removed only that target from the prior source and attached it to the new
source. Panel-to-Panel succeeded. Object-to-Panel, Panel-to-Object, Base-to-
Object, Base-to-Panel, and same-kind COMPs in different parent networks were
rejected by the locked runtime. Disconnecting the target input removed the
edge from both connector directions.

## Source-first Agent and live evidence

- Canonical Agent source revision:
  `f3957249091987d5a9c371860e28dc9f634e8d3738ddeb49b1eb8b3cc091eab2`
- Local derived artifact SHA-256:
  `0c8020bae63f35c9d8b3dfa2ac5d61d156f126e98578fa3ca1834efe10bc7da0`
- Online Instance Selector: `8023`
- Advertised capabilities: all 31 public Commands
- Required eight-Operator artifact topology: passed

Isolated `tdcli_` networks verified Object and Panel inventory, output fan-out,
occupied rejection without mutation, exact no-op, explicit replacement with
the prior source in the result, cycle rejection, same-parent rejection,
cross-kind rejection, exact disconnect, and overflow failure without
truncation. A connected root Geometry COMP proved that destroy and move reject
a hierarchy boundary by default. Copy reported the exact unreplicated hierarchy
edge, while explicitly authorized move reported the exact detached edge.

Automated failure injection separately proves replacement rollback, rollback-
failure classification, disappearance as uncertain outcome, complete
bidirectional readback, root and Agent-tree protection, and hierarchy edges in
structural guards.

## Automated and packaged gates

- `uv run pytest -q`: `346 passed` at the final source-first build point.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: passed.
- `uv run mypy src`: passed with 16 source files checked.
- `git diff --check`: passed.
- PyInstaller 6.15.0 rebuilt all three one-file executables and both Agent
  distributions. Packaged `td.exe` reported v0.1.2 and performed live hierarchy
  inventory, connect, and disconnect against the Online Instance.

Local acceptance archive SHA-256 values (not immutable Release assets):

- `td-v0.1.2-windows-x86_64.zip`:
  `914fe7deabfda9e2d7803efbb76251033a25109e31cca497afd6d01d53727811`
- `td-daemon-v0.1.2-windows-x86_64.zip`:
  `fd99493404f2251f9ba43145387ee1e906059923b1cffaada3eb5e80c7459ed9`
- `td-agent-cli-v0.1.2-windows-x86_64.zip`:
  `9d958dd022b0c42cdad900d0577f5a36066a761af23e5f22b81f7bf8c29fe029`
- `td-agent-component-v0.1.2-td2025.32050.zip`:
  `d37146f6fd2fcf6c618321301d46a7ab81f66cba1eca668c94d5c0d177c064c8`
