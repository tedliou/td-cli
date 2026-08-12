# TouchDesigner CLI Control

This context describes the runtime identities and control messages used to operate a local TouchDesigner session through td-cli.

## Language

**TouchDesigner Instance**:
A single live TouchDesigner process and runtime session. It is not the `.toe` project loaded by that process.
_Avoid_: TD project instance, project instance

**Agent Component**:
The self-contained `td-agent.tox` component loaded into a TouchDesigner project.
_Avoid_: sidecar, agent process

**Daemon**:
The single per-user background runtime that authenticates local clients, retains Requests, and routes Commands to TouchDesigner Instances.
_Avoid_: Windows Service, per-instance sidecar

**Instance ID**:
The UUID that identifies a TouchDesigner Instance for the lifetime of its current runtime session.
_Avoid_: PID, project ID

**Connection ID**:
An identifier issued by the daemon for one successfully registered connection generation of a TouchDesigner Instance. It distinguishes current traffic from messages arriving through an older connection.
_Avoid_: Instance ID, socket ID, session ID

**Selector**:
A short prefix that uniquely identifies a retained TouchDesigner Instance for convenient CLI selection. It remains associated with that Instance while the daemon retains its registration.
_Avoid_: short ID, instance name

**Online Instance**:
A registered TouchDesigner Instance whose Agent Component currently has a live, healthy connection to the daemon and can accept Commands.
_Avoid_: connected project, active project

**Offline Instance**:
A recently disconnected or unresponsive TouchDesigner Instance whose registration is temporarily retained for observation but cannot accept Commands.
_Avoid_: disconnected project, stale instance

**Draining Instance**:
A TouchDesigner Instance preparing to shut down that may finish its current Request but cannot accept another one.
_Avoid_: closing project, shutting-down connection

**Command**:
A typed TouchDesigner operation routed by the daemon to exactly one TouchDesigner Instance.
_Avoid_: script, arbitrary Python

**Operator Path**:
The canonical absolute TouchDesigner path of an Operator. Public Commands do not accept relative paths, shortcuts, patterns, or traversal segments.
_Avoid_: relative OP path, OP shortcut

**Regular Connection**:
A left-to-right data wire between regular Operator input and output connectors. It is distinct from a COMP Hierarchy Connection.
_Avoid_: hierarchy wire, parent connection

**COMP Hierarchy Connection**:
A top-to-bottom parent/child connection between compatible Object COMPs or compatible Panel COMPs. It is not a Regular Connection and never connects across the Object and Panel hierarchy kinds.
_Avoid_: regular wire, COMP data connection

**Operator Family Inspection**:
A bounded, read-only snapshot of the latest cached CHOP, DAT, TOP, SOP, POP, or MAT family metadata for one Operator. It does not force a cook, return family content, or replace common Operator state, Parameter, connection, or DAT content Commands.
_Avoid_: Operator dump, arbitrary attribute inspection, family content preview

**Parameter Value**:
A typed boolean, integer, number, string, one canonical Operator Path (or null),
or a bounded ordered list of canonical Operator Paths, read from or written to
one Operator Parameter. Expression source text is mode-specific. Export and
Bind use typed source identities rather than Parameter Values. It is not an
arbitrary Python object.
_Avoid_: untyped value, serialized object

**Request**:
A submission of one Command carrying a Request ID whose result remains queryable after a client timeout.
_Avoid_: command invocation

**Protocol Version**:
The version of the wire messages and JSON schemas shared by the daemon and Agent Component. Protocol v1 does not imply that the pre-1.0 CLI surface has a SemVer stability guarantee.
_Avoid_: CLI version, Release version

**Unknown Request**:
A dispatched Request whose completion cannot be determined after its TouchDesigner Instance disconnects. It remains queryable and must not be dispatched automatically again.
_Avoid_: failed request, lost command

**Release**:
A single versioned distribution containing the three executable tools and the Agent Component.
_Avoid_: CLI-only release, agent-only release

**Release Artifact**:
One of the four independently downloadable ZIP archives in a Release: one archive for each executable tool and one for the Agent Component.
_Avoid_: release bundle, installer

**Bootstrap Installer**:
The PowerShell script distributed with a Release that installs or upgrades all Release Artifacts for the current Windows user through one command.
_Avoid_: Release Artifact, system installer, package manager
