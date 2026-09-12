---
name: td-cli
description: Inspect and edit live TouchDesigner projects through td-cli, including operator graphs, parameters, signals, and saving.
---

Use the installed `td` CLI as the control interface. TouchDesigner owns its GUI,
timeline and project lifetime; a CLI Request is a bounded operation on an existing
Instance, not a license to close or replace that Instance.

Discover the installed interface with `td --version`, `td --help`, and the relevant
subcommand's `--help`. For operator names and support, use `td ops types --help`
and its catalog queries. Read the actual graph/parameters before editing it.
An operator type's existence in TouchDesigner does not imply td-cli supports every
operation on it. Use exact absolute operator paths, and select an Instance when
more than one is online.

- For starting, selecting, reconnecting, upgrading or saving, read
  [sessions.md](references/sessions.md).
- For graph design, cooking, parameter modes or checking live signals, read
  [touchdesigner.md](references/touchdesigner.md). Follow its official-document
  links only for the concept the current task needs.

`td --json --timeout 10 instances list` starts the per-user Daemon when needed.
Use `--instance <selector>` before the subcommand. Keep queries and mutations
bounded; a timeout only ends the wait. Retain the Request ID and inspect it using
`td requests get <id>` before deciding another action. Do not repeat a mutation
whose outcome is unknown.

Preserve the user's design and unsaved work. Verify the requested graph/value
and save result, not merely a successful submission. Improve this skill from a
reproduced usage failure: correct the smallest relevant reference rather than
adding a new universal workflow. Its interface examples target td-cli 0.5.0;
installed help is authoritative when versions differ.
