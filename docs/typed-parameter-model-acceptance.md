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

An isolated `/project1/tdcli_parameter_inventory` network confirmed the custom
styles Float, Int, Toggle, Pulse, Str, Menu, StrMenu, OP, TOPMulti, Python, and
Sequence. `TOPMulti.evalOPs()` preserved ordered Operator identities; assigning
an OP object and an ordered list of TOPs succeeded. A Python Parameter accepted
an opaque object, confirming that it must not cross the wire.

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

Source-first Agent artifact identity, packaged CLI smoke, and final Online
Instance command evidence are recorded after the canonical source commit.
