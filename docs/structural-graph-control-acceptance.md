# Safe structural graph control acceptance

Date: 2026-08-11

Platform: Windows 11, Python 3.11, TouchDesigner 2025.32050 official build

## Public contract

This increment adds four independently advertised typed Commands without
weakening the v0.1.2 authentication, Request durability, payload bounds,
operator catalog, or release layout:

- `ops.connections` returns the complete bounded regular-connector inventory
  for one exact Operator Path. It includes connector descriptions, empty
  inputs, and exact source/output/target/input endpoints. It is read-only and
  batchable. Overflow fails with `result_too_large`; it never truncates.
- `ops.destroy` rejects the root, the Agent Component, and every ancestor of
  the Agent Component. A non-empty Operator requires `recursive=true`; any
  regular connection requires `allow_connected=true`; the affected subtree is
  bounded and returned with every detached connection.
- `ops.copy` uses `COMP.copy()` with an exact collision-free name, verifies the
  resulting canonical path, family, type, placement, and bounded affected
  forest, and reports boundary connections that TouchDesigner does not copy.
  Externally docked Operators require explicit `include_docked=true`.
- `ops.move` is an explicit copy-verify-destroy operation because TouchDesigner
  does not expose a parent setter. It rejects moves into the source subtree,
  requires explicit authorization to detach boundary connections, reports that
  Operator identity is not preserved, and removes the copy if source deletion
  fails. Distinct rollback and uncertain-outcome errors prevent false success.

The commands do not claim to rewrite DAT string literals, external systems, or
every path-bearing expression/reference. Network mutations remain excluded
from `batch.execute`.

## Locked source-first evidence

The retained loopback-only diagnostic bridge rebuilt the Agent Component from
the feature branch inside TouchDesigner 2025.32050. Independent artifact
inspection passed with the required eight-Operator topology.

- Canonical Agent source revision:
  `3525478402589750c2c9d9ae093d602923138152b2ee40a21618812c24a94f64`
- Local derived artifact SHA-256:
  `ed0e504ce1c11827a684648e89e8c40dcb00aedc418ef5b7418884ddff36aeb7`
- Online Instance Selector: `04ee`
- Advertised capabilities: all 19 public Commands, including the four new
  structural graph Commands

The validation project was a temporary copy of `E:\td-sample\Project.toe`.
All disposable Operators lived under `/project1/tdcli_structural`; the source
project was never modified.

## Locked-runtime behavior

The public `uv run td --json --instance 04ee` seam proved:

1. A `Noise TOP -> Null TOP` wire was reported from both directions with exact
   connector indexes and real TouchDesigner connector descriptions. Empty
   inputs were represented as JSON `null`.
2. Copy created the exact requested `noiseTOP`, preserved requested network
   coordinates, reported one affected Operator, and identified the source wire
   as unreplicated. The live SocketIO integer `0` for `include_docked` exposed a
   public typing defect; Daemon normalization now returns JSON `false` and has
   a regression test.
3. Moving an unconnected copy into a child COMP succeeded with
   `identity_preserved=false`. Moving the connected source required
   `--allow-connected`, returned the exact detached wire, and left the former
   Null TOP input explicitly unconnected.
4. Exact leaf destroy and bounded recursive destroy succeeded. Recursive
   cleanup reported four Operators in the happy-path network and five in the
   negative-path network; the final `ops.get` returned `operator_not_found`.
5. Real negative calls returned `operator_not_empty`, `operator_connected`,
   and `operator_mutation_forbidden` for an unapproved subtree, connected
   source, and `/project1` respectively. No negative call mutated its target.

The disposable `/project1/tdcli_structural` network was removed after both live
runs. `/project1/td_agent` and the diagnostic bridge were not targeted by any
public mutation.

## Automated and packaged gates

- `uv run pytest -q`: `241 passed` before the live-only boolean normalization;
  the focused normalization suite subsequently passed `13 passed`.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: passed.
- `uv run mypy src`: passed with 16 source files checked.
- `git diff --check`: passed.
- PyInstaller 6.15.0 rebuilt all three one-file executables from the feature
  branch. From a clean temporary directory, `td.exe`, `td-daemon.exe`, and
  `td-agent.exe` each reported `0.1.2`; packaged `td.exe` successfully executed
  `ops.connections /project1` against the Online Instance.

Local executable SHA-256 values (build evidence, not Release assets):

- `td.exe`: `242411ce4026e6ad4aae475a0ce48de9cb7f9722f1f68d42dbc80505ca983f47`
- `td-daemon.exe`: `76bfb0da6199edd2a72d6c6058a89d39869c60f3c60f52c0b2b41845b830101a`
- `td-agent.exe`: `de1bac8870c9b3178fbcb17f6fae75c80f7eb7811769f76529d823302d1f1d96`

These hashes are intentionally local and mutable until a later Wayfinder
ticket selects a Release version and stages an immutable exact-commit Agent
artifact.
