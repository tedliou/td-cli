# Designer-owned sessions (v0.5.0)

Specification: [#114](https://github.com/tedliou/td-cli/issues/114).
Base: `1081d17`, TouchDesigner `2025.32050`, Windows x86-64.
Published release and installed-artifact evidence is recorded below.

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

The initial integrated local gate passed: 471 tests, Ruff lint/format, mypy (19 files),
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

Promotion CI run `34696348285` exposed a test-harness deadline defect: the job
survival probe read its result after only eight seconds although its launcher
allows ten seconds. The corrected probe observes a bounded fifteen-second outer
deadline, writes results atomically, and retains caller stderr/exit status.
Production launch deadlines are unchanged. Known test children and caller jobs
retain explicit cleanup; a child also has a twenty-second self-exit bound.
The complete local gate passed again (471 tests).

Subsequent develop CI run `34696639728` returned the launcher's explicit unknown
outcome after ten seconds. The trace locates this only at the whole WMI invocation,
not a specific PowerShell/provider substep. This exposed hidden product limits:
client startup capped the visible command budget at five seconds and project open
ignored it in favor of ten. Both now honor the visible total command budget;
Daemon start exposes the same bounded timeout (default thirty seconds). Request
waiting receives only the remaining budget. No launch is retried after timeout.
The job-semantics probe explicitly allows thirty seconds plus five seconds of
outer observation overhead and records elapsed launch time; short-deadline unknown
behavior has a separate deterministic regression. Product defaults are not reset
for each phase. Incremental review also found a fixed health-probe timeout that
could accept late readiness; both initial and polling probes now check remaining
time before and after the response. The finding is closed with deterministic
late-ready regressions. Final gate after these changes passed all 477 tests,
Ruff lint/format, mypy, lock/source inspection, and diff checks.

## Publication and installed acceptance

[v0.5.0](https://github.com/tedliou/td-cli/releases/tag/v0.5.0) was published on
2026-09-12 at 13:52:02 UTC as immutable Release `387586357`. The annotated tag
points to exact main `c87c6f73352fe2c8efc7967c3d26cc5ec44e344d`.
Implementation PRs #115, #117 and #118 and promotion #116 were merged after green
CI. Exact-main CI `34697238535` passed. A human approved the protected release
environment; Publish Release run `34697374785` then completed successfully.

Stage run `34697263925` produced artifact `10299495630`, digest
`sha256:bf5a775f80abf071fd83e43e1c61530276fbcb3564b08a8beead27efea4fc370`,
expiring 2026-10-12. A fresh remote download matched its digest, source commit,
Agent source revision and TOX hash. All seven public Release assets were downloaded
and matched their GitHub digests; all ZIPs also matched `SHA256SUMS`.

The initial GitHub CLI download timed out after 180 seconds. Direct public CDN
transfer measured about 100 KB/s; bounded range-resume downloads completed without
redownloading verified files. The official installer consumed those same verified
assets through its `AssetBaseUri` option and a temporary loopback server (PID 9208,
port 59628). The server was closed after installation. Installed executables,
TOX, manifest and verification files match the public ZIP bytes exactly.

`td`, `td-daemon` and `td-agent` report 0.5.0. A fresh installed
`td --json --timeout 30 instances list` started the missing Daemon and returned
success in 6.187 seconds; its empty list was expected because no TD was open.
The Daemon was PID 21900 on `127.0.0.1:9982`.

The published `td-agent install-skill` installed all three skill files into
`C:/Users/Ted/.codex/skills/td-cli`. Their content matches exact-main canonical text
(the Windows package uses CRLF and Git uses LF); the skill is discoverable on the
next agent turn. Machine-readable records are `release-v050.json`,
`installed-v050.json` and `release-download-transfers.json` in the evidence folder.

Automatic execution review rejected recursive deletion of the local, nonsecret
`.codex/stage-v050-main` staging directory with `blocked by policy`. It remains
untouched; no alternate deletion method or policy bypass was attempted.

## Original artwork regression

The artwork maintainer completed the final gate using installed v0.5.0 commands.
Formal offline migration preserved the original backup SHA-256
`b98ccfe67b9bed9a3b689534ef3b317bd004c9cab27f325cf376c9fdf6e745da`.
The 47-channel OSC regression passed values, errors, pulses, offline behavior and
source isolation. Same-PID save in PID 15608 succeeded (Request
`01a095f4-1490-7af3-aacc-bbb5f5347217`, 65770 bytes), with disk SHA-256
`53468a917b684c2a95d4da713e079a9e6b97889a8d8332ed37de9468e4fa78ff`.
Fresh metadata Request `01a095f4-1eb6-7380-91dd-a9a2b8a20962` succeeded in that PID.

After normal window close and confirmed process exit, the installed CLI opened
PID 22860. Agent 0.5.0 registered online as Instance
`ee4137f0-1fe2-49e4-b449-0d36dcdb4aa7`. Cold readback passed all twelve controls,
four direct Select CHOPs, the unique out1, and existing relative Resources media
paths. Initial brain_ready/motion_ready were zero and temporary probes were
removed. Two new OSC bundles then passed all twelve addresses with two messages,
seen/fresh true and no error (Request `01a095f6-bf6f-7d7d-b85b-9ad8879638f3`).
The disk checksum remained unchanged. The original artwork stays open for its
user; td-cli does not own or automatically close that session.

The evidence folder contains the original-save, reopen-controls, reopen-OSC and
47-channel acceptance JSON reports. All publication, installation and artwork
gates are complete; only the explicitly policy-blocked local staging cleanup
remains outstanding.
