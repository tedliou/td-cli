# Complete typed Parameter model acceptance

Date: 2026-08-11

Platform: Windows 11, Python 3.11, TouchDesigner 2025.32050 official build

## Locked-runtime contract

The public model is keyed by TouchDesigner's runtime `Par.style`, `is*`
capabilities, and `ParMode`; it does not infer types from display text or from a
Python object's representation. Scalar reads distinguish constant, expression,
export, and bind modes. A typed source identity is a canonical CHOP
Operator/channel for Export or a canonical Operator/Parameter for Bind.

Single-OP constants are a canonical path or null. Multi-OP constants are an
ordered list of at most 256 canonical paths. Wildcards, traversal components,
missing Operators, opaque Python values, and implicit Operator creation are not
accepted. Python values remain inspectable as `python` with a null wire value
and `opaque_or_structural` reason.

Sequence inspection and replacement use an exact Operator path and Sequence
name. Replacement is limited to 128 blocks and 256 Parameter writes per block.
The Agent snapshots the complete ordered block representation, verifies the
complete public readback, and restores block count, names, modes, values, and
sources after failure. Rollback failure and disappearance have separate typed
outcomes.

## TouchDesigner 2025.32050 inventory

Two isolated networks, `/project1/tdcli_parameter_inventory` and
`/project1/tdcli_parameter_styles`, exercised every Parameter constructor in
the locked `Page` TDI. All 34 applicable `append*` methods succeeded. Their 29
distinct runtime styles were:

`CHOP`, `COMP`, `DAT`, `File`, `FileSave`, `Float`, `Folder`, `Header`, `Int`,
`MAT`, `Menu`, `Momentary`, `Object`, `OP`, `PanelCOMP`, `POP`, `Pulse`,
`Python`, `RGBA`, `Sequence`, `SOP`, `Str`, `StrMenu`, `Toggle`, `TOP`,
`TOPMulti`, `UVW`, `WH`, and `XYZW`.

The grouped constructors normalize as documented by runtime style: `appendRGB`
and `appendRGBA` both report `RGBA`; `appendUV` and `appendUVW` report `UVW`;
`appendXY`, `appendXYZ`, and `appendXYZW` report `XYZW`; `appendOBJ` and
`appendObject` report `Object`. The locked `parTypes.py` adds only `DATAdder` to
this set. There is no `Page` constructor and a full locked TDI Operator-stub
search found no `ParDATAdder` occurrence, so it remains an explicit `unknown`
and non-writable style rather than an invented value model.

`TOPMulti.evalOPs()` preserved ordered Operator identities; assigning an OP
object and an ordered list of TOPs succeeded. A Python Parameter accepted an
opaque object, confirming that it must not cross the wire. `Momentary` evaluated
as boolean, all OP-family styles evaluated as exact Operator identities, and
the numeric/vector constructors exposed individual numeric Pars.

Assigning `bindExpr` immediately produced `ParMode.BIND`, retained the exact
bind master, and evaluated through the master Parameter. Assigning
`ParMode.EXPORT` without a pre-existing channel export produced Export mode but
left both `exportSource` and `exportOP` null. The locked TDI exposes those
members as observations and exposes `Channel.exports` as a read-only list; it
does not expose a safe per-Parameter source setter. Consequently the public
Export mutation only reactivates and verifies an already-existing exact source.
It never fabricates an expression or mutates a CHOP's global export table.

A custom Sequence created with a Sequence header followed by two ParGroups and
`blockSize = 2` expanded to two blocks. Runtime names followed
`<sequence><index><local-name>`, while `SequenceBlock.par` resolved the stable
block-local names. Block names, indexes, block size, block count, and unlimited
`maxBlocks = None` were read back exactly.

## Automated gates

- Protocol rejects malformed source discriminators, mixed source fields,
  non-canonical paths, oversized expressions, Multi-OP lists, Sequences, and
  extra fields.
- Agent tests cover disabled writes, explicit opaque inspection, exact OP
  identity, ordered Sequence replacement, preflight-before-batch mutation, and
  the pre-existing v0.1.2 through typed-DAT regression suite.
- Transport restores nullable source, bounds, unsupported reason, Sequence,
  block-name, and Operator-null fields omitted by the locked Socket.IO DAT.

## Source-first Agent and live evidence

The Agent was rebuilt from the committed canonical source inside TouchDesigner
2025.32050, inspected independently, and then used for all public-command
checks.

- Canonical Agent source revision:
  `a53dff00c3bd51d93749d67e9347af0eadcbb38d21e46968be21afe10b460d83`
- Local derived artifact SHA-256:
  `992ec254b0a2acb828f83e6fe7048088c6fc70dede7243db60fc9c1fb5472abf`
- Online Instance Selector: `0e7e`
- Advertised capabilities: all 28 public Commands
- Required eight-Operator artifact topology: passed

Public live commands verified single OP and ordered Multi-OP constants, Int,
Unicode Str, opaque Python inspection without a value, Pulse, Bind source
identity, disabled rejection without mutation, and an exact two-block Sequence
containing a Unicode name, empty name, constant value, and expression source.
A clamped Sequence write failed with `parameter_sequence_write_failed`; the
subsequent complete read proved both blocks, names, modes, and values had been
restored. An Export request without an existing matching channel failed with
`parameter_export_source_unavailable` and preserved the prior Bind state.

## Automated and packaged gates

- `uv run pytest -q`: `313 passed` at the final source-first build point.
- `uv run ruff check .`: passed.
- `uv run mypy src`: passed with 16 source files checked.
- `git diff --check`: passed.
- PyInstaller 6.15.0 rebuilt all three one-file executables and both Agent
  distributions. Packaged `td.exe` reported v0.1.2 and performed live Unicode
  Parameter and Sequence reads against the Online Instance.

Local acceptance archive SHA-256 values (not immutable Release assets):

- `td-v0.1.2-windows-x86_64.zip`:
  `9b3ee365c3238936cbcea45ad28e39945375259dbfa0f2f46e2c5498c1a4bcc0`
- `td-daemon-v0.1.2-windows-x86_64.zip`:
  `88691b6ec4deecb09011bd516353064756cc8455a6b90e08de1ee8b77ac0f5e5`
- `td-agent-cli-v0.1.2-windows-x86_64.zip`:
  `2c20be5dfce6f8c1cf3823c9de64074a2cd63957fcb8ec6cdaa1264b7d36458d`
- `td-agent-component-v0.1.2-td2025.32050.zip`:
  `56cbc486d35727d4e57b41cd2a898388dfbc00d91527d9a38ba699580537822f`
