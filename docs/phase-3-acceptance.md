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
  `4f74b1c6286c8000c075f19dcead4ad3f6d88081dd643d251893309bc925792b`
- Local derived artifact SHA-256:
  `a2a7a6153d23240b73fdb6f3610def3e616ec2a689ca72ae8380021e68bcaf8c`

The Online TouchDesigner Instance advertised all ten capabilities. Public
`td --json` calls proved project metadata, a depth-one `/project1` snapshot,
ordered two-item batch set/readback, cursor event/error reads, and COMP `tox`
export of `/project1/td_agent` (4,942 bytes; SHA-256
`5620425a8c535a7a95ebbed4eb55b21066cb1c048a5fa6680c839805cc31895d`).

A locked-runtime negative batch first proposed `display=false` and then read a
missing Parameter. It failed with `parameter_not_found`; a subsequent public
read proved `display` remained `true`, so every item was preflighted before the
first mutation. A snapshot capped below the actual Operator count failed with
`result_too_large`, and the next event read contained both typed failures.

Live iterations caught and corrected persistent-Daemon schema staleness,
Phase 2 Agent state migration, explicit injection of TouchDesigner's `project`
object, and the exact no-argument COMP `saveByteArray()` call.

Finally, all three PyInstaller one-file executables were rebuilt. Their
`--version` entry points passed from a clean temporary directory, and the
packaged `td.exe --json project metadata` completed against the live Online
Instance.
