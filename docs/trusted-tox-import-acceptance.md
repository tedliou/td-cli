# Trusted TOX Import acceptance

## Contract

`ops.tox.import` is a non-batchable Trusted TOX Import for TouchDesigner
2025.32050. It requires an exact parent Operator Path, an existing absolute
local `.tox`, an explicit allowlist root, an exact target name, and
`trusted: true`. The TOX is executable project content: callbacks may run
before the load call returns, and td-cli does not claim to sandbox it or undo
filesystem, network, process, or other out-of-graph side effects.

The Command rejects UNC/device/ADS paths, links and reparse points, files
outside the allowlist, non-`.tox` files, changed files, files over the caller's
bounded size, collisions without `replace`, unsupported Operator types,
external TOX linkage, VFS content, ambiguous load shape, and inventories over
the caller's bound. No result is truncated.

Replacement loads and verifies the new graph before touching the destination,
then creates and independently restores an in-memory backup of the old graph.
After mutation, it verifies the exact destination identity and complete sorted
inventory. A failed commit restores and verifies the old graph. Typed errors
distinguish trust, path, size, collision, protection, load, verification,
backup, commit, rollback, and uncertain outcomes.

## Automated gates

- Protocol validation and non-batchability: `tests/test_protocol.py`
- Dedicated CLI submission: `tests/test_control_cli.py`
- File, staging, inventory, replacement, rollback, and cleanup behavior:
  `tests/test_agent_runtime.py`
- Socket.IO DAT boolean normalization: `tests/test_socket_transport.py`
- Typed client errors: `tests/test_client.py`

## Locked live evidence

Source-first acceptance ran in TouchDesigner 2025.32050 using only `tdcli_`
artifacts in an unsaved `Sample.toe` session:

- Canonical Agent source revision:
  `7e200c9f555ebf4667d3de9c86d83ab2fa6ce5ed547ccca1c1a2c54ada6ceda3`
- Local derived artifact SHA-256:
  `d7e2a1ad89da4c0aa41080bf4c6209aeb5006f1d965b765ddb9ee32a1a13e8be`
- Artifact topology: all eight required Operators passed independent inspection.
- Advertised capabilities: all 33 public Commands, including
  `ops.tox.import`.

Fresh import returned the exact `/project1/tdcli_imports/tdcli_asset` root and
its complete two-Operator Base COMP/Null TOP inventory, canonical source path,
278-byte size, and SHA-256. A second import without `replace` returned
`tox_destination_exists`; explicit replacement succeeded only after the
backup round-trip.

An active Execute DAT in a trusted TOX destroyed the new root only when its
copy reached the final parent. This injected a failure after the old target
was destroyed. The Command returned `tox_commit_failed`, restored
`tdcli_rollback/old_child`, and left no staging Operator. This also confirmed
that locked `conditional` executable types are admitted only behind the
explicit trust assertion. The callback is an intentional acceptance probe;
its out-of-graph effects would not be recoverable.

`max_operators=1` rejected the two-Operator TOX with
`tox_verification_failed` and no truncated result or residue. A nested COMP
whose `externaltox` survived serialization was rejected with the same typed
verification error. Import beneath `/` returned `tox_parent_protected`.

The first live submission exposed that SocketIO DAT materializes JSON `true`
as numeric `1`; the independent Agent adapter now accepts only `True/1` after
the daemon's strict Protocol validation, and a regression test locks that
transport seam. No security or product contract was relaxed.
