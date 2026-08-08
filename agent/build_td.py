"""Build `td-agent.tox` inside TouchDesigner 2025.32050 from canonical text."""

import json
from pathlib import Path


def build(source_dir, output_path, source_revision):
    source = Path(source_dir)
    output = Path(output_path)
    project = op("/project1")  # type: ignore[name-defined]
    existing = project.op("td_agent")
    if existing:
        existing.destroy()
    agent = project.create(baseCOMP, "td_agent")  # type: ignore[name-defined]

    manifest_dat = agent.create(textDAT, "agent_manifest")  # type: ignore[name-defined]
    manifest_dat.text = (source / "manifest.json").read_text(encoding="utf-8")
    extension_dat = agent.create(textDAT, "agent_extension")  # type: ignore[name-defined]
    extension_dat.text = (source / "extension.py").read_text(encoding="utf-8")
    callbacks_dat = agent.create(textDAT, "socket_callbacks")  # type: ignore[name-defined]
    callbacks_dat.text = (source / "socket_callbacks.py").read_text(encoding="utf-8")
    heartbeat_dat = agent.create(executeDAT, "heartbeat_execute")  # type: ignore[name-defined]
    heartbeat_dat.text = (source / "heartbeat_execute.py").read_text(encoding="utf-8")
    heartbeat_dat.par.start = True
    heartbeat_dat.par.framestart = True

    events = agent.create(tableDAT, "events_table")  # type: ignore[name-defined]
    for event in (
        "registered",
        "registration_error",
        "request_dispatch",
        "result_recorded",
        "daemon_draining",
    ):
        events.appendRow([event])
    auth = agent.create(tableDAT, "auth_table")  # type: ignore[name-defined]
    socket_dat = agent.create(socketioDAT, "socketio1")  # type: ignore[name-defined]
    socket_dat.par.url = "http://127.0.0.1:9982"
    socket_dat.par.delay = 1000
    socket_dat.par.callbacks = callbacks_dat
    socket_dat.par.clamp = True
    socket_dat.par.maxlines = 100
    socket_dat.inputConnectors[1].connect(events)
    socket_dat.inputConnectors[3].connect(auth)

    if hasattr(agent.par, "ext0object"):
        agent.par.ext0object = extension_dat
        agent.par.ext0name = "Agent"
        agent.par.promoteextension0 = True
    agent.save(str(output))
    evidence = {
        "source_revision": source_revision,
        "touchdesigner_version": "2025.32050",
        "operators": json.loads(manifest_dat.text)["required_operators"],
    }
    output.with_suffix(output.suffix + ".manifest.json").write_text(
        json.dumps(evidence, separators=(",", ":"), sort_keys=True), encoding="utf-8"
    )
    return evidence


if "args" in globals():
    build(*args)  # type: ignore[name-defined]
