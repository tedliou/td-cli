# Ordered command run acceptance

Issue: [#125](https://github.com/tedliou/td-cli/issues/125).
Locked platform: Windows 11, TouchDesigner 2025.32050.

## Evidence

- Complete local gate passed: 506 tests, Ruff lint/format, mypy (20 source
  files), locked dependency check, Agent source/artifact inspection, and diff
  whitespace check. The existing Starlette/httpx deprecation warning remains.
- Installed v0.6.0 `td.exe --version`, three bounded subprocesses: 1.518,
  1.592, 1.582 seconds. Warm source CLI startup: 1.015 seconds.
- Artwork public CLI three-mutation smoke: 1.496 seconds, independent Requests
  approximately 100 ms apart. Evidence maintained by the artwork owner:
  `captures/native-scenes-20260913/stream-smoke.jsonl`.
- Disposable project, PID 11772, selector `c924`, new Agent v0.7.0: ordered
  create/state/read completed in 1.408 seconds. Request IDs:
  `01a0994f-297b-741d-a289-09ffaf61d55a`,
  `01a0994f-29e0-7b7d-87a5-881b5b97dc17`,
  `01a0994f-2a40-7273-893c-8bd8048dadda`.
- Partial failure: state mutation succeeded at
  `01a0994f-2ef6-7fd7-9346-97bd58ac5422`; missing Operator lookup failed at
  `01a0994f-2f5c-7543-9fac-8569171c4c0d`; subsequent destroy was never submitted.
  Exit 5, `stopped.unsubmitted=1`, elapsed 1.322 seconds.
- Real artwork independently hit a disabled Sphere SOP parameter. The public
  runner stopped; the owner corrected only the remaining plan and continued.
- The source public CLI also works with the existing v0.6.0 Agent and Daemon;
  there is no new runtime Command or transport behavior.
- Independent review found initial Instance-selection errors lacked a stopped
  event. This was fixed and tested with and without `--json`; reviewer confirmed
  the fix and found no remaining substantive code issues.

## Release artifact and migration checkpoint

Agent logic is unchanged; its release manifest is v0.7.0. Built TOX SHA-256:
`7aeed719e4d6f94ab1a07a79113c6dd2b8b7e5ca27cb843a989acc9940bda126`.
Canonical source revision:
`a284635ea70f78f5e95be665579a4c8d208949380b0802e5da4b237b61816f24`.

The v0.6.0 canonical offline source whitelist was computed from the previously
published TOX `2c36a3b4bca3db058d3481c6d84b00882b0d9700e5c88465177a1944cda53cc1`.
The disposable project imported it through `ops.tox.import`, preserved the
authored probe graph, saved SHA-256
`d4109aff907cb10e1a35ea9840ad250b0ccb2ce82cb966f9743b441ac5311b0b`,
then closed normally. Offline upgrade correctly refused while the independent
artwork TD process remained open. No gate was bypassed.

Actual offline upgrade, backup comparison, cold reopening, same-version no-op,
and final release/install verification are pending the coordinated all-TD-closed
window. This checkpoint does not claim release completion.

## Simplification review

One CLI orchestration module reuses the existing typed Protocol and Request
client. No mutation batch scheduler, extra runtime state, fallback, retry loop,
or duplicated validation was introduced. Existing read-only batch remains
unchanged. One independently verifiable capability delivered since the previous
simplification review; release review includes the new migration whitelist and
owned-process cleanup. The test TD was normally closed; the artwork and product
Daemon remained running.
