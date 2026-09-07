# Read derived table DATs

Artwork acceptance request `01a07c61-694f-7415-8ff8-4951548b7e91`
failed with `dat_type_mismatch` for a CHOP to DAT containing real output values.
The existing parameter export path only reads already-established export sources
and cannot establish this acceptance graph (`parameter_export_source_unavailable`,
request `01a07c63-560f-7d62-af92-bb334e7faae6`).

Approved narrow change: `dat.table.get` accepts any DAT whose official
[`isTable` property](https://docs.derivative.ca/DAT_Class) is true. Existing
row/column/cell/byte limits and result schema remain unchanged. Table writes still
require `tableDAT`; no force-cook operation, expression evaluation, or general
CHOP API is introduced. Ordinary TD property reads use its normal dependency cook.
Read acceptance must prove a derived CHOP to DAT contains actual output values.

Public test seam: derived table bounded read succeeds; text/non-DAT read and
derived-DAT mutation fail. This follows the existing DAT model, not an alternate
transport or execution path.
