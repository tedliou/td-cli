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

## Completed acceptance and review

The final local gate passed: 471 tests, Ruff lint/format, mypy (19 files),
locked dependency resolution, Agent source inspection, and diff whitespace check.
Independent Standards review of `1081d17...d89c923` found no blocking violations.
Spec review found that `td version` inadvertently started the Daemon; the fix uses
an offline client and has a regression test. Follow-up review of the migration
and version fix found no blocking issues.

- Same-PID live save on v0.5.0 PID 2640 succeeded (Request
  `01a095b3-f16d-73e4-96f1-2a21db11738b`, 53314 bytes), disk SHA-256
  `2ccf22dcf20b6eb9281b97934e3350d20577c8b3741712fe4bff47919a305b01`.
  A fresh metadata Request succeeded afterward.
- After Daemon stop, a fresh CLI call started v0.5.0 PID 6368 and reconnected
  the same TD PID and Instance. Window enumeration every 50 ms for 150 samples
  observed no new visible window and unchanged TD foreground HWND 3081612.
- Cold opening the saved copy before any auth token existed used PID 21372.
  The first CLI call started the Daemon in 3.719 seconds; a subsequent metadata
  Request `01a095b8-ca67-7c1b-8025-9deeed402477` succeeded in that same PID.
  `Connectionstate` read back `online`; out1 readback succeeded. A deliberately
  wrong save checksum returned `project_file_changed` and preserved the file.
- With timeline playback paused, locked TD PID 10840 successfully completed
  metadata Request `01a095c2-6ff9-7a9b-ad85-d209c5559505` and node creation
  Request `01a095c2-7474-703c-ae17-df32dd89ba07`. The scheduler remained on the
  main thread; timeline pause did not require restarting TD.
- Formal offline `upgrade-project` migrated an untouched artwork copy from the
  explicitly recognized v0.4.0 Agent to v0.5.0, preserved its backup, and wrote
  SHA-256 `e8eca6a386af1378b455a1c02bba891f2e86fd2415e17a58551c835e8debbb73`.
  A second invocation was unchanged. Cold-open metadata in PID 8504 succeeded
  (Request `01a095c0-25ae-7a8c-be96-3fe5000d8070`). The v0.3.1 recognition path
  remains covered; unknown modified Agents are rejected. The new read-only
  Connectionstate parameter is copied only after known structure validation.

Machine-readable observations are in `docs/evidence/designer-sessions/`.
Only disposable copies were mutated. Original artwork regression is owned by
its project maintainer after published installation.

## Remaining gates

Exact-source Agent staging, CI, promotion, human release-environment approval,
remote download and installed-version verification, and final original-artwork
regression. This document does not claim those have passed.
