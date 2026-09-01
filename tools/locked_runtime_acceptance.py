"""One-shot acceptance probe for the locked TouchDesigner runtime.

This file is executed by an Execute DAT in a disposable project. It deliberately
uses TouchDesigner's main thread and official ``run(..., delayRef=op.TDResources)``
scheduler; it is not imported by the shipped Agent Component.
"""

# ruff: noqa: F821 - TouchDesigner injects its Python API names at runtime.

import json
import statistics
import threading
import time
import traceback
from pathlib import Path

REPOSITORY = Path(r"E:\td-cli")
SOURCE = REPOSITORY / "agent"
ARTIFACT = REPOSITORY / "td-agent.tox"
ACCEPTANCE_TOX = REPOSITORY / ".tmp-runtime-acceptance-source.tox"
RESULT = REPOSITORY / ".tmp-locked-runtime-acceptance.json"
AGENT_STATE = REPOSITORY / ".tmp-locked-runtime-agent-state.json"
REINIT_RESULT = REPOSITORY / ".tmp-locked-runtime-reinit.json"
SAVED_PROJECT = Path(r"C:\Users\Ted\AppData\Local\Temp\td-cli-runtime-acceptance\saved-online.toe")
LOCKED_BUILD = "2025.32050"
source_revision = __import__("runpy").run_path(
    str(REPOSITORY / "tools" / "runtime_acceptance_common.py")
)["source_revision"]


def _measure(agent, command, repetitions=11):
    samples = []
    frames = []
    for _ in range(repetitions):
        before_frame = int(absTime.frame)  # type: ignore[name-defined]
        started = time.perf_counter()
        agent.execute_command(command)
        samples.append((time.perf_counter() - started) * 1000)
        frames.append(int(absTime.frame) - before_frame)  # type: ignore[name-defined]
    ordered = sorted(samples)
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return {
        "cold_ms": round(samples[0], 3),
        "warm_median_ms": round(statistics.median(samples[1:]), 3),
        "p95_ms": round(ordered[p95_index], 3),
        "max_ms": round(max(samples), 3),
        "frame_delta_max": max(frames),
        "samples": len(samples),
    }


def _build_graph():
    project = op("/project1")  # type: ignore[name-defined]
    for name in ("runtime_acceptance", "runtime_acceptance_perform"):
        old = project.op(name)
        if old is not None:
            old.destroy()
    holder = project.create(baseCOMP, "runtime_acceptance")  # type: ignore[name-defined]
    for index in range(999):
        holder.create(nullDAT, f"n{index:03d}")  # type: ignore[name-defined]
    holder.save(str(ACCEPTANCE_TOX))
    table = project.create(tableDAT, "runtime_acceptance_table")  # type: ignore[name-defined]
    performance = project.create(performCHOP, "runtime_acceptance_perform")  # type: ignore[name-defined]
    return holder, table, performance


def _run_probe(scheduled_at, scheduled_frame):
    try:
        _run_probe_inner(scheduled_at, scheduled_frame)
    except Exception as error:  # noqa: BLE001 - preserve exact locked-runtime failure evidence
        RESULT.write_text(
            json.dumps(
                {
                    "error": type(error).__name__,
                    "message": str(error),
                    "traceback": traceback.format_exc(),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )


def _run_probe_inner(scheduled_at, scheduled_frame):
    evidence = {
        "touchdesigner_build": str(app.build),  # type: ignore[name-defined]
        "main_thread": threading.current_thread() is threading.main_thread(),
        "timeline_paused": not bool(root.time.play),  # type: ignore[name-defined]
        "independent_scheduler": {
            "elapsed_ms": round((time.perf_counter() - scheduled_at) * 1000, 3),
            "frame_delta": int(absTime.frame) - scheduled_frame,  # type: ignore[name-defined]
        },
    }
    if evidence["touchdesigner_build"] != LOCKED_BUILD:
        raise RuntimeError(f"locked TouchDesigner {LOCKED_BUILD} required")

    build_module = __import__("runpy").run_path(str(SOURCE / "build_td.py"), init_globals=globals())
    build_started = time.perf_counter()
    artifact = build_module["build"](str(SOURCE), str(ARTIFACT), source_revision(SOURCE))
    evidence["artifact"] = artifact
    evidence["artifact"]["build_ms"] = round((time.perf_counter() - build_started) * 1000, 3)

    built_comp = op("/project1/td_agent")  # type: ignore[name-defined]
    built_comp.destroy()
    agent_comp = op("/project1").loadByteArray(  # type: ignore[name-defined]
        bytearray(ARTIFACT.read_bytes()), unwired=True, pattern=None
    )
    if agent_comp is None or str(agent_comp.path) != "/project1/td_agent":
        raise RuntimeError("locked artifact did not load as /project1/td_agent")
    agent = agent_comp.ext.Agent
    holder, table, performance = _build_graph()
    rows = [["abcdefgh" for _ in range(64)] for _ in range(64)]
    cases = {
        "fast_read": {"name": "ops.get", "input": {"operator_path": "/project1"}},
        "bounded_scan_or_export": {
            "name": "project.snapshot",
            "input": {
                "operator_path": str(holder.path),
                "max_depth": 1,
                "max_operators": 1000,
            },
        },
        "bounded_mutation": {
            "name": "dat.table.replace",
            "input": {"operator_path": str(table.path), "rows": rows},
        },
        "trusted_asset_mutation": {
            "name": "ops.tox.import",
            "input": {
                "parent_path": str(holder.path),
                "tox_path": str(ACCEPTANCE_TOX),
                "allowlist_root": str(REPOSITORY),
                "target_name": "runtime_acceptance_import",
                "trusted": True,
                "replace": True,
                "max_file_bytes": 67_108_864,
                "max_operators": 1000,
            },
        },
    }
    frame_budget_ms = 1000 / 60
    evidence["frame_budget_ms_at_60_fps"] = round(frame_budget_ms, 3)
    evidence["execution_classes"] = {}
    for name, command in cases.items():
        performance.cook(force=True)
        fps_before = float(performance["fps"].eval())
        measurement = _measure(
            agent,
            command,
            repetitions=3 if name == "trusted_asset_mutation" else 11,
        )
        performance.cook(force=True)
        measurement["perform_fps_before"] = fps_before
        measurement["perform_fps_after"] = float(performance["fps"].eval())
        measurement["dropped_frames_available"] = False
        measurement["max_frame_budget_occupancy"] = round(
            measurement["max_ms"] / frame_budget_ms,
            3,
        )
        evidence["execution_classes"][name] = measurement

    agent.connection_id = agent.connection_id or "acceptance-connection"
    request_id = "00000000-0000-7000-8000-000000000090"
    execution_id = "00000000-0000-7000-8000-000000000091"
    command = {
        "name": "ops.create",
        "input": {
            "parent_path": str(holder.path),
            "op_type": "nullDAT",
            "name": "mutation_once",
            "node_x": 0,
            "node_y": 0,
            "allow_conditional": False,
        },
    }
    event, _ = agent.reserve(
        {
            "request_id": request_id,
            "instance_id": agent.instance_id,
            "connection_id": agent.connection_id,
            "command": command,
        }
    )
    authorized = agent.authorize(
        {
            "request_id": request_id,
            "instance_id": agent.instance_id,
            "connection_id": agent.connection_id,
            "execution_id": execution_id,
        }
    )
    first = agent.execute_authorized(request_id, execution_id)
    second = agent.execute_authorized(request_id, execution_id)
    evidence["single_execution"] = {
        "reserved_event": event,
        "authorized": authorized,
        "first_status": None if first is None else first["status"],
        "second_is_noop": second is None,
        "created_count": len([child for child in holder.children if child.name == "mutation_once"]),
    }
    agent.acknowledge_outcome(request_id, execution_id)
    evidence["power_off"] = "unsupported"
    evidence["completed_at_unix"] = time.time()
    RESULT.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    _schedule_agent_state_probe()
    run(  # type: ignore[name-defined]
        _reinit_cycle,
        10,
        delayMilliSeconds=1000,
        delayRef=op.TDResources,  # type: ignore[name-defined]
    )


def _schedule_agent_state_probe():
    run(  # type: ignore[name-defined]
        _write_agent_state,
        delayMilliSeconds=1000,
        delayRef=op.TDResources,  # type: ignore[name-defined]
    )


def _write_agent_state():
    agent = op("/project1/td_agent").ext.Agent  # type: ignore[name-defined]
    records = [
        {
            "connection_id": record["connection_id"],
            "execution_id": record["execution_id"],
            "outcome_status": None if record["outcome"] is None else record["outcome"]["status"],
            "outcome_bytes": record["outcome_bytes"],
            "phase": record["phase"],
            "request_id": request_id,
        }
        for request_id, record in sorted(agent.execution_records.items())
    ]
    AGENT_STATE.write_text(
        json.dumps(
            {
                "connection_id": agent.connection_id,
                "heartbeat_emissions": agent._state["heartbeat_emissions"],
                "records": records,
                "runtime_active": agent.runtime_active,
                "updated_at_unix": time.time(),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    _schedule_agent_state_probe()


def _reinit_cycle(remaining):
    op("/project1/td_agent").par.reinitextensions.pulse()  # type: ignore[name-defined]
    if remaining > 1:
        run(  # type: ignore[name-defined]
            _reinit_cycle,
            remaining - 1,
            delayMilliSeconds=500,
            delayRef=op.TDResources,  # type: ignore[name-defined]
        )
        return
    run(  # type: ignore[name-defined]
        _begin_heartbeat_window,
        delayMilliSeconds=2500,
        delayRef=op.TDResources,  # type: ignore[name-defined]
    )


def _begin_heartbeat_window():
    agent = op("/project1/td_agent").ext.Agent  # type: ignore[name-defined]
    run(  # type: ignore[name-defined]
        _finish_reinit_probe,
        agent._state["heartbeat_emissions"],
        delayMilliSeconds=6500,
        delayRef=op.TDResources,  # type: ignore[name-defined]
    )


def _finish_reinit_probe(initial_emissions):
    agent_comp = op("/project1/td_agent")  # type: ignore[name-defined]
    agent = agent_comp.ext.Agent
    errors = [
        {"path": str(item.path), "errors": list(item.errors())}
        for item in [agent_comp, *agent_comp.findChildren()]
        if item.errors()
    ]
    saved = bool(project.save(str(SAVED_PROJECT)))  # type: ignore[name-defined]
    REINIT_RESULT.write_text(
        json.dumps(
            {
                "auth_rows": int(agent_comp.op("auth_table").numRows),
                "connection_id": agent.connection_id,
                "cycles": 10,
                "heartbeat_emissions_in_6_5_seconds": agent._state["heartbeat_emissions"]
                - initial_emissions,
                "operator_errors": errors,
                "runtime_active": agent.runtime_active,
                "saved_online_project": saved,
                "saved_project_path": str(SAVED_PROJECT),
                "socket_active": bool(agent_comp.op("socketio1").par.active),
                "timeline_paused": not bool(root.time.play),  # type: ignore[name-defined]
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def start():
    if RESULT.exists():
        RESULT.unlink()
    if AGENT_STATE.exists():
        AGENT_STATE.unlink()
    if REINIT_RESULT.exists():
        REINIT_RESULT.unlink()
    if SAVED_PROJECT.exists():
        SAVED_PROJECT.unlink()
    root.time.play = False  # type: ignore[name-defined]
    scheduled_at = time.perf_counter()
    scheduled_frame = int(absTime.frame)  # type: ignore[name-defined]
    run(  # type: ignore[name-defined]
        "args[0](args[1], args[2])",
        _run_probe,
        scheduled_at,
        scheduled_frame,
        delayMilliSeconds=500,
        delayRef=op.TDResources,  # type: ignore[name-defined]
    )
