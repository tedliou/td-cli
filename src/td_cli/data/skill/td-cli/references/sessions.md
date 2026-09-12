# Sessions and saved projects

The Agent Component (`td-agent.tox`) is loaded once into the user's project.
The per-user Daemon routes requests; stopping it does not close TouchDesigner.
An Instance ID identifies a live runtime, not a `.toe` file or a PID. Relist after
reconnection or project/Agent changes instead of reusing an old selector blindly.

Start with `td --json --timeout 10 instances list`. An empty list does not prove
a defect: TouchDesigner may still be loading, the project may not contain an
Agent, or its Agent/build may be incompatible. Match `project metadata` to the
intended project before mutation. `synchronizing` means registration is not yet
ready for commands. Observe boundedly; do not close a user's manually opened TD
to make it attach. The Agent's Connection page shows its connection state.

The Daemon starts automatically on first online CLI use. `td-daemon status --json`
is a read-only diagnostic; `td-daemon start` explicitly starts it without a console
window. An unhealthy or incompatible runtime requires diagnosis rather than an
automatic replacement. Agent 0.5.0 waits for missing authentication material and
can connect when the Daemon appears, without reopening the project.

If the user asks to open a project, use `td project open --help` and supply the
actual TouchDesigner executable and `.toe`. This launches independently and
returns a PID; it does not guarantee the Agent is online. Existing Instances stay
open. Local Windows process creation runs outside the caller job; if Windows rejects
the launch, inspect the reported error rather than retrying through another launcher.

Saving is a live operation; closing or force-killing TD is not a save step.
Read `td project save --help`: supply the current absolute `.toe` path and its
current on-disk SHA-256 (`Get-FileHash -Algorithm SHA256` in PowerShell). The
precondition prevents overwriting a different disk revision. The successful
Request returns the written path, size and SHA-256; compare to disk while the same
TD PID remains alive. External TOX files are not saved. Unknown results require
Request inspection and disk observation, not a blind second save.

Executable upgrades do not replace an embedded Agent. The user can load the
released TOX in their running project. The explicit `td-agent upgrade-project`
command is a separate offline migration for known saved Agent versions: consult
its help and release notes. Its closed-project precondition is not a general
requirement for connecting, editing, or saving.
