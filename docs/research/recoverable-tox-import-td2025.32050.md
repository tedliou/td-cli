# TouchDesigner 2025.32050 recoverable bounded TOX import

## Scope and evidence policy

This note answers GitHub Issue #74 for the locked Windows runtime. It uses only
Derivative's official documentation, Derivative's bundled TDI declarations from
`C:\Program Files\Derivative\TouchDesigner\bin\Lib\tdi`, and disposable live
probes in TouchDesigner 2025.32050. All live operators used a `tdcli_` prefix in
the unsaved repository `Sample.toe`; the project was not saved.

Primary sources:

- `COMP.loadTox`, `loadByteArray`, `reload`, `save`, and `saveByteArray`:
  `...\tdi\ops\comps\COMP.py` lines 276-293, 328-334, and 358-379;
  [official COMP Class](https://docs.derivative.ca/COMP_Class).
- `COMP.copy` and `create` collision/initialization semantics:
  `...\tdi\ops\comps\COMP.py` lines 166-201.
- OP identity, hierarchy, type, counts, validity, and destruction:
  `...\tdi\ops\OP.py` lines 39-41, 207-217, 239-241, 279-281, 307-313,
  432-496, 502-556, and 667-670;
  [official OP Class](https://docs.derivative.ca/OP_Class).
- Execute DAT creation semantics:
  `...\tdi\ops\dats\executeDAT.py` lines 19-51 and 161-193;
  [official Execute DAT](https://docs.derivative.ca/Execute_DAT).
- External TOX parameters and relative-path behavior:
  Derivative's installed `Config\TDParameterHelp.json` entries for
  `enableexternaltox`, `externaltox`, `savebackup`, `subcompname`, and `relpath`;
  [official Select COMP parameter reference](https://docs.derivative.ca/Select_COMP).
- TOX purpose and VFS payloads:
  [official Component guide](https://docs.derivative.ca/Component) and
  [Virtual File System](https://docs.derivative.ca/Virtual_File_System).

TDI is a declaration, not an atomicity or security guarantee. Where the runtime
contradicts or extends TDI, the result below is explicitly labelled **locked
live observation**. Numeric bounds are td-cli safety decisions, not Derivative
limits.

## Locked API inventory and live corrections

### Load and save primitives

`COMP.loadTox(filepath, unwired=False, pattern=None, password=None) -> OP` loads
from disk *inside the receiver COMP*. `pattern` can select operators within the
component and explicitly does not support wildcards. `loadByteArray` has the
same shape and consumes bytes created by `saveByteArray`. `COMP.save(filepath,
createFolders=False, password=None) -> str` writes a COMP to disk, while
`saveByteArray() -> bytearray` produces the same bytes held in a TOX file (TDI
`COMP.py` lines 276-293 and 358-379).

Locked live observations:

- `loadTox(valid_tox)` returned the one imported root OP. A saved Base COMP
  `source_root` returned `baseCOMP('/.../source_root')` with its two children.
- Loading it again beside the first did **not** reject collision: the returned
  roots were `source_root1`, then `source_root2` for `loadByteArray`. This agrees
  with the documented general `COMP.copy`/`create` suffixing convention
  (`COMP.py` lines 166-201), but is not stated on `loadTox` itself.
- `loadTox(missing_path)` and `loadByteArray(nonempty_corrupt_bytes)` returned
  `None` without raising or adding a child. `loadByteArray(bytearray())` instead
  raised `tdError: Input bytearray object has 0 length.` Therefore neither
  “returned normally” nor “no exception” proves success; a non-null returned OP
  plus post-load identity verification is mandatory.
- TouchDesigner accepted a valid TOX copied to a `.txt` filename. Extension and
  file-kind policy must be enforced before calling TD.
- A 318-byte saved TOX round-tripped through `saveByteArray`/`loadByteArray`.
  The primitive restores exactly one root, not an arbitrary list.
- `pattern='alpha'` returned the selected top-level `textDAT` directly;
  a missing pattern returned `None`. Thus public import must always use
  `pattern=None`; otherwise it does not guarantee a single COMP root and makes
  preflight identity ambiguous.

### `reload` is not the rollback primitive

TDI says `COMP.reload(filepath)` replaces children, top-level parameters,
flags, size, storage, comments, and inputs while preserving the original node
x/y (`COMP.py` lines 328-334). Locked live behavior matched this: it returned
`None`, kept the same OP id/path and coordinates `(77,-33)`, replaced the old
child with the TOX child, and removed prior storage. A missing file raised
`tdError: Error reloading component.` and left the observed children unchanged.

Despite that negative case, the API has no transaction or documented rollback
guarantee for all parse/initialization failures. It mutates the existing target
in place and can execute imported callbacks. Do not use it for public import or
as the only restoration mechanism.

### Names, return shape, and multi-root ambiguity

`OP.name` is mutable and `OP.path`, `OP.id`, `OP.OPType`, `OP.family`,
`OP.isCOMP`, `OP.valid`, `children`, `numChildren`, and
`numChildrenRecursive` provide the verification primitives (TDI `OP.py` lines
cited above). Locked live assignment of a duplicate name raised
`tdError: Invalid or duplicate operator name`; slash, space, non-ASCII, and
empty test names were also rejected. Existing td-cli's stricter exact-name
grammar (`[A-Za-z_][A-Za-z0-9_]{0,63}`) is therefore a conservative stable
public grammar.

A normal component TOX produced one returned root. However `pattern` can return
a non-COMP, and no official declaration guarantees that every file is a
well-formed single-root public payload. Treat a null return, non-COMP root,
unexpected parent/path/name/type, or any extra child added to the staging
parent as load/verification failure. Do not infer the imported set only from
the return value: diff the staging parent's child ids before/after, require
exactly one new id, and require that id to equal the returned root.

## External TOX linkage and path behavior

Derivative documents `externaltox` as the disk path whose content may source a
COMP at project start; `enableexternaltox` controls loading, `savebackup` embeds
a fallback in the project, `subcompname` selects a nested COMP, and `relpath`
can make child file paths relative to the `.toe`, external `.tox`, or parent
behavior (installed parameter help and the official Select COMP page).

Locked live observations sharpen the boundary:

- Saving a root whose `externaltox` was `sentinel-relative.tox`, then loading
  it, yielded an empty `externaltox` on the imported root.
- A *nested* COMP's `externaltox='nested-external.tox'` survived loading, even
  with `enableexternaltox=False`.
- `saveByteArray` restoration likewise cleared the backed-up root's
  `externaltox`, but retained its `enableexternaltox`, `reloadcustom`,
  `reloadbuiltin`, `savebackup`, `subcompname`, and `relpath` values.

Consequently, clearing only the imported root is insufficient. Snapshot mode
must recursively inventory every COMP and require all external linkage fields
to be inert: set `enableexternaltox=False`, clear `externaltox` and
`subcompname`, set `savebackup=False`, and choose project-relative/inherited
path behavior according to the final contract; then read all values back.
Because changing these values after load cannot undo callbacks already run,
files containing external linkage should conservatively be rejected after
staging rather than silently sanitized into a claimed trusted snapshot.

VFS is also part of a TOX: Derivative documents that a COMP may embed arbitrary
files and address them through `vfs:`. A bounded graph inventory does not bound
embedded VFS bytes. The preflight disk-size limit bounds the container, but the
initial public contract should reject any imported root with nonempty VFS until
a separately bounded VFS inventory is implemented.

## TOX is executable project content, not passive data

The Execute DAT parameter help says its `create()` method can run when the node
is created “by loading a component from disk, by copying & pasting, or any other
way” (`executeDAT.py` lines 19-51, 161-193). Locked live proof was stronger: a
saved TOX containing an active Execute DAT incremented a value outside the
loaded subtree, and the value was already `1` when `loadTox` returned.

Other callbacks, extensions, initialization scripts, file-writing DATs,
network/device operators, Window COMPs, and project-relative/external asset
parameters can also cause work during or after creation. Derivative documents
that `COMP.create(..., initialize=True)` normally runs an operator's
initialization script (`COMP.py` lines 193-201); `loadTox` exposes no
`initialize=False` switch. Therefore:

1. there is no general safe static preflight API for arbitrary TOX;
2. loading into an “isolated” namespace limits graph placement, not Python,
   filesystem, network, UI, GPU, or process side effects;
3. no post-load rollback can undo arbitrary external side effects; and
4. “trusted local TOX” must be an explicit caller assertion and deployment
   boundary, never language suggesting sandboxing.

The public command must not accept Python, passwords, `pattern`, generic
methods, relative paths, linked mode, or arbitrary post-load callbacks from the
request. Documentation should state that a trusted TOX may execute project
code despite every structural safeguard.

## Conservative public contract

Expose one non-batchable mutation:

```text
ops.tox.import
```

Recommended input:

```json
{
  "parent_path": "/project1/target_parent",
  "tox_path": "C:\\approved\\asset.tox",
  "allowlist_root": "C:\\approved",
  "target_name": "asset",
  "replace": false,
  "trusted": true,
  "max_file_bytes": 67108864,
  "max_operators": 1000
}
```

Contract rules:

- All strings retain the repository-wide 4096 UTF-8-byte cap. `parent_path`
  is an exact canonical Operator path; `target_name` uses the safe exact-name
  grammar. Require `trusted: true` so the execution boundary cannot be missed.
- `tox_path` and `allowlist_root` must be existing absolute local Windows paths.
  Canonically resolve both without following an unexamined link; reject UNC,
  device, namespace, alternate-data-stream, directory, symlink/junction/reparse,
  non-regular, non-`.tox`, changed-during-read, and outside-root inputs. Compare
  final canonical paths case-insensitively by path components, not string
  prefix. Open the verified file once, bound bytes (recommended 1..64 MiB),
  hash SHA-256, and reject if metadata changes before load. These are td-cli
  filesystem safety policies; `loadTox` itself does not provide them.
- Resolve the parent again immediately before mutation; require a valid COMP,
  and protect `/`, the Agent Component, all of its ancestors/descendants, and
  any destination/temporary path outside the exact approved parent namespace.
- Default `replace=false`; if `<parent>/<target_name>` exists, fail before
  loading. Never rely on TD's automatic numeric suffix.
- Use a uniquely named, direct child staging COMP under the exact parent (for
  example `tdcli_tox_stage_<request-id>`), record the complete before-child id
  set, read the verified file once into a bounded bytearray, and call
  `stage.loadByteArray(verified_bytes, unwired=True, pattern=None)`. This binds
  the executed content to the reported SHA-256 and avoids reopening a mutable
  path after verification.
  The stage namespace must initially contain no other child.
- Require exactly one new child id and the returned object to be that id, a
  valid COMP, and direct child of stage. Require source root name to be safe,
  then assign `target_name` only after destination collision recheck.
- Inventory the whole subtree iteratively before exposure: root plus recursive
  descendants, each as `{relative_path, op_type, family}` sorted by relative
  path. Fail rather than truncate above `max_operators` (1..1000). Validate
  every path is beneath the returned root, every OP is valid, every `op_type`
  is in the locked catalog and supported for the locked OS, every name is safe,
  no extra stage children appeared, and the identity is unchanged after read.
  Explicitly reject nonempty VFS, external linkage anywhere, private/encrypted
  content that prevents full inventory, and unsupported/conditional OP types.
- A successful result returns exact destination/root identity, exact count,
  complete bounded inventory, canonical source path, file byte size, SHA-256,
  `replaced`, and `rollback_performed=false`. Do not return temporary paths or
  raw TD proxies.

### Replacement transaction

For `replace=true`, perform all input/file checks and load/verify the new root
in staging *before touching the old target*. Then:

1. re-resolve the old target and verify its id is unchanged;
2. ensure its subtree fits `max_operators` and is fully inspectable;
3. create an in-memory `old.saveByteArray()` backup and independently restore
   it with `stage.loadByteArray(..., unwired=True, pattern=None)` under a second
   isolated temporary name;
4. fully verify the restored backup against a deterministic structural
   manifest of the original (relative path, type/family, count, and critical
   linkage state), then destroy the verification copy;
5. retain the original backup bytes and manifest; only now destroy old target,
   recheck exact destination vacancy, move/copy the verified new root to the
   exact destination, and verify it again;
6. on any post-destroy failure, destroy every partial object at the exact
   destination/staging ids, restore with the verified bytes, force the exact
   name, verify the old manifest again, and only then report rolled back.

The live round-trip proves `saveByteArray`/`loadByteArray` is a feasible
single-root restoration primitive, but not a transaction. It clears the root
`externaltox`, and restoration can itself execute callbacks. Backups therefore
must be verified *before* old-target destruction and retained until final
verification. If rollback cannot be proven exact, return an uncertain outcome
with all observed ids/paths internally logged; never claim atomic success.

Moving the new root out of a temporary parent is preferable to reloading the
old OP in place because it allows complete verification before target mutation.
If TouchDesigner cannot preserve the verified identity through the repository's
existing safe move primitive, use a second byte-array round trip and verify it;
do not weaken the gate.

## Typed error taxonomy

Keep protocol validation errors for malformed/range-invalid input. Runtime
typed errors should distinguish:

| Code | Meaning |
|---|---|
| `tox_trust_required` | Caller did not explicitly assert trusted executable content. |
| `tox_path_rejected` | Path is non-absolute, non-local, outside allowlist, wrong extension, directory/link/reparse/non-regular, changed during read, or otherwise unsafe. |
| `tox_file_too_large` | Exact bytes exceed `max_file_bytes`; no truncation. |
| `tox_destination_exists` | Collision while replacement is disabled or destination changed before commit. |
| `tox_parent_protected` | Parent/destination/temp namespace intersects a protected path. |
| `tox_load_failed` | TD raised, returned null, or did not create exactly one returned root. |
| `tox_verification_failed` | Root/subtree/type/name/VFS/linkage/count/identity/side-effect structural checks failed before commit. |
| `tox_backup_failed` | Existing target could not be saved, independently restored, or matched before mutation. |
| `tox_commit_failed` | Verified staged import could not be installed at the exact destination before old-target loss. |
| `tox_rollback_failed` | Old target restoration or verification failed after mutation. |
| `tox_import_outcome_unknown` | An OP disappeared/was replaced, TD timed out/disconnected, or final identity cannot prove success or rollback. |

All failures before old-target destruction must clean only ids created by the
request. Cleanup failure upgrades to `tox_import_outcome_unknown`. A successful
rollback returns failure (`tox_commit_failed` with rollback metadata), never a
successful import. `tox_rollback_failed` and `tox_import_outcome_unknown` must
include a concise recovery-oriented message but not disclose arbitrary file
content or byte arrays.

## Acceptance matrix for TouchDesigner 2025.32050

Use a disposable unsaved project and only `tdcli_` artifacts.

| Area | Required live cases and proof |
|---|---|
| File preflight | exact allowed absolute `.tox`; missing; directory; wrong extension containing valid TOX; outside root; symlink/junction/reparse; UNC/device/ADS; size at/over limit; metadata change race. Prove no TD mutation on rejection. |
| Load shape | normal one-root COMP; missing/corrupt `None`; empty bytes exception; collision suffix observed but rejected by command; `pattern` remains inaccessible; non-COMP/ambiguous stage output rejected. |
| Naming | exact safe name; collision default rejection; collision arising between preflight/commit; invalid names; verify exact path and no numeric suffix. |
| Inventory | nested mixed-family graph; zero/one/`max_operators`/over-limit descendants; unsupported/conditional type; invalidation during traversal; stable sorted complete result and no truncation. |
| Linkage/content | root and nested `externaltox`; enabled/disabled combinations; `subcompname`, `savebackup`, `relpath`; nonempty VFS; private/encrypted TOX; prove initial contract rejects linked/VFS/uninspectable content. |
| Executable risk | active Execute DAT create callback (already observed running before return); frame callback; extension/init behavior where safe to probe. Verify docs call content trusted/executable and cleanup does not claim to undo external effects. |
| Fresh import | stage, verify, exact install, stage removal, result hash/count/inventory, existing commands can inspect destination. |
| Replacement | independently verified byte backup; successful replace; injected failure before old destroy; after old destroy; during new install; after install verification; successful rollback restores exact manifest/name; rollback failure yields typed uncertain result. |
| Concurrency | parent/target/source file disappears or changes at every boundary; stage gets an extra child; returned OP invalidates; daemon deadline/disconnect. Require relookup/id checks and conservative uncertain outcomes. |
| Protection | `/`, Agent, Agent ancestors/descendants, sibling/outside paths, and forged temp names are rejected; cleanup cannot destroy pre-existing operators. |
| Product layers | strict Protocol model and bounds; non-batchable catalog; CLI arguments/output; Agent normalization; daemon serialization; typed client errors; capability advertisement; source-first locked Agent artifact; packaged Windows CLI smoke. |
| Regression | full automated suite and all v0.1.2+ acceptance gates remain green; project is never saved. |

## Decision

Implement `ops.tox.import` only as a **trusted executable-content transaction**,
not as a TOX sandbox. Use `loadTox(..., unwired=True, pattern=None)` solely in a
bounded staging namespace; verify exactly one returned COMP and the complete
bounded subtree. Snapshot mode rejects every nested external link and VFS.
Replacement remains opt-in and is permitted only after an in-memory backup has
been independently restored and matched. Any ambiguous mutation, cleanup, or
rollback becomes a typed uncertain outcome.

The crucial limitation is irreducible: an active Execute DAT demonstrably runs
while loading, before post-load verification is possible. An opaque TOX cannot
be fully preflighted for callbacks, extension initialization, parameter
expressions, embedded VFS programs, or operator-specific initialization without
first loading it; that load is already execution. The code can mutate operators
outside the staging subtree and external process/filesystem/network/device
state. Destroying the staged root, reloading old bytes, or restoring a target
backup cannot undo those effects. Consequently raw TOX import and global
atomic rollback cannot truthfully satisfy a requirement that public input never
execute arbitrary Python.

There are exactly two honest product choices:

1. **Trusted executable TOX:** expose the contract above with explicit
   `trusted: true`, clearly promise only destination-graph recovery (not global
   atomicity), and state that trusted content can run code before return. This
   is the closest match to Issue #74's currently authorized raw local-TOX
   direction, but the Issue wording must not claim arbitrary-code prevention or
   universal rollback.
2. **Verified safe envelope:** do not accept an arbitrary `.tox`. Accept only a
   td-cli-produced, versioned manifest/envelope bound to exact TOX SHA-256 and a
   trusted signing key. Production occurs in a controlled TouchDesigner
   process, inventories every supported OP/type/parameter/expression/callback,
   rejects executable DATs, extensions, expressions, external linkage, VFS,
   conditional/unknown types, and records the locked TD build and limits. The
   importer verifies signature, hash, build, expiry/policy version, and manifest
   before loading, then repeats runtime structural verification. This reduces
   accepted content to what td-cli previously certified; it still relies on
   the producer and the completeness of its locked-build policy, so it should
   promise bounded accepted behavior rather than a general sandbox.

Absent one of these two explicit trust models, the safe implementation is to
reject the command before `loadTox`. The caller's trust assertion or signed
envelope is therefore part of correctness, not merely documentation.
