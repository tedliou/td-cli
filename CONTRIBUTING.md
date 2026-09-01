# Contributing to td-cli

## Issues and specifications

GitHub Issues are the source of truth for defects, specifications, and implementation tasks. Start
with an issue that states the observable outcome, constraints, public test seam, acceptance criteria,
and explicit non-goals. Use the repository's five triage labels as documented in
`docs/agents/triage-labels.md`.

Changes to Protocol messages, Daemon request processing, TouchDesigner execution, persistence, or
release behavior require an approved specification before implementation. Resolve design branches
against primary documentation and recorded project evidence; document material decisions on the
specification issue before creating implementation tickets.

## Branches and commits

Begin each implementation round from a clean worktree on a descriptive branch. Commit completed
phase outputs before beginning the next phase so research, specification, implementation, and
acceptance evidence remain independently reviewable. Keep commits cohesive and do not rewrite
unrelated contributor work.

## Development workflow

Use Python 3.11 and the locked environment:

```powershell
uv sync --locked --python 3.11
```

For behavior changes, agree on the public test seam in the issue and use one red-to-green vertical
slice at a time. Prefer real in-process or local integration interfaces over mocks of project code.
Mock only true external dependencies, time, or failure injection that cannot be exercised safely.
Tests must assert observable behavior and survive internal refactoring.

Runtime reliability changes must follow
`.agents/skills/td-runtime-reliability/SKILL.md`. Preserve the Request and TouchDesigner Instance
invariants named there, and reserve locked TouchDesigner testing for facts that substitutes cannot
prove. Do not introduce automatic mutation retries, compatibility fallbacks, or alternate transports
without an approved specification.

Run the complete local gate before review:

```powershell
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv lock --check
uv run python -m td_cli.agent_tool inspect-source agent
git diff --check
```

Add focused packaging or locked-runtime checks when the issue changes those seams. CI should run each
test once; avoid a second invocation of files already included by the full suite.

## Documentation

`README.md` is the canonical English user and developer manual. `README.zh-TW.md` and
`README.zh-CN.md` are the Traditional Chinese and Simplified Chinese manuals. A user-visible change
updates all three in the same pull request while preserving reciprocal language links and the shared
ordered `doc-section` markers. Translate meaning and terminology; do not mechanically require equal
headings, line counts, or prose.

Mandatory agent execution policy belongs in `AGENTS.md`. Human contribution policy belongs here.
Domain vocabulary belongs in `CONTEXT.md`; durable architecture decisions belong in `docs/adr/` only
when a real decision needs a lasting rationale. Acceptance evidence belongs under `docs/` and must
record the exact build, artifact, commands, observations, and limitations.

## Pull requests

A pull request must link its specification and implementation issues, describe observable behavior
and compatibility changes, list verification evidence, and disclose any locked-runtime work that was
not executed. Review the complete branch range for both repository standards and specification
compliance. Resolve every material finding before merge.

Pull requests target `develop`. Promotion from `develop` to `main` and Release publication are
separate maintainer-controlled operations after green CI and required runtime acceptance. A pull
request does not authorize publishing a Release.
