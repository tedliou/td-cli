# Designer-owned sessions (v0.5.0 candidate)

Specification: [#114](https://github.com/tedliou/td-cli/issues/114).
Base: `1081d17`, TouchDesigner `2025.32050`, Windows x86-64.
This is an in-progress acceptance record, not publication evidence.

## Diagnosis and decisions

- v0.4.0 Daemon startup used `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`,
  inherited standard handles, and did not escape the caller job. The CLI did not
  start a missing Daemon. A dead PID 23276 retained in `daemon.json` was reported
  as starting/unhealthy on 2026-09-12.
- A first Agent initialization without `auth.token` reproducibly raised
  `FileNotFoundError`, disabled its socket, and did not recover when the token
  appeared. The red test now passes using the existing heartbeat scheduler and
  the same Instance ID. No second network retry mechanism was introduced.
- A suspended `CreateProcess` experiment proved that successful breakaway can
  still leave a child in an ancestor job. That implementation was removed.
  The single production launcher uses local WMI `Win32_Process.Create`, which
  does not associate a child with the caller job. Parameters travel as encoded
  JSON into a fixed PowerShell script, not interpolated shell expressions.
- Real Windows tests create kill-on-close caller jobs both permitting and
  forbidding breakaway. Closing the caller job leaves the launched child alive;
  only that test-created child is cleaned up afterward. Both cases passed.
- Existing live save worked on the artwork copy without closing TD. A separate
  red regression exposed missing project error codes in the client's compatibility
  guard: `project_file_changed` became `protocol_incompatible`. The narrow fix
  preserves all four existing save failure codes and the Request identity.

Primary sources checked 2026-09-12:
[Windows jobs](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects),
[Win32_Process.Create](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/create-method-in-class-win32-process),
[Project Class](https://derivative.ca/UserGuide/Project_Class),
[SocketIO DAT](https://derivative.ca/UserGuide/SocketIO_DAT),
[OpenAI skill guidance](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra).

## Runtime observations

- Original artwork was accidentally opened for a read-only baseline (PID 7716),
  never mutated or saved, then closed normally. Its SHA-256 remained
  `b98ccfe67b9bed9a3b689534ef3b317bd004c9cab27f325cf376c9fdf6e745da`.
  Startup took about 50 seconds. Its first registration remained synchronizing
  and expired after six seconds; this is an observation, not evidence that
  closing it is required. Subsequent copy startup registered normally.
- WMI-launched copy PID 17652 had an interactive window in user session 1.
  Public `project.metadata` Request `01a095af-f122-7adc-af52-c3ffd66003e1`
  succeeded. Live save Request `01a095af-f5b8-7c88-9e40-cac2d6823f9c` succeeded
  while that PID remained alive, writing 57130 bytes with SHA-256
  `c30c2ca7e49c57a0a6378125cf73bc69bc47550d9c38a28f4b5cc0106e5ced87`.
  The Agent was still v0.4.0, so no save algorithm rewrite is claimed.
- Candidate Agent built from source revision
  `eb1de2cd2228811ea7b79e2b6293faf0c4436be2832f2054065648a1d8919a0e`, artifact
  SHA-256 `1281011400ef5bca24bff4748b0e3ac01e9045944104805f8e3756cb90db30fd`.
  It registered as v0.5.0 in copy PID 2640, Instance
  `f90510e0-9e72-4d38-bdca-df48afb43222`; metadata Request
  `01a095b2-f6fa-7342-8c3d-1b72bede6d7f` succeeded.

## Skill and simplification

The bundled skill is a short entry point with session and TouchDesigner theory
references. It routes to real help and a new bounded, offline `ops types`
catalog rather than copying an operator manual. An independent forward test ran
help/catalog queries for manually opened TD, CHOP-to-DAT value inspection,
conditional OSC creation, and live save. It found one nonexecutable `list/get`
abbreviation; this was replaced with two actual commands.

Simplification retains the existing transport, Request lifecycle, save algorithm,
and heartbeat scheduler. The discarded suspended-launch implementation is not
kept as a fallback. The suite's fixed September 1 completed timestamp had aged
beyond retention; the migration test now uses a current terminal timestamp while
preserving its behavioral assertions. No distinct reliability case was removed.

## Remaining gates

Final local gate, same-PID restart/save/readback, cold reopen, missing-token locked
runtime, daemon window/focus observation, independent code review, exact-source
Agent staging, CI, promotion, human release-environment approval, remote download
and installed-version verification, and final original-artwork regression.
