# Typed DAT content control acceptance

Date: 2026-08-11

Platform: Windows 11, Python 3.11, TouchDesigner 2025.32050 official build

## Public contract

This increment adds five independently advertised Protocol v1 Commands without
weakening the v0.1.2, structural-control, or Operator-state invariants:

- `dat.text.get` is a batchable, bounded, lossless UTF-8 read and
  `dat.text.set` is an atomic whole-content replacement.
- `dat.table.get` is a batchable rectangular-window read that also reports the
  complete dimensions. `dat.table.replace` atomically replaces content and
  dimensions; `dat.table.patch` atomically updates an exact in-bounds rectangle
  without resizing.
- Only exact `textDAT` and `tableDAT` Operators are accepted. Cells are strings;
  no content is evaluated or executed and no Command accepts a filesystem path.
- Content is bounded to 32 KiB UTF-8, 256 rows, 256 columns, 4096 cells, and
  16 KiB per cell. Reads fail instead of truncating.
- Mutation rejects root, the Agent Component tree and ancestors, File/Sync File
  modes, Lock, Replicator output, and clone-controlled ancestry. It snapshots
  the complete prior content before writing, verifies exact public readback,
  and restores the complete snapshot or reports distinct rollback/unknown
  outcome errors.

## Locked source and runtime inventory

The installed official TDI declares `DAT.text`, `DAT.numRows`, `DAT.numCols`,
cell access, `clear()`, and `setSize()`. Exact Text DAT and Table DAT types both
declare File and Sync File parameters. The common Operator interface declares
Lock and Replicator identity; clone ancestry is exposed through the ancestor
COMP Clone parameter. These facts define the conservative writable-mode guard.

A disposable live project then verified Unicode, empty content, complete table
dimensions, explicit windows, exact patching, type mismatch, File mode, Lock,
Agent Component protection, out-of-bounds patch rejection, bounded reads, and
batchable reads. Runtime mutation failures that require injected clamping,
disappearance, or rollback rejection are covered by Agent seam tests because
the public contract deliberately provides no arbitrary code-injection hook.

## Source-first Agent evidence

The Agent Component was rebuilt from this branch in TouchDesigner 2025.32050
and independently inspected before live use.

- Canonical Agent source revision:
  `ef1be625c7c0277a7b368e96544e866b122888067c0e5ac07333a4f11da1021d`
- Local derived artifact SHA-256:
  `5dc8d2797ef3aa78d091f07d7808cef1610c16b48b6c9553058cabaaaeb23a7e`
- Online Instance Selector: `f54b`
- Advertised capabilities: all 26 public Commands, including all five DAT
  content Commands
- Required eight-Operator artifact topology: passed

The project was an unsaved temporary copy of `E:\td-sample\Project.toe`.
Acceptance Operators lived under `/project1/tdcli_dat_acceptance`; the source
project was never modified.

## Locked-runtime behavior

1. Text replacement and readback preserved `繁體`, newlines, and emoji as 18
   UTF-8 bytes. Empty replacement read back as exactly zero bytes.
2. A 17-byte read limit rejected the 18-byte Text DAT with
   `result_too_large`; no truncated success was returned.
3. Table replacement produced an exact 3x3 Unicode/string table including an
   empty cell. A 2x2 offset window reported both its window and the complete 3x3
   dimensions. Rectangular patch changed only the requested cells.
4. A patch beginning at row 3 of the 3-row table failed with
   `table_dat_patch_out_of_bounds`. Whole replacement with `[]` produced and
   read back a true 0x0 table.
5. Text read against a Constant TOP failed with `dat_type_mismatch`.
6. A non-empty File parameter and Lock each failed mutation with
   `dat_content_not_writable`; both were restored and the original Unicode text
   remained unchanged.
7. Mutation of `/project1/td_agent/agent_extension` failed with
   `operator_mutation_forbidden`.
8. One `batch.execute` returned the Text read and an explicit Table window.
9. Live validation exposed a Windows cp950 output defect: the runtime result was
   correct but `ensure_ascii=False` raised `UnicodeEncodeError`. A regression
   test now requires ASCII-portable JSON whose parsed string remains exactly
   equal, and the live CLI then returned the emoji content successfully.

## Automated and packaged gates

- `uv run pytest -q`: `305 passed`.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: passed.
- `uv run mypy src`: passed with 16 source files checked.
- `git diff --check`: passed.
- Independent Standards review: zero findings.
- Independent Spec code review: zero findings before the final live-discovered
  output fix; final review is rerun after this evidence commit.

PyInstaller 6.15.0 rebuilt all three one-file executables. Each reported
`0.1.2`, and packaged `td.exe` performed a live Unicode Text DAT read.

- `td.exe`: `1131a7445bd0939c3c481989c3da575c7c53a0b5051a76b07b368871c6c74640`
- `td-daemon.exe`: `3e96628539ca5626a2492a87040d3acce55f7bd0706eb41b2ed7ee1146bbf58`
- `td-agent.exe`: `b9376f1a734271d0ffe3e1aeba92d0ec9e7df71fca1146db5838f00f5847f3f4`

These are local acceptance hashes, not immutable Release assets.
