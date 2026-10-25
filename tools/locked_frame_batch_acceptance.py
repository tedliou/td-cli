"""Locked acceptance for Frame Batches: 512 Commands with and without ``--frame-batch``.

Runs ``locked_daemon_harness`` with the frame-timing project and probe, which stamp every
``execute_authorized`` call and every frame start on one precise clock. The native td of this
checkout runs the same two plans twice through the isolated Daemon: 256 ``ops.create`` of a
Constant CHOP, then 256 ``parameters.set`` of their first value (a plan holds at most 256
Commands), once as one Request per Command and once with ``--frame-batch``. Each run writes
into its own fresh Base COMP, and the harness reads both graphs back. The Daemon side stamps
each dispatch and outcome on the same clock, as in ``locked_frame_timing_acceptance``.
``--source agent`` first builds the Agent from that source.
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import statistics
import subprocess
import time
from itertools import pairwise
from pathlib import Path
from typing import Any

from tools.locked_daemon_harness import NATIVE_TD, PORT, Harness, run_locked
from tools.locked_frame_timing_acceptance import DAEMON_EVENTS, _instrument, build_artifact

OPERATORS = 256
RUN_TIMEOUT_SECONDS = 120
SAMPLES = (0, 127, 255)
RUNS = {"one_by_one": [], "frame_batch": ["--frame-batch"]}


def plans(parent: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The 256 creates and the 256 parameter sets: a plan holds at most 256 Commands."""
    creates, sets = [], []
    for index in range(OPERATORS):
        creates.append(
            {
                "name": "ops.create",
                "input": {
                    "parent_path": parent,
                    "op_type": "constantCHOP",
                    "name": f"c{index:03d}",
                    "node_x": (index % 16) * 150,
                    "node_y": -(index // 16) * 100,
                },
            }
        )
        sets.append(
            {
                "name": "parameters.set",
                "input": {
                    "operator_path": f"{parent}/c{index:03d}",
                    "parameter": "const0value",
                    "mode": "constant",
                    "value": float(index),
                },
            }
        )
    return creates, sets


def _execute(instance_id: str, work: Path, plan_path: Path, flags: list[str]) -> dict[str, Any]:
    env = {
        **os.environ,
        "LOCALAPPDATA": str(work / "localappdata"),
        "TDCLI_TEST_ENDPOINT": f"127.0.0.1:{PORT}",
    }
    command = [
        str(NATIVE_TD),
        "--json",
        "--timeout",
        str(RUN_TIMEOUT_SECONDS - 10),
        "--instance",
        instance_id,
        "commands",
        "execute",
        *flags,
        "--input-file",
        str(plan_path),
    ]
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=RUN_TIMEOUT_SECONDS,
        check=False,
    )
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    events = [json.loads(line) for line in completed.stdout.splitlines()]
    if completed.returncode != 0 or events[-1].get("event") != "finished":
        raise RuntimeError(f"native td exited {completed.returncode}: {completed.stdout[-2000:]}")
    return {"exit_code": completed.returncode, "elapsed_ms": elapsed, "events": events}


def _read_back(harness: Harness, parent: str) -> dict[str, Any]:
    children = harness.run(f"children {parent}", "ops.children", {"operator_path": parent})
    values = {
        index: harness.run(
            f"value {parent} {index}",
            "parameters.get",
            {"operator_path": f"{parent}/c{index:03d}", "parameter": "const0value"},
        )["value"]
        for index in SAMPLES
    }
    rows = children if isinstance(children, list) else children.get("children", [])
    return {
        "children": len(rows),
        "constant_chops": sum(1 for row in rows if row.get("op_type") == "constantCHOP"),
        "sampled_values": values,
    }


def _steps(harness: Harness, work: Path) -> dict[str, Any]:
    runs = {}
    for label, flags in RUNS.items():
        parent = f"/project1/{label}"
        harness.run(
            f"create {parent}",
            "ops.create",
            {"parent_path": "/project1", "op_type": "baseCOMP", "name": label},
        )
        parts = []
        for part, commands in zip(("creates", "sets"), plans(parent), strict=True):
            plan_path = work / f"{label}-{part}.json"
            plan_path.write_text(json.dumps({"commands": commands}), encoding="utf-8")
            parts.append({"plan": part, **_execute(harness.instance_id, work, plan_path, flags)})
        runs[label] = {
            "flags": flags,
            "elapsed_ms": round(sum(part["elapsed_ms"] for part in parts), 1),
            "plan_elapsed_ms": {part["plan"]: part["elapsed_ms"] for part in parts},
            "events": [event for part in parts for event in part["events"]],
            "read_back": _read_back(harness, parent),
        }
    return {"native_td": str(NATIVE_TD), "commands": 2 * OPERATORS, "runs": runs}


def _ms(start: int, end: int) -> float:
    return round((end - start) / 1e6, 3)


def _spread(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "median": statistics.median(ordered),
        "p95": ordered[max(0, round(0.95 * len(ordered)) - 1)],
        "max": ordered[-1],
    }


def _round_trips(
    request_ids: list[str],
    td_events: dict[tuple[str, str], dict[str, Any]],
    daemon: dict[tuple[str, str], int],
    starts: list[int],
) -> dict[str, Any]:
    """Where the time between one outcome and the next dispatch goes, on one shared clock."""

    def offset(ns: int) -> float | None:
        index = bisect.bisect_right(starts, ns) - 1
        return None if index < 0 else _ms(starts[index], ns)

    segments: dict[str, list[float]] = {
        "outcome_offset_in_frame": [],
        "outcome_emit_to_daemon": [],
        "daemon_outcome_to_next_dispatch": [],
        "dispatch_emit_to_callback": [],
        "dispatch_offset_in_frame": [],
    }
    for current, following in pairwise(request_ids):
        outcome = td_events.get(("outcome_chunks", current))
        dispatch = td_events.get(("dispatch", following))
        received = daemon.get(("outcome", current))
        emitted = daemon.get(("request_dispatch", following))
        if None in (outcome, dispatch, received, emitted):
            continue
        values = {
            "outcome_offset_in_frame": offset(outcome["end_ns"]),
            "outcome_emit_to_daemon": _ms(outcome["end_ns"], received),
            "daemon_outcome_to_next_dispatch": _ms(received, emitted),
            "dispatch_emit_to_callback": _ms(emitted, dispatch["start_ns"]),
            "dispatch_offset_in_frame": offset(dispatch["start_ns"]),
        }
        for key, value in values.items():
            if value is not None:
                segments[key].append(value)
    return {key: _spread(values) for key, values in segments.items()}


def analyse(evidence: dict[str, Any]) -> dict[str, Any]:
    """Execution time of each Request and the frame intervals while each run was going on."""
    td = evidence.get("td-final", {})
    starts = sorted(ns for _, ns in td.get("frames", []))
    executions = {
        event["request_id"]: event
        for event in td.get("events", [])
        if event["event"] == "execute_authorized"
    }
    td_events: dict[tuple[str, str], dict[str, Any]] = {}
    for event in td.get("events", []):
        td_events.setdefault((event["event"], event["request_id"]), event)
    daemon: dict[tuple[str, str], int] = {}
    for event in evidence.get("daemon_events", []):
        daemon.setdefault((event["event"], event["request_id"]), event["ns"])
    analysis: dict[str, Any] = {}
    for label, run in evidence.get("runs", {}).items():
        submitted = [event for event in run["events"] if event["event"] == "submitting"]
        completed: dict[str, int] = {}
        for event in run["events"]:
            if event["event"] == "completed":
                request_id = event.get("request_id") or event["request"]["request_id"]
                completed[request_id] = completed.get(request_id, 0) + 1
        requests = []
        for event in submitted:
            execution = executions.get(event["request_id"])
            requests.append(
                {
                    "index": event["index"],
                    "count": event.get("count", 1),
                    "executed": completed.get(event["request_id"], 0),
                    "execute_ms": execution and _ms(execution["start_ns"], execution["end_ns"]),
                }
            )
        timed = [executions[e["request_id"]] for e in submitted if e["request_id"] in executions]
        window = [
            ns
            for ns in starts
            if timed and timed[0]["start_ns"] <= ns <= timed[-1]["end_ns"] + 50_000_000
        ]
        analysis[label] = {
            "elapsed_ms": run["elapsed_ms"],
            "requests": len(requests),
            "frames": len(window),
            "execute_ms": _spread([row["execute_ms"] for row in requests if row["execute_ms"]]),
            "frame_interval_ms": _spread([_ms(a, b) for a, b in pairwise(window)]),
            "round_trip_ms": _round_trips(
                [event["request_id"] for event in submitted], td_events, daemon, starts
            ),
            "read_back": run["read_back"],
            "per_request": requests,
        }
    single = analysis.get("one_by_one", {}).get("execute_ms") or {}
    batched = analysis.get("frame_batch", {})
    if single and batched:
        limit = 8.0 + single["max"]
        over = [row for row in batched["per_request"] if (row["execute_ms"] or 0.0) > limit]
        analysis["frame_batch_limit_ms"] = round(limit, 3)
        analysis["frame_batch_over_limit"] = over
        analysis["speedup"] = round(analysis["one_by_one"]["elapsed_ms"] / batched["elapsed_ms"], 2)
    analysis["frame_period_ms_median"] = (
        round(statistics.median(b - a for a, b in pairwise(starts)) / 1e6, 3)
        if len(starts) > 1
        else None
    )
    return analysis


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--source", type=Path, help="build the Agent from this source first")
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    if (args.artifact is None) == (args.source is None):
        parser.error("pass exactly one of --artifact or --source")
    args.work.mkdir(parents=True, exist_ok=True)
    if args.source is not None:
        args.artifact = build_artifact(args.project, args.source, args.work)
    _instrument()
    run_locked(
        work=args.work,
        project=args.project,
        artifact=args.artifact,
        evidence_path=args.evidence,
        steps=_steps,
    )
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    evidence["daemon_events"] = DAEMON_EVENTS
    evidence["analysis"] = analyse(evidence)
    for run in evidence.get("runs", {}).values():
        run["events"] = run["events"][-1:]  # the per-Request rows in the analysis replace them
    args.evidence.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    summary = {
        label: {key: value for key, value in row.items() if key != "per_request"}
        if isinstance(row, dict)
        else row
        for label, row in evidence["analysis"].items()
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
