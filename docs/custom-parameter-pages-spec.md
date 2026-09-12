# Typed custom parameter pages

Collective Dream Factory requires an editable native control surface containing a
manual/device selector, three scalar gyro controls, engagement and signal health.
Version 0.5.0 can inspect custom parameters but cannot create them. Operator layout
is already supported; artwork runtime callbacks own effect cooking and unloading.

## Approved internal design

Add `td parameters page-create --input-file FILE`, Command
`parameters.page.create`. The input identifies one mutable COMP and a **new**
page name, with 1–32 ordered scalar definitions: `float`, `toggle`, or `menu`.
Float definitions include finite minimum/maximum/default, hard clamping and the
same slider range. Toggle defaults are boolean. Menus contain 1–32 unique stable
names and corresponding labels, and a default name. Parameter names use a capital
ASCII letter followed by lowercase ASCII letters/digits (maximum 32 characters).
Existing pages or parameter names are rejected before mutation; no overwrite.
The ASCII-escaped input JSON plus one serialized target path per parameter must
fit 16,384 bytes, leaving capacity for the returned descriptor/value metadata.

Return the created parameters through the existing parameter descriptor/value
contract. Existing `parameters list/get/set` remain the inspection/editing seam.
Creation verifies the exact page, names, style, labels, values and range/menu
metadata. On failure destroy only the newly created page. Report rollback failure
or vanished-target unknown honestly; never retry a mutation automatically.

Public tests use Command catalog validation and Agent OperatorControl with the
external TD API substituted. Locked TD 2025.32050 must prove native page creation,
menu/default/range readback, duplicate rejection without graph damage, save and
reload persistence. Complete local gate and independent review are required.

Non-goals: arbitrary Python execution, custom expressions/callback generation,
page replacement/deletion, vector groups, CLI runtime scheduler, and new cooking
or unloading commands. Existing Request FIFO, persistence, connection-generation
and main-thread execution contracts are unchanged.

Primary API references: [custom parameters](https://derivative.ca/UserGuide/Custom_Parameters),
[Page](https://derivative.ca/UserGuide/Page_Class),
[COMP](https://derivative.ca/UserGuide/COMP_Class). Research accessed 2026-09-12;
newer online methods are not assumed supported without locked runtime evidence.
