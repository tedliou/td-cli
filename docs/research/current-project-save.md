# Current-project save

Specification: https://github.com/tedliou/td-cli/issues/108

The locked TouchDesigner 2025.32050 vendor stub at
`bin/Lib/tdi/tdClasses/Project.py:258` documents
`project.save(path, saveExternalToxs=False) -> bool`. The
[official Project Class](https://docs.derivative.ca/Project_Class) describes the
current `folder` and `name`. Omitting the path increments the filename; always
pass the exact current path. The stub mentions an overwrite prompt, so a
disposable locked-runtime probe must demonstrate unattended exact-current-path
saving before release. Do not probe this on a user's unsaved work.

The typed mutation accepts an expected local existing `.toe` path and disk
SHA-256. This detects a stale disk version at preflight, not an atomic
cross-process compare-and-swap: callers must exclude concurrent writers.
Digests are streamed with a 64 MiB limit and stable file identity checks.
Reparse traversal is rejected. Save only the current project; external TOX
saving remains disabled. Return the actual disk path, size, and digest.

Preflight rejection precedes any save. A save call followed by an exception,
false result, or unverifiable disk state is `project_save_outcome_unknown`.
There is no rollback or automatic mutation retry. Existing retained Request,
FIFO, generation, and disconnect semantics remain unchanged. The existing
trusted-asset execution lease is eight seconds; complete save and hashing time
must be measured in the locked build.

Independent design review accepted this scope with these constraints. No
save-as, file-lock framework, CHOP API, or alternate execution transport is
introduced. The OSC workflow can create conditional Operators explicitly and
read actual CHOP values through the existing parameter-expression interface.
