# Typed Operator state and node metadata acceptance

Date: 2026-08-11

Platform: Windows 11, Python 3.11, TouchDesigner 2025.32050 official build

## Public contract

This increment adds two independently advertised typed Commands while
preserving the v0.1.2 and structural-control authentication, durability,
payload, protection, packaging, and lifecycle invariants:

- `ops.state.get` is a strict batchable read for the locked common Operator
  state subset: node position, node dimensions, RGB node color, comment, and
  Bypass, Lock, Viewer, and Expose flags.
- `ops.state.set` is a non-batchable atomic partial update. It accepts only the
  same common fields, requires at least one field, applies explicit numeric and
  text bounds, reads every requested value back, and returns the verified full
  common state plus the ordered `applied_fields` list.
- Root, the whole Agent Component tree, and every ancestor of the Agent
  Component remain protected. A failed update restores and verifies the entire prior common
  state or reports distinct `operator_state_rollback_failed` or
  `operator_state_outcome_unknown` errors. Temporarily unreadable state reports
  `operator_state_unavailable` rather than fabricated defaults.

The locked common subset deliberately excludes family-specific Display,
Render, and Allow Cooking semantics; transient selection/current/active-viewer
state; Show Docked state that the locked runtime did not restore reliably; and
storage, arbitrary attributes, Python objects, or expression evaluation.

## Locked-runtime inventory

The official TDI `OP` interface declares all selected fields readable and
writable. A disposable live probe then wrote and restored them on Base COMP,
Constant TOP, Constant CHOP, Text DAT, and Box SOP Operators.

The same probe established the exclusions and normalization rules:

- `allowCooking=false` was accepted only by COMP and rejected by TOP, CHOP,
  DAT, and SOP, so it remains family-specific.
- `showDocked` and `current` did not always restore their previous state and
  are not part of the atomic public interface.
- Display and Render were writable but retain family-specific meanings and are
  deferred to the family contracts.
- A requested node width of 1 was clamped to 54. Height 1 was clamped to 49 for
  COMP and 29 for the other probed families. The public schema permits positive
  bounded dimensions, but success requires exact readback; clamping therefore
  triggers rollback.
- TouchDesigner stores node color components as float32. Readback verification
  uses an explicit absolute tolerance of `1e-6` while returning the actual
  stored values.

## Locked source-first evidence

The loopback-only diagnostic bridge rebuilt the Agent Component from the
feature branch inside TouchDesigner 2025.32050. Independent artifact
inspection passed with the required eight-Operator topology.

- Canonical Agent source revision:
  `bf8fff8be3eb27fbf306b52c2cf5391169015990d6016ca69feeb3a923da3a04`
- Local derived artifact SHA-256:
  `48849946612fbdb055276cd5f2ec496f843ca2dc62caeb3807b34be2e07a0933`
- Final review-rerun Online Instance Selector: `ebb3`
- Advertised capabilities: all 21 public Commands, including
  `ops.state.get` and `ops.state.set`

The validation project was a temporary copy of `E:\td-sample\Project.toe`.
All disposable Operators lived under `/project1/tdcli_state_acceptance`; the
source project was never modified.

## Locked-runtime behavior

The public CLI seam proved:

1. Base COMP, Constant TOP, Constant CHOP, Text DAT, and Box SOP returned the
   same typed state shape. SocketIO integer flags were normalized to real JSON
   booleans at the Daemon seam.
2. One TOP update atomically applied all ten fields. Exact position and
   dimensions read back, RGB values read back with the expected float32
   representation, the comment was exact, and all four flags matched.
3. While that TOP remained locked, a patch requesting `node_x=999` and
   `node_width=1` detected runtime clamping, failed as `operator_state_failed`,
   and restored every earlier value including Lock, color, and comment.
4. Updating `/project1` failed with `operator_mutation_forbidden` and did not
   change it.
5. Packaged `td.exe` independently read the same live state through the public
   Daemon interface.
6. The final post-review Agent rejected mutation of
   `/project1/td_agent/agent_extension` with `operator_mutation_forbidden`,
   proving that protection covers the entire Agent Component tree rather than
   only its root.

Both the six-Operator inventory network and final two-Operator review network
were removed with public
`ops.destroy --recursive --allow-connected`; the following `ops.get` returned
`operator_not_found`. The diagnostic bridge was shut down and the unsaved
temporary project was closed. Temporary directories were sent to the Windows
Recycle Bin.

## Automated and packaged gates

- `uv run pytest -q`: `267 passed`.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: passed.
- `uv run mypy src`: passed with 16 source files checked.
- `git diff --check`: passed.
- PyInstaller 6.15.0 rebuilt all three one-file executables. From a clean
  temporary directory, `td.exe`, `td-daemon.exe`, and `td-agent.exe` each
  reported `0.1.2`; packaged `td.exe` executed `ops.state.get` against the
  Online Instance.

Local executable SHA-256 values (build evidence, not Release assets):

- `td.exe`: `a54f9af0f5e84aec744336c28a04cca0731f92c8d300e3b10758844ab548eaf0`
- `td-daemon.exe`: `f9c5da9cbf8ca9bf7d640cba1b51601e0c5da0d9f55c40916d96f7be63b5d51d`
- `td-agent.exe`: `35982dd00c8395b0a1a2b607da604f880c1fadbbdd46608e82cdab8dc4f66065`

These hashes are intentionally local and mutable until a later Wayfinder
ticket selects a Release version and stages immutable exact-commit artifacts.
