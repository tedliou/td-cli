# Phase 2 locked-runtime acceptance

Date: 2026-08-09

Platform: Windows 11, TouchDesigner 2025.32050, Python 3.11

Git source commit: `756d238f7b72401de3193ad09a32237b30757c65`

Canonical Agent source revision:
`24ad82716a68b5e80d718685f0cfa77611334523ea18e1913716caf80f2452bc`

## Source-first Agent Component

The retained localhost diagnostic bridge executed `agent/build_td.py` on the
TouchDesigner main thread. The resulting local derivative was independently
hashed and inspected:

- Artifact: `td-agent.tox`
- SHA-256: `85f98c7c9d576f0c12590c1f9d94002f832d8b059b61ea1345e8f2711949915a`
- Operators: `agent_extension`, `agent_manifest`, `auth_table`, `events_table`,
  `heartbeat_execute`, `socket_callbacks`, `socketio1`
- Registered capabilities: `ops.children`, `ops.get`, `parameters.get`,
  `parameters.pulse`, `parameters.set`

The diagnostic bridge remained bound to loopback and was not added to the
public `td` surface or Release Artifact.

## Five typed Commands

The public `td --json` executable submitted all five Commands to the live Agent
Component. The locked runtime returned the expected canonical Operator and
Parameter schemas for:

- `ops get /project1`
- `ops children /project1 --op-type base`
- `parameters get /project1 display`
- `parameters set /project1 display --bool false`
- `parameters set /project1 display --expression "True"`
- `parameters pulse /project1/td_agent reinitextensions`

Constant and expression writes were read back through `parameters get`. An
invalid expression produced `expression_invalid`, Request status `failed`, and
exit code 5 without changing the subsequent valid constant write.

## Two-Instance exclusive control

A second TouchDesigner 2025.32050 process was launched with an independent copy
of the acceptance project. Both processes were simultaneously Online:

- Selector `2110`, Instance ID `2110dd68-0d08-4f78-a0ea-644efa301536`
- Selector `1aa6`, Instance ID `1aa655c9-690d-4118-994d-eb52d3e741c7`

Using only the public CLI, `display=false` was written and read back through
Selector `2110`, while `display=true` was written and read back through Selector
`1aa6`. Omitting `--instance` while both were Online returned
`instance_selector_ambiguous` with exit code 4. This proves that a Command is
routed exclusively to its selected TouchDesigner Instance and that no arbitrary
Instance is selected when the target is ambiguous.

The second acceptance process was then stopped by its exact PID. The original
TouchDesigner process, Daemon, and retained diagnostic bridge remained running.
