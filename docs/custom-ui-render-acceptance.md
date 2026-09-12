# v0.6.0 native controls and render flags acceptance

Specifications: #120, #121. Windows 11 / Python 3.11 / TouchDesigner 2025.32050.
Implementation commit: `848b340` (following typed-page commit `add2a2a`).

## Final artifact and locked observations

- Agent SHA-256: `2c36a3b4bca3db058d3481c6d84b00882b0d9700e5c88465177a1944cda53cc1`.
- Canonical Agent source revision: `e4485a354734d6257d6e84891aff1fd224a5d1bfdf3fb530e624745ad8fb2571`.
- `td-agent inspect-artifact td-agent.tox` confirmed exact source/build/structure.
- Disposable project `E:/td-cli/.codex/custom-pages/render-build.toe`, SHA after
  formal save `0d3fa110877a9c88155e2678a9f3947ad9ff01a02ebcb5ae44bc59cd158dc63b`.
- Formal `parameters page-create --input-file controls.json` created float Gyrox
  (-1..1, default 0), toggle Manual (true), menu Source (manual/device).
  `parameters list /project1/controls` returned bounds, menus and labels.
- Duplicate page Request `01a0961c-e195-725c-9295-050dc4e2829d` was retained as
  failed `parameter_page_exists`, with the original page intact. The initial CLI
  error-code omission was fixed and the retained outcome subsequently read.
- Formal `ops state set /project1/geo/shape --display --render` and the same
  operation on sibling both succeeded. Readback proved the first remained true.
  The equivalent flags succeeded and read back on TOP, CHOP and Geometry COMP.
- A circle SOP, constant MAT, camera and Render TOP were connected through
  ordinary public Commands. The default torus POP and sibling were disabled.
  Three exports with SOP render on/off/on produced white-circle/black/white-circle
  images. Exports were visually inspected. Final PNG SHA:
  `d38ba3d68525d3f413443c5d557f1ea97fa6163f3c59fbf215e4aebb4ee40d76`.
- Cold reopened the final saved project. Requests
  `01a09626-4943-7d64-a73a-76d3a48672c5` (parameters),
  `01a09626-4e84-77e1-b76b-f19215c0e470` (flags), and
  `01a09626-5402-7403-bf06-0ddd10b63a0b` (PNG) all succeeded. All 30 existing and
  new parameter descriptors matched before save; the rendered PNG digest matched.

Queries had 10-second CLI deadlines and 15/30-second subprocess deadlines. Owned
disposable TD PIDs were recorded and closed individually after their phase.
The original artwork PID was not closed. Two coordinated source-Daemon reloads
preserved protocol 3 connectivity with the artwork's Agent 0.5.0. The background
Daemon remains a user-level service independently of agent sessions.

## Automated checks and independent review

Complete local gate: **485 tests passed**, Ruff check/format, mypy (19 files),
locked dependency check, Agent source inspection and Git whitespace check passed.
Independent review covered the whole implementation/spec range and found two
material issues: vanished target during rollback and oversized metadata/path
results. Both were fixed with regressions and re-reviewed. Formal CLI acceptance
also caught the CommandInput union and error whitelist omissions before delivery.

Simplification review: retained the existing parameter descriptor, state patch,
Request and rollback interfaces; no new scheduler, cooking API or generic attribute
access. Skill change is one line clarifying that batch accepts reads only. No
additional abstraction or deletion of unrelated work was justified.

## Limits and delivery state

Page creation supports new pages only and three scalar styles. The 16,384-byte
escaped definition budget includes repeated result paths. SOP render/display
flags are distinct from cooking/resource release. A flag readback on TOP/CHOP
does not imply geometry rendering. Render TOP Object parameter multi-object values
remain a known existing limitation; dynamic artwork expressions are outside this
change. No offline 0.5.0 migration claim is introduced; the artwork uses the
existing compatible same-PID trusted TOX import procedure.

PRs, staging identity and final release status are recorded separately after
GitHub CI and the protected human release-environment gate. This evidence alone
does not claim publication or installed executable upgrade.
