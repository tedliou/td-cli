# TouchDesigner 2025.32050 family-specific Operator inspection

## Scope and source policy

This note defines a conservative, typed, read-only inspection surface for CHOP,
DAT, TOP, SOP, POP, and MAT operators. It is based only on Derivative's bundled
TDI snapshot from the locally locked TouchDesigner 2025.32050 installation and
Derivative's official class-reference URLs. TDI annotations describe the public
Python shape, but they are declarations rather than proof that a property is
cheap or that a live operator can never throw while cooking. Runtime validation
must therefore remain an acceptance gate.

Primary sources:

- Common OP API: `C:\Program Files\Derivative\TouchDesigner\bin\Lib\tdi\ops\OP.py`
- Passive lookup helper: `C:\Program Files\Derivative\TouchDesigner\bin\Lib\tdi\td.py` lines 212-215; [official Cook reference](https://docs.derivative.ca/Cook)
- CHOP: `...\tdi\ops\chops\CHOP.py` lines 11-48; [official CHOP Class](https://docs.derivative.ca/CHOP_Class)
- DAT: `...\tdi\ops\dats\DAT.py` lines 11-64; [official DAT Class](https://docs.derivative.ca/DAT_Class)
- TOP: `...\tdi\ops\tops\TOP.py` lines 11-56 and 68-94; [official TOP Class](https://docs.derivative.ca/TOP_Class)
- SOP: `...\tdi\ops\sops\SOP.py` lines 11-80; [official SOP Class](https://docs.derivative.ca/SOP_Class)
- POP: `...\tdi\ops\pops\POP.py` lines 11-117; [official POP Class](https://docs.derivative.ca/POP_Class)
- MAT: `...\tdi\ops\mats\MAT.py` lines 1-15; [official MAT Class](https://docs.derivative.ca/MAT_Class)
- SOP attribute metadata: `...\tdi\tdClasses\Attribute.py` lines 11-58 and `Attributes.py` lines 11-23; [official Attribute Class](https://docs.derivative.ca/Attribute_Class)

The official site may return HTTP 403 to automated fetchers. The URLs above are
still the owning first-party references; exact declarations quoted below come
from the installed, build-locked TDI files.

## Recommended contract

Expose one discriminated result with `family` equal to `chop`, `dat`, `top`,
`sop`, `pop`, or `mat`, plus a family-specific `details` object. Inspect one
canonical OP path per request. Never return TouchDesigner proxy objects: convert
positions to finite three-number arrays, enum-like values to strings, and
attribute/group/channel names to strings. A missing/destroyed/wrong-family OP is
a typed failure, not a nullable successful result.

After canonical resolution and family validation, inspect through `passive(op)`.
The bundled helper says passive OPs do not cook before member access (`td.py`
lines 212-215), while `OP.passive` documents the same behavior (`OP.py` lines
428-431). This intentionally reports the latest cached/last-cooked state rather
than forcing freshness. The response should say it is passive/cached; inspection
must never call `OP.cook()`.

Use these request-wide limits:

- `max_items`: integer 1..1000, default 100, applied independently to every
  variable-length name collection. Return `total` and `items` only when the
  complete collection fits; otherwise fail with the existing
  `result_too_large` typed error. Do not silently truncate a requested snapshot.
- `max_text_bytes`: integer 1..1,048,576 only if a later explicit DAT content
  preview is added. The initial inspector should not return DAT content.
- Reject non-finite floats during serialization. Catch each family property read
  and return a typed `family_inspection_failed`; do not silently omit a field.
- Do not call `cook()`, `numpyArray()`, `sample()`, `vals()`, `points()`,
  `prims()`, or `verts()` in the default inspector.

The limits are td-cli safety-policy choices, not TouchDesigner limits. They keep
response size and Agent execution bounded while preserving exact totals where a
cheap scalar count exists.

## Stable typed fields by family

### CHOP

Recommended scalar fields (all non-null after a successful family check):

| Field | JSON type | TDI declaration | Notes |
|---|---:|---|---|
| `num_channels` | integer | `numChans: int` | Number of channels. |
| `num_samples` | integer | `numSamples: int` | Samples per channel. |
| `sample_rate` | number | `rate: float` | Reject non-finite values. |
| `start` / `end` | number | `start: float`, `end: float` | Channel index domain, not wall-clock time. |
| `is_time_slice` | boolean | `isTimeSlice: bool` | Describes the last cook. |
| `export` | boolean | `export: bool` | Family flag; duplication with generic flags is acceptable only if the schema intentionally groups family state. |
| `export_changes` | integer | `exportChanges: int` | Monotonic-ish runtime observation, not a stable identity/version. |

An optional bounded `channel_names` collection may be built from `chans()` and
each `Channel.name`; TDI declares `chans()` as a possibly empty list and
`Channel.name: str` (`CHOP.py` lines 64-80; `tdClasses\Channel.py` lines 11-28).
It is variable length and must use `max_items`. Do not read channel samples in
this command: iteration and `numpyArray()` scale as `num_channels × num_samples`
and can force evaluation/data transfer.

Risk: these metadata properties describe cooked output and may cause an
otherwise out-of-date CHOP to evaluate in live TouchDesigner. They are bounded
scalar reads, but not guaranteed zero-cook reads. A cook error must become the
typed failure.

### DAT

Recommended fields (non-null after family check):

| Field | JSON type | TDI declaration | Notes |
|---|---:|---|---|
| `is_table` / `is_text` | boolean | `isTable: bool`, `isText: bool` | Expected to be complementary, but report both rather than infer. |
| `is_editable` | boolean | `isEditable: bool` | Capability observation, not authorization to mutate. |
| `num_rows` / `num_cols` | integer | `numRows: int`, `numCols: int` | Meaningful for table-formatted DATs; retain integers for text DATs exactly as TD reports them. |
| `editing_file` | string | `editingFile: str` | Empty string is a value, not `null`; may expose a local path, so include only because the caller explicitly requests family inspection. |
| `export` | boolean | `export: bool` | DAT Export flag. |

Do not include `text`, `csv`, `jsonObject`, `locals`, or `module` by default.
`text`/`csv` are unbounded; `jsonObject` parses attacker/project-controlled text
and may throw; `module` executes/loads Python semantics; `locals` exposes live
arbitrary Python objects. Existing explicit typed DAT operations are the proper
surface for content. If preview is later added, operate on UTF-8 bytes with
`max_text_bytes`, report truncation, and never use `jsonObject` or `module`.

Risk: output DAT dimensions/content can require a cook. Scalar shape reads are
bounded but still need exception normalization.

### TOP

Recommended non-null scalar fields:

| Field | JSON type | TDI declaration | Notes |
|---|---:|---|---|
| `width` / `height` / `depth` | integer | corresponding `int` properties | Texture dimensions. Validate non-negative results. |
| `aspect` / `aspect_width` / `aspect_height` | number | corresponding `float` properties | Reject non-finite values. |
| `pixel_format_name` | string | `pixelFormatName: str` | Canonical menu name for Python handling. |
| `pixel_format_display` | string | `pixelFormat: str` | Display-only label; do not use as an identifier. |
| `gpu_memory_bytes` | integer | common `OP.gpuMemory: int` | Family-relevant cached GPU allocation; declaration is in `OP.py` lines 199-202. |
| `current_pass` | integer | `curPass: int` | Runtime cook-pass observation. |
| `newest_slice_w_offset` | integer | `newestSliceWOffset: int` | Relevant to 3D texture filling; preserve TD's value for other TOPs. |

Never call `sample()`: TDI explicitly says it is very expensive, stalls the
graphics pipeline, and downloads the entire texture (`TOP.py` lines 88-111).
Likewise exclude `numpyArray()`, CUDA memory/array access, and byte/image saves.
Dimension and format properties may still cook/allocate upstream GPU resources;
catch TD exceptions and apply an Agent deadline.

### SOP

Recommended scalar/tuple fields, all non-null on success:

| Field | JSON type | TDI declaration | Notes |
|---|---:|---|---|
| `num_points` / `num_primitives` / `num_vertices` | integer | `numPoints`, `numPrims`, `numVertices: int` | Exact cooked geometry counts. |
| `center` / `min` / `max` / `size` | array of 3 numbers | `tdu.Position` | Copy values out; never serialize the Position proxy. |
| `compare` / `template` | boolean | corresponding flag properties | Family flags. |

Optional `point_attributes`, `primitive_attributes`, and `vertex_attributes`
may list bounded metadata objects: `name: string`, `size: integer`, `is_array:
boolean`, `array_size: integer`, `matrix_rows: integer`, `matrix_cols: integer`,
and a normalized type-name string. These are declared by `Attribute.py` lines
15-53. Do not serialize `owner`, `default`, attribute values, `points`, or
`prims`. Optional point/primitive group **names** may be copied from the declared
dicts, sorted for deterministic output, and bounded with `max_items`; never
serialize Group or geometry proxy values.

Risk: counts, bounds, attributes, and groups describe cooked geometry and may
trigger a SOP cook or propagate a cook exception. Attribute/group collections
are variable length. Avoid `bounds()` as redundant when the scalar Position
properties suffice, and avoid all element traversal.

### POP

POP is actually supported in the locked build. TDI defines `class POP(OP)` in
`ops\pops\POP.py`, and the installed `ops\pops` directory contains 98 class
definition files including the base (97 derived operator definitions plus `POP.py`,
and 99 Python files when `__init__.py` is included). Therefore the
runtime family class is `td.POP` / Python class name `POP`; it must not be folded
into SOP or treated as unknown. Runtime acceptance should verify `isinstance(op,
td.POP)` against representative POPs.

Safe default fields:

| Field | JSON type | TDI declaration | Nullability/risk |
|---|---:|---|---|
| `dimension` | normalized string or integer | `dimension: Any` | TDI does not provide a stable type. Normalize only observed scalar/enum values; otherwise fail validation rather than stringify arbitrary objects. |
| `max_vertices_per_line_strip` | integer | `maxVertsPerLineStrip: int` | Non-null scalar. |
| `compare` / `template` | boolean | corresponding flags | Non-null scalar. |
| `allocated_points` | integer | `numPoints(max=True)` | TDI says `max=True` is always instant and ignores `delayed`. |
| `allocated_primitives` | integer | `numPrims(max=True)` | Same. |
| `allocated_vertices` | integer | `numVerts(max=True)` | Same. |

Do **not** include actual point/primitive/vertex counts or bounds by default.
TDI says `numPoints`, `numPrims`, `numVerts`, and `bounds` can delay GPU
downloads; without `max=True`, synchronous calls may stall. With `delayed=True`,
the first/result-not-ready call can effectively be `None` despite the `-> int`
or `-> Bounds` annotation, so any future opt-in actual-count schema must use
`integer | null` plus `pending: boolean` and must never substitute allocated
capacity for actual count.

The POP attribute properties are annotated `Any`, and `pointAttributesChanged`,
`primAttributesChanged`, and `vertAttributesChanged` are runtime change state.
Do not expose them until live 2025.32050 tests establish their concrete shape.
Never call `points()`, `prims()`, `verts()`, or `Attribute.vals()` in inspection:
they download variable-sized GPU data and can return delayed/not-ready results.

### MAT

The locked `MAT.py` declares no MAT-specific properties or methods beyond
`class MAT(OP)` and its family description. The stable family-specific payload
is therefore an empty object (or only a `family: "mat"` discriminator), with
generic OP identity, state, flags, parameters, and connections supplied by the
existing generic commands. Do not manufacture shader/uniform/texture fields by
probing operator-specific parameters: their shape varies by MAT type and is
already representable through the Parameter model.

Reading ordinary generic OP metadata may still observe cook/error state, but the
MAT family inspector itself needs no cook-dependent call. Operator-specific
shader compilation diagnostics belong to a future explicitly scoped command.

## Failure, nullability, and compatibility rules

1. Resolve the canonical path once, verify the expected family, then snapshot
   fields. A destroyed/replaced OP during the snapshot produces
   `family_inspection_outcome_unknown` (or the project's equivalent read-race
   error), never a partially successful object.
2. Declared scalar properties are non-null in successful results. Null is
   reserved for documented asynchronous POP results; absence is not encoded as
   an empty string, zero, or empty list.
3. An unavailable property, TD exception, invalid proxy type, non-finite number,
   or collection mutation during iteration is a typed failure. This makes build
   drift visible instead of silently weakening the contract.
4. Sort name-only maps (SOP groups) for deterministic transport; preserve native
   channel/attribute order where order has operator meaning. Reject collections
   whose exact `total` exceeds `max_items`.
5. Keep the initial command read-only and non-batchable unless the existing
   batch deadline can bound cumulative cook cost. One nominally scalar request
   can still provoke a family cook.

## Acceptance matrix for TouchDesigner 2025.32050

Use only isolated `tdcli_` prefixed operators and do not save the project.

- CHOP: constant and time-sliced examples; zero/many channels; an upstream cook
  error; verify over-limit rejection.
- DAT: Table and Text DAT, empty and large shapes, non-JSON text, and a DAT with
  an external editing path; verify no content/module parsing occurs.
- TOP: 2D and 3D textures, an erroring shader/input, and a large texture; verify
  no pixel download and exact format-name distinction.
- SOP: empty and populated geometry, attributes/groups beyond `max_items`, and
  an upstream cook error; verify Positions become finite length-three arrays.
- POP: representative generator and downstream POP; prove `td.POP`, validate
  allocation counts are immediate, inspect observed `dimension`, and separately
  demonstrate why synchronous actual count/bounds are excluded.
- MAT: at least Constant/Phong/GLSL-style MAT variants; prove the family result
  remains type-stable and does not depend on operator-specific parameter pages.
- For every family: missing path, wrong family, operator destroyed during read,
  response-size bound, serialization, daemon transport, CLI output, and no
  mutation/cook call issued by td-cli code.

## Decision

Implement metadata-first family inspection. CHOP/DAT/TOP/SOP receive the stable
scalar fields above; SOP additionally permits bounded attribute/group metadata;
POP exposes only scalar flags/dimension and instant allocated-capacity counts;
MAT intentionally has no invented family payload. Raw samples, cells/text,
pixels, geometry elements, GPU downloads, Python-module objects, and
operator-specific MAT parameter projections remain outside this command.
