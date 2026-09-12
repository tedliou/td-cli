# Typed display/render flags

Collective Dream Factory creates Script SOP geometry and particle sprites, then
moves them into Geometry COMPs. Fresh nodes have display/render flags disabled;
`ops.inspect` can read these flags, but 0.5.0 cannot write them. Formal evidence:
Requests 01a0961c-f3cd-7674-abd7-b4690c62c1fa and
01a0961c-fde4-770c-9cf5-f26ff73fbf4b in the artwork Instance ee41.

Approved design: extend existing typed `ops.state.set` with boolean `display`
and `render`, CLI `--display/--no-display`, `--render/--no-render`, and return
them through `ops.state.get`. Use the same verified state patch and rollback
contract. No Python execution or callback-based authoring workaround.
The Python OP attributes already underpin `ops.inspect` for every inspected
family. Locked TD 2025.32050 acceptance must verify SOP flags and render output,
readback, sibling flag preservation, and representative TOP/CHOP/COMP behavior.
If a family rejects/clamps a requested flag, the existing patch rejection and
rollback behavior applies; a returned flag is not proof a family renders geometry.

Public seams: strict Command/CLI state patch; OperatorControl rollback tests;
locked existing TD artifact through public commands. Non-goals: new flags beyond
display/render, implicit SOP selection, sibling selection changes, arbitrary
attributes, lifecycle scheduling or cooking/unloading commands.

References: [Render Flag](https://derivative.ca/UserGuide/Render_Flag),
[Display Flag](https://derivative.ca/UserGuide/Display_Flag),
[OP Class](https://derivative.ca/UserGuide/OP_Class). Accessed 2026-09-12.
