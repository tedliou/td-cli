"""Build `td-agent.tox` inside TouchDesigner 2025.32050 from canonical text."""

import builtins
import hashlib
import json
from pathlib import Path

LOCKED_TOUCHDESIGNER_VERSION = "2025.32050"


def locked_touchdesigner_version(application):
    touchdesigner_version = str(application.build)
    if touchdesigner_version != LOCKED_TOUCHDESIGNER_VERSION:
        raise RuntimeError(
            f"locked TouchDesigner {LOCKED_TOUCHDESIGNER_VERSION} required; "
            f"got {touchdesigner_version}"
        )
    return touchdesigner_version


def configure_agent_runtime(agent, extension_dat, heartbeat_dat, socket_dat, auth_table):
    del extension_dat, auth_table
    agent.par.ext0object = (
        "op('./agent_extension').module.AgentExt(me, project_info=project, app_info=app)"
    )
    agent.par.ext0name = "Agent"
    agent.par.ext0promote = True
    agent.par.initextonstart = True
    heartbeat_dat.par.start = False
    heartbeat_dat.par.create = False
    heartbeat_dat.par.framestart = False
    socket_dat.par.active = False


def build(source_dir, output_path, source_revision):
    touchdesigner_version = locked_touchdesigner_version(app)  # type: ignore[name-defined]
    source = Path(source_dir)
    output = Path(output_path)
    project = op("/project1")  # type: ignore[name-defined]
    existing = project.op("td_agent")
    if existing:
        existing.destroy()
    agent = project.create(baseCOMP, "td_agent")  # type: ignore[name-defined]

    manifest_dat = agent.create(textDAT, "agent_manifest")  # type: ignore[name-defined]
    manifest_dat.text = (source / "manifest.json").read_text(encoding="utf-8")
    catalog_dat = agent.create(textDAT, "operator_catalog")  # type: ignore[name-defined]
    catalog_dat.text = (source / "touchdesigner-2025.32050-operators.json").read_text(
        encoding="utf-8"
    )
    extension_dat = agent.create(textDAT, "agent_extension")  # type: ignore[name-defined]
    extension_dat.text = (source / "extension.py").read_text(encoding="utf-8")
    callbacks_dat = agent.create(textDAT, "socket_callbacks")  # type: ignore[name-defined]
    callbacks_dat.text = (source / "socket_callbacks.py").read_text(encoding="utf-8")
    heartbeat_dat = agent.create(executeDAT, "heartbeat_execute")  # type: ignore[name-defined]
    heartbeat_dat.text = (source / "heartbeat_execute.py").read_text(encoding="utf-8")

    events = agent.create(tableDAT, "events_table")  # type: ignore[name-defined]
    events.clear()
    for event in (
        "registered",
        "registration_error",
        "request_dispatch",
        "request_execute",
        "outcome_recorded",
        "record_release",
        "daemon_draining",
    ):
        events.appendRow([event])
    auth = agent.create(tableDAT, "auth_table")  # type: ignore[name-defined]
    auth.clear()
    socket_dat = agent.create(socketioDAT, "socketio1")  # type: ignore[name-defined]
    socket_dat.par.active = False
    socket_dat.par.url = "http://127.0.0.1:9982"
    socket_dat.par.delay = 1000
    socket_dat.par.callbacks = callbacks_dat
    socket_dat.par.clamp = True
    socket_dat.par.maxlines = 100
    socket_dat.inputConnectors[1].connect(events)
    socket_dat.inputConnectors[3].connect(auth)
    generated_callbacks = agent.op(f"{socket_dat.name}_callbacks")
    if generated_callbacks and generated_callbacks != callbacks_dat:
        generated_callbacks.destroy()

    builtins._td_cli_building_artifact = True
    try:
        configure_agent_runtime(agent, extension_dat, heartbeat_dat, socket_dat, auth)
        socket_dat.par.active = False
        auth.clear()
        agent.save(str(output))
    finally:
        del builtins._td_cli_building_artifact
    operators = sorted(child.name for child in agent.children)
    evidence = {
        "source_revision": source_revision,
        "touchdesigner_version": touchdesigner_version,
        "operators": operators,
        "artifact_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }
    output.with_suffix(output.suffix + ".manifest.json").write_text(
        json.dumps(evidence, separators=(",", ":"), sort_keys=True), encoding="utf-8"
    )
    return evidence


if "args" in globals():
    build(*args)  # type: ignore[name-defined]
