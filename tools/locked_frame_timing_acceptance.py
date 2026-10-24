"""Locked frame-timing probe of the Protocol v3 handshake through the real Daemon transport.

Runs ``locked_daemon_harness`` with a disposable project whose Execute DAT runs
``locked_frame_timing_probe.py`` (frame start enabled). The Daemon side stamps each outbound
``request_dispatch``/``request_execute`` and each received acceptance and outcome on the same
precise system clock as the probe, so every Request splits into measured segments:

client submit -> dispatch emit -> reserve callback -> accepted received -> execute emit ->
authorize callback -> run() execution -> outcome emit -> outcome received -> client terminal.

``--source agent`` first builds the Agent artifact from that source inside a separate disposable
TouchDesigner run; ``--pause`` runs the measurement with the root timeline paused.
"""

from __future__ import annotations

import argparse
import bisect
import ctypes
import json
import os
import statistics
import subprocess
from itertools import pairwise
from pathlib import Path
from typing import Any

import socketio

from td_cli.daemon.lifecycle import RequestLifecycle
from tools.locked_daemon_harness import TOUCHDESIGNER, Harness, run_locked
from tools.runtime_acceptance_common import source_revision

DAEMON_EVENTS: list[dict[str, Any]] = []
WARMUP = 5
MEASURED = 40


def wall_ns() -> int:
    value = ctypes.c_ulonglong()
    ctypes.windll.kernel32.GetSystemTimePreciseAsFileTime(ctypes.byref(value))
    return (value.value - 116444736000000000) * 100


def _instrument() -> None:
    emit = socketio.AsyncServer.emit

    async def stamped_emit(self, event, data=None, *args, **kwargs):
        if event in {"request_dispatch", "request_execute"} and isinstance(data, dict):
            DAEMON_EVENTS.append(
                {"event": event, "request_id": data.get("request_id"), "ns": wall_ns()}
            )
        return await emit(self, event, data, *args, **kwargs)

    socketio.AsyncServer.emit = stamped_emit  # type: ignore[method-assign]
    for name in ("accepted", "outcome"):
        original = getattr(RequestLifecycle, name)

        setattr(RequestLifecycle, name, _stamped(original, name))


def _stamped(original_method, event_name):
    def stamped(self, instance_id, connection_id, request_id, *rest, **kw):
        DAEMON_EVENTS.append({"event": event_name, "request_id": request_id, "ns": wall_ns()})
        return original_method(self, instance_id, connection_id, request_id, *rest, **kw)

    return stamped


def _steps(harness: Harness, work: Path) -> dict[str, Any]:
    del work
    client_rows = []
    for index in range(WARMUP + MEASURED):
        started = wall_ns()
        harness.run(f"ops.get {index}", "ops.get", {"operator_path": "/project1"})
        row = harness.requests[-1]
        client_rows.append(
            {
                "request_id": row["request_id"],
                "warmup": index < WARMUP,
                "submit_ns": started,
                "terminal_ns": wall_ns(),
            }
        )
    return {"client": client_rows, "daemon_events": DAEMON_EVENTS}


def _ms(start: int | None, end: int | None) -> float | None:
    return None if start is None or end is None else round((end - start) / 1e6, 3)


def analyse(evidence: dict[str, Any]) -> dict[str, Any]:
    """Split each measured Request into handshake segments and frame positions."""
    td = evidence.get("td-final", {})
    frames = td.get("frames", [])
    starts = sorted(ns for _, ns in frames)
    td_events: dict[tuple[str, str], dict[str, Any]] = {}
    for event in td.get("events", []):
        td_events.setdefault((event["event"], event["request_id"]), event)
    daemon: dict[tuple[str, str], int] = {}
    for event in evidence.get("daemon_events", []):
        daemon.setdefault((event["event"], event["request_id"]), event["ns"])

    def frame_offset(event: dict[str, Any] | None) -> float | None:
        """Milliseconds since the last frame start, placing the call inside its frame."""
        if event is None:
            return None
        index = bisect.bisect_right(starts, event["start_ns"]) - 1
        return None if index < 0 else _ms(starts[index], event["start_ns"])

    def frames_between(first: dict[str, Any] | None, second: dict[str, Any] | None) -> int | None:
        if first is None or second is None:
            return None
        return bisect.bisect_right(starts, second["start_ns"]) - bisect.bisect_right(
            starts, first["start_ns"]
        )

    rows = []
    for client in evidence.get("client", []):
        if client["warmup"]:
            continue
        rid = client["request_id"]
        reserve = td_events.get(("reserve", rid))
        authorize = td_events.get(("authorize", rid))
        execute = td_events.get(("execute_authorized", rid))
        outcome = td_events.get(("outcome_chunks", rid))
        rows.append(
            {
                "request_id": rid,
                "total_ms": _ms(client["submit_ns"], client["terminal_ns"]),
                "submit_to_dispatch_emit": _ms(
                    client["submit_ns"], daemon.get(("request_dispatch", rid))
                ),
                "dispatch_emit_to_reserve": _ms(
                    daemon.get(("request_dispatch", rid)), reserve and reserve["start_ns"]
                ),
                "reserve_to_accepted_received": _ms(
                    reserve and reserve["end_ns"], daemon.get(("accepted", rid))
                ),
                "accepted_to_execute_emit": _ms(
                    daemon.get(("accepted", rid)), daemon.get(("request_execute", rid))
                ),
                "execute_emit_to_authorize": _ms(
                    daemon.get(("request_execute", rid)), authorize and authorize["start_ns"]
                ),
                "authorize_to_run": _ms(
                    authorize and authorize["end_ns"], execute and execute["start_ns"]
                ),
                "execute_ms": _ms(execute and execute["start_ns"], execute and execute["end_ns"]),
                "outcome_emit_to_received": _ms(
                    outcome and outcome["end_ns"], daemon.get(("outcome", rid))
                ),
                "outcome_received_to_client": _ms(
                    daemon.get(("outcome", rid)), client["terminal_ns"]
                ),
                "reserve_frame_offset_ms": frame_offset(reserve),
                "authorize_frame_offset_ms": frame_offset(authorize),
                "run_frame_offset_ms": frame_offset(execute),
                "frames_reserve_to_authorize": frames_between(reserve, authorize),
                "frames_authorize_to_run": frames_between(authorize, execute),
            }
        )
    keys = [key for key in (rows[0] if rows else {}) if key != "request_id"]
    summary = {}
    for key in keys:
        values = sorted(row[key] for row in rows if row[key] is not None)
        if values:
            summary[key] = {
                "median": statistics.median(values),
                "p95": values[max(0, round(0.95 * len(values)) - 1)],
                "min": values[0],
                "max": values[-1],
            }
    periods = [b - a for a, b in pairwise(starts)]
    return {
        "requests": rows,
        "summary": summary,
        "frame_period_ms_median": round(statistics.median(periods) / 1e6, 3) if periods else None,
        "cook_rate": td.get("cook_rate"),
        "real_time": td.get("real_time"),
        "timeline_paused": td.get("timeline_paused"),
    }


def build_artifact(project: Path, source: Path, work: Path) -> Path:
    """Build the Agent from ``source`` in a disposable TouchDesigner run that then quits."""
    output = (work / "td-agent.tox").resolve()
    output.unlink(missing_ok=True)
    for name in ("build.json", "td-error.json"):
        (work / name).unlink(missing_ok=True)
    env = {
        **os.environ,
        "TDCLI_MENU_LIMIT_WORK": str(work.resolve()),
        "TDCLI_MENU_LIMIT_ARTIFACT": str(output),
        "TDCLI_MENU_LIMIT_URL": "http://127.0.0.1:1",
        "TDCLI_FRAME_TIMING_BUILD": f"{source.resolve()}|{output}|{source_revision(source)}",
    }
    process = subprocess.Popen([str(TOUCHDESIGNER), str(project)], env=env)
    try:
        process.wait(timeout=180)
    except subprocess.TimeoutExpired:
        process.kill()  # the disposable TouchDesigner process started above
        raise
    if not (work / "build.json").exists() or not output.exists():
        error = work / "td-error.json"
        raise RuntimeError(error.read_text("utf-8") if error.exists() else "Agent build failed")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--source", type=Path, help="build the Agent from this source first")
    parser.add_argument("--pause", action="store_true", help="pause the root timeline")
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    if (args.artifact is None) == (args.source is None):
        parser.error("pass exactly one of --artifact or --source")
    args.work.mkdir(parents=True, exist_ok=True)
    if args.source is not None:
        args.artifact = build_artifact(args.project, args.source, args.work)
    if args.pause:
        os.environ["TDCLI_FRAME_TIMING_PAUSE"] = "1"
    _instrument()
    run_locked(
        work=args.work,
        project=args.project,
        artifact=args.artifact,
        evidence_path=args.evidence,
        steps=_steps,
    )
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    evidence["analysis"] = analyse(evidence)
    args.evidence.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in evidence["analysis"].items() if k != "requests"}, indent=2))


if __name__ == "__main__":
    main()
