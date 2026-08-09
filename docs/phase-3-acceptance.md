# Phase 3 bounded observation and orchestration

Date: 2026-08-09

Platform: Windows 11, TouchDesigner 2025.32050, Python 3.11

## Protocol v1 contract

Phase 3 adds five independently advertised Commands while preserving strict
input validation, the 256 KiB Agent result envelope, durable Request semantics,
and the existing Protocol v1 error enum.

- `project.snapshot`: breadth-first Operator metadata from one canonical root;
  `max_depth` is 0 through 8 and `max_operators` is 1 through 1000.
- `binary.export`: read-only base64 export. `tox` accepts COMP and uses native
  `saveByteArray()`; `png` accepts TOP and uses `saveByteArray('.png')`. Raw
  data is at most 194,560 bytes. Results include byte count and SHA-256. No
  arbitrary file read, import, or write is exposed.
- `batch.execute`: 1 through 16 existing typed Operator/Parameter Commands.
  Every item is preflighted before any mutation, then items execute in order.
  This is deliberately not an atomic transaction: a runtime failure after
  preflight may leave earlier writes.
- `project.metadata`: only name, folder, and saved-with version, build, time,
  OS name, and OS version. It cannot save, load, or quit a project.
- `events.read`: cursor reads from the last 1000 Agent Command outcomes, 1
  through 200 at a time, optionally with at most 100 current recursive Operator
  errors. It does not claim to capture the separate Console or all Textport
  output.

The overall 256 KiB result guard remains authoritative. The development-only
diagnostic bridge remains loopback-only, bearer-token authenticated,
unsandboxed, and outside this public surface and all Release Artifacts.

## Locked-runtime evidence

The retained bridge rebuilt the source-first Agent Component on the
TouchDesigner main thread. Independent artifact inspection passed with the
required seven-Operator topology.

- Canonical Agent source revision:
  `18fc77dc3ba0ace8b5ba92b42640af734bb61d18bb5a575475f8388d9318e978`
- Local derived artifact SHA-256:
  `391b872f182c966aa1bc125d6387faebe678bb6e3e0e1596e4be91755d7472d7`

The Online TouchDesigner Instance advertised all ten capabilities. Public
`td --json` calls proved project metadata, a depth-one `/project1` snapshot,
ordered two-item batch set/readback, cursor event/error reads, and COMP `tox`
export of `/project1/td_agent` (4,942 bytes; SHA-256
`5620425a8c535a7a95ebbed4eb55b21066cb1c048a5fa6680c839805cc31895d`).

Live iterations caught and corrected persistent-Daemon schema staleness,
Phase 2 Agent state migration, explicit injection of TouchDesigner's `project`
object, and the exact no-argument COMP `saveByteArray()` call.
