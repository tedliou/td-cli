# Bounded Operator family inspection acceptance

Date: 2026-08-12

Platform: Windows 11, Python 3.11, TouchDesigner 2025.32050 official build

## Locked-runtime contract

`ops.inspect` is one read-only, batchable public Command for CHOP, DAT, TOP,
SOP, POP, and MAT. It resolves one exact canonical Operator Path and returns a
discriminated uppercase runtime `family`, common cached metadata, and a typed
family-specific `details` object. Inspection uses `td.passive()` and never
calls `cook()`, samples channels, reads DAT content, downloads TOP pixels,
materializes SOP elements, or downloads current POP geometry.

Every variable-length collection is complete or fails with
`result_too_large`; `max_items` is 1..1000 with a default of 100. POP dimension
is covered by the same item bound. Every variable family-detail string has a
4096 UTF-8 byte ceiling. Non-finite or inconsistent runtime values fail with
`family_inspection_unavailable`; unsupported families and endpoint identity
races have distinct `operator_family_unsupported` and
`family_inspection_outcome_unknown` results.

## TouchDesigner 2025.32050 inventory

The locked TDI and isolated live runtime established these conservative
surfaces:

- CHOP: channel count and complete names, samples, rate/range, time-slice, and
  export metadata.
- DAT: table/text kind, shape, editability, export state, and nullable editing
  path; no cells, text, module, JSON object, or locals.
- TOP: resolution/depth, aspect, pixel formats, pass, and finite newest-slice W
  offset; no samples or arrays. A live Texture 3D returned a fractional offset,
  correcting the narrower installed TDI annotation.
- SOP: counts, finite 3D bounds, bounded attribute descriptors and group names;
  no point, primitive, or vertex values.
- POP: dimension, line-strip maximum, and allocated capacities obtained only
  with `max=True`; no current GPU counts, bounds, attributes, or values.
- MAT: no stable family-specific members exist, so `details` is intentionally
  empty rather than invented.

Representative isolated Operators were Constant and time-slice Noise CHOP;
Table and Text DAT; Constant and Texture 3D TOP; Box SOP; Box POP; and Constant,
Phong, and GLSL MAT. All returned their expected discriminator and typed shape.

## Source-first Agent and live evidence

The Agent was rebuilt from committed canonical source inside TouchDesigner
2025.32050, independently inspected, and used for the public-command checks.

- Canonical Agent source revision:
  `e5ad6b067a87cb43d516a6909d406e8998209a8a64498a8f8119f1b8012e50cd`
- Local derived artifact SHA-256:
  `cf163b9aa7ea7d315ab03176afc4aa4e97a9ca2e999cd765044c8eb919b2f89c`
- Source commit: `6902f9bb4f271ac947f87d0dda08618d86941d8e`
- Online Instance Selector: `8de0`
- Advertised capabilities: all 32 public Commands
- Required eight-Operator artifact topology: passed

The isolated Constant CHOP was explicitly cooked once and locked. Its cached
three-channel result was then inspected through the public CLI; `cookAbsFrame`
was `23978` both before and after inspection, proving the passive read did not
cook it. Setting `max_items=2` returned `result_too_large` for the three names
without truncation. `batch.execute` returned complete TOP and MAT inspections.
DAT nullable `editing_file`, POP allocated counts, Texture 3D, time-slice CHOP,
and three MAT variants also passed through the source-first Agent.

The original retained `Sample.toe` diagnostic session had reached its 600-frame
range and later contained a stale bridge executor without its process-local
builtins. It was closed without saving. Final evidence used a fresh unsaved
project with an extended test timeline; this was a harness correction, not an
inspection behavior change.

## Automated and packaged gates

- `uv run pytest -q`: `356 passed` at the source-first build point.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: passed.
- `uv run mypy src`: passed with 16 source files checked.
- `git diff --check`: passed.
- PyInstaller 6.15.0 rebuilt all three executables and both Agent
  distributions. Packaged `td.exe` reported v0.1.2 and performed live CHOP,
  POP, MAT, batch, and typed overflow checks against the Online Instance.

Local acceptance archive SHA-256 values (not immutable Release assets):

- `td-v0.1.2-windows-x86_64.zip`:
  `368a3e49605ead3539063ea9202da19f99964ef4c576c85709a73ef6d0c83b5e`
- `td-daemon-v0.1.2-windows-x86_64.zip`:
  `892897c381de8cfc3fa56127617e6618dc4bd2a5d71da3d32fe0e1029515af68`
- `td-agent-cli-v0.1.2-windows-x86_64.zip`:
  `d2c59db8537f3c4efa0ab915008d5524b4ef0a4159036942d3eafa89dca87b28`
- `td-agent-component-v0.1.2-td2025.32050.zip`:
  `b9fab7c4bfa597230143cc33ec3563fc855f453412638a93cc1038f563b28c97`
