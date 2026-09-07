# v0.4.0 typed authoring acceptance

Environment: Windows 11, Python 3.11, TouchDesigner **2025.32050**.
Specifications: #108 (current-project save), #109 (typed SocketIO dispatch and
StrMenu), #110 (offline upgrade), #111 (derived table reads).

## Reproduction and decisions

- A canonical daemon Request retained JSON `false`, but SocketIO DAT delivered
  Python integer `0` inside its nested `command.input.value`. The Agent correctly
  rejected the altered type. Protocol v3 sends the complete Command as canonical
  JSON text and explicitly decodes it; there is no scalar guessing or v2 transport.
- Select CHOP `channames` is an open StrMenu, not a closed Menu. It now accepts
  channel pattern strings; ordinary Menu membership validation is unchanged.
- Same-protocol v3 restart trace delivered `onOpen`, `registered`, execution sync,
  and a heartbeat every two seconds. Initial v2 rejection followed by v3 restart
  instead emitted registration without receiving `registered`; the daemon expired
  it. Generic timed reset was removed after independent review. Protocol rejection
  now disables the connection until initialization. The independent offline upgrade
  command solves this migration without keeping an incompatible transport alive.
- Scheduled callbacks now carry the live COMP/DAT reference through TOX staging;
  connection generation state belongs to the owner OP and runtime session. Copied
  storage cannot reuse another owner's generation. Request retention stays shared
  per Instance; protection uses the executing Agent's current path.

## Locked-runtime observations

CLI probes used `uv run td --timeout 8 --instance <selector> --json ...`.
Disposable processes were recorded and stopped after their acceptance stage.
No arbitrary execution API or UI fallback was used to operate the artwork.

| Probe | Evidence |
| --- | --- |
| Real boolean mutation and readback | `parameters set /project1/bool_probe active --bool false`, Request `01a07c5c-ad64-7164-af53-75adffc668fc`, succeeded with boolean false; readback `01a07c5c-d5fe-72a0-9ab3-780b4960a41f` remained false. |
| Open StrMenu pattern | Five band names accepted by `channames`, Request `01a07c5c-d104-74b0-9eb4-e619db616e02`; readback `01a07c5c-db13-7408-afd1-4b8102ac1e48` returned the exact string. |
| Current-project save | Request `01a07c5d-0536-7caa-803d-a196f048b493` saved disposable `upgrade-probe.toe`, 40946 bytes, SHA `9b4b1a0b4c4eb947f75917a40b1ef34f67f25743e03853c1331b3a6453cb0ac1`. Direct locked save measured 29.5223 ms with timeline playing, safely within the 8-second trusted-asset lease. |
| Offline migration | Closed original saved artwork 0.3.1 → 0.4.0. Source SHA `cc7567bcabb39c9a1ec09d629912a93fa5b72f90233924954e239b644fdfa06d`; upgraded SHA `50ddc3ae30e13217b78f7458bd44a645b1522bab442e80d9b11437646fa9192c`. Every unrelated expanded file and both vendor round trips were byte-identical; unique backup verified. |
| Identical target | A second offline command returned `unchanged` with the same SHA and no additional backup. |
| Cold open | Migrated disposable selector `de98` and original artwork selector `ee12` became online on v3. Metadata confirmed the original artwork path before restoration. |
| Artwork graph restoration | Trusted checkpoint SHA `3019ec9be4ab33255379b688a456f79e47170fa0f75f8f8e34bbb40f740ea353`, 4334 bytes, 26 operators. Formal TOX import then `ops.move` restored `/project1/muse_input`; staging container removed. |
| Real derived DAT read | Constant CHOP 0.75 → CHOP to DAT returned `[["0.75"]]`, Request `01a07c66-9568-7256-a65d-342b27be8bcc`. |
| Compatible Agent side-load | Latest artifact imported under `/project1/td_cli_runtime/td_agent`; new generation remained online, old Agent was formally destroyed, and real table read still returned 0.75 (`01a07c67-3ac6-7c5a-84e7-944061375015`). Same procedure updated artwork `ee12` without changing its selector or restarting daemon. |

The final Agent artifact for #111 is 34830 bytes, SHA
`06ce79ea641abf2804fadfc4195b01e3bba83be047101d745a3db84481c416b8`.
Its code differs from the prior cold-open artifact only by the derived table read
predicate. Compatible side-load and derived reads above used this exact artifact.

## Simplification and independent review

Removed speculative generic reconnect/reset after traces isolated the incompatible
handshake edge. Kept one typed command transport and one independent offline
upgrade entry point. The new read capability reuses bounded table reads instead
of adding a CHOP API. CI reads the Protocol constant instead of duplicating its
number. No open-ended diagnostic process or mutation retry remains.

Independent reviews covered standards and specifications across the complete
develop-to-branch range. Findings fixed: owner identity in copied storage, live
protected path, all-TD-closed requirement, staged artifact digest check, scratch
cleanup before commit, and verification of event subscriptions and child operator
descriptors. Local gates and final artwork acceptance are recorded with the PR.

Limits: `.toe` save/upgrade requires exclusive file ownership, not an atomic
compare-and-swap against arbitrary writers. Offline migration is restricted to the
documented versions and build. A release requiring human environment approval
cannot be represented as published before that gate passes.
