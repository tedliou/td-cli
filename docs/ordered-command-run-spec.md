# Ordered command run

The installed v0.6.0 CLI takes 1.52–1.59 seconds for `--version` alone on the
artwork workstation. Constructing hundreds of distinct native Operators pays
this process cost for every mutation. Existing `batch.execute` accepts only
read-only Commands (up to 16); copy and trusted TOX import reuse graphs but do
not express distinct typed authoring operations.

## Public contract

`td --json --instance SELECTOR --timeout 120 commands execute --input-file plan.json`
runs an ordered list of existing Protocol Commands in one CLI process. Input is
`{"commands":[{"name":"ops.create","input":{...}}]}`, limited to 256 entries
and 1 MiB UTF-8. Validate the entire plan with the existing `Command` model
before contacting the Daemon. No shell command strings, arbitrary execution,
new Agent command, or nested `batch.execute` are accepted.

Resolve one Instance once, then submit and await each independent Request in
order. The global timeout is one deadline for the whole run, not per item.
Before every submission, emit and flush a JSON line containing zero-based
index, generated Request ID, Instance ID, and `event: submitting`. Emit the
full terminal Request snapshot in `event: completed`. Stop immediately on any
non-success or client error, emitting `event: stopped` with the Request ID,
error and count of unsubmitted entries. Emit `event: finished` only after all
items succeed. JSONL is emitted regardless of the global `--json` flag.

This is not atomic: earlier changes remain after later failure. There is no
rollback across Commands, automatic resubmission, resume, or continue-on-error.
After interruption or uncertain outcome, query the recorded Request ID before
preparing a new plan. A submitting record indicates intent, not acceptance.
Existing per-Command validation, protections, persistence, FIFO, result limits,
and unknown-outcome semantics remain authoritative.

## Acceptance and design review

Test full-plan validation without network effects; dependent create/configure
ordering; first failure stopping later submissions; transport uncertainty with
Request ID retained; aggregate deadline; and interruption-safe flushed progress.
Locked TD acceptance creates a disposable graph using the public CLI, verifies
results with read-only Commands, and compares repeated process invocations with
one ordered run. Preserve the artwork and its Daemon during offline work.

Client orchestration is selected over Agent-side mutation batching: every item
keeps its existing independently queryable lifecycle and bounded execution.
Persistent HTTP connection optimization and wire protocol changes are non-goals
unless runtime evidence demonstrates they are necessary.
