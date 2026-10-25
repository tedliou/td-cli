"""Locked acceptance for the native td: whole-process ``ops get`` against a real Agent.

Runs ``locked_daemon_harness`` with the frame-timing project and measures the same
``ops.get /project1`` twice: as one Request through the in-process DaemonClient (the
TouchDesigner round trip), and as a whole ``td --json --instance <id> ops get /project1``
process of this checkout's native td against the isolated Daemon. The difference is what the
td process itself costs. ``--source agent`` first builds the Agent from that source.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

from tools.latency_bench import summarize
from tools.locked_daemon_harness import NATIVE_TD, PORT, Harness, run_locked
from tools.locked_frame_timing_acceptance import build_artifact

WARMUP = 5
MEASURED = 40
RUN_TIMEOUT_SECONDS = 60


def _run_native_td(instance_id: str, work: Path) -> tuple[float, subprocess.CompletedProcess[str]]:
    env = {
        **os.environ,
        "LOCALAPPDATA": str(work / "localappdata"),
        "TDCLI_TEST_ENDPOINT": f"127.0.0.1:{PORT}",
    }
    command = [str(NATIVE_TD), "--json", "--instance", instance_id, "ops", "get", "/project1"]
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
    elapsed = round((time.perf_counter() - started) * 1000, 3)
    if completed.returncode != 0:
        raise RuntimeError(f"native td exited {completed.returncode}: {completed.stderr}")
    return elapsed, completed


def _steps(harness: Harness, work: Path) -> dict[str, Any]:
    request_ms = []
    for index in range(WARMUP + MEASURED):
        harness.run(f"ops.get {index}", "ops.get", {"operator_path": "/project1"})
        if index >= WARMUP:
            request_ms.append(harness.requests[-1]["elapsed_ms"])
    process_ms = []
    first: dict[str, Any] | None = None
    for index in range(WARMUP + MEASURED):
        elapsed, completed = _run_native_td(harness.instance_id, work)
        if first is None:
            first = json.loads(completed.stdout)
        if index >= WARMUP:
            process_ms.append(elapsed)
    request = summarize(["ops.get", "/project1"], request_ms)
    process = summarize(["--json", "--instance", "<id>", "ops", "get", "/project1"], process_ms)
    return {
        "native_td": str(NATIVE_TD),
        "warmup": WARMUP,
        "request": request,
        "process": process,
        "process_minus_request_p50_ms": round(
            statistics.median(process_ms) - statistics.median(request_ms), 3
        ),
        "first_envelope": first,
    }


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
    run_locked(
        work=args.work,
        project=args.project,
        artifact=args.artifact,
        evidence_path=args.evidence,
        steps=_steps,
    )
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    print(json.dumps({key: evidence.get(key) for key in ("request", "process")}, indent=2))


if __name__ == "__main__":
    main()
