# Offline Agent upgrade

Protocol v3 preserves typed Command scalars that TouchDesigner's SocketIO DAT
coerces when they are sent as native nested objects. An installer cannot replace
an Agent embedded in an existing saved project.

Locked 2025.32050 traces show ordinary v3 daemon restart reconnects without any
extra recovery. After a v3 Agent receives v2 `registration_error`, however, its
next native connection emits `register` but never delivers the daemon's
`registered` callback. The daemon expires the unsynchronized connection. No DAT
exception is reported. Generic timed reset is rejected: it would repeatedly retry
an incompatible daemon. Registration rejection is terminal until initialization.

The approved independent upgrade entry point is `td-agent upgrade-project`.
It operates on a closed, saved local `.toe`, independently of command Protocol,
using Derivative's [toeexpand/toecollapse format](https://docs.derivative.ca/.toe).
Its initial support is deliberately limited to canonical Agent 0.3.1 to 0.4.0
and identical 0.4.0 verification on TouchDesigner 2025.32050.

The command validates the target artifact digest and locked-build manifest,
uniquely identifies the known embedded Agent, preserves its operator path, and
replaces only its known component subtree in scratch space. A vendor round trip
and a second expansion must preserve every unrelated file byte for byte. Unknown
Agent children, external linkage, versions, builds, ambiguous matches, active
TouchDesigner processes, or changed source digests fail without replacing the source.
The original receives a unique verified backup before same-volume atomic replace.
All TouchDesigner processes must be closed: launch command lines cannot prove which project is currently open. Digest checks cannot exclude a writer racing the final replace;
exclusive project ownership remains an operational precondition.

This CLI entry point is the durable upgrade interface, not a fallback command
transport or arbitrary code execution API. Future versions extend its explicit
supported migration matrix. Historical 0.3.1 binaries cannot retroactively gain
it: run the new `td-agent` tool for the first offline migration.

Independent review required canonical identification beyond component name,
preservation of root path, no-op for an identical target, scratch-only vendor
tools, and complete post-collapse verification. These decisions were accepted.
