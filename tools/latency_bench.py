"""Measure the wall time of whole ``td`` processes for before/after latency comparisons.

Run it from PowerShell or cmd, not Git Bash timing, against an installed or ``dist`` td:
``uv run python tools/latency_bench.py --td dist/td/td.exe --runs 20``.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import time

DEFAULT_COMMANDS = ("--version", "--json instances list")
RUN_TIMEOUT_SECONDS = 60


def summarize(command: list[str], samples: list[float]) -> dict[str, object]:
    """Nearest-rank percentiles, so every reported value is one observed run."""
    if not samples:
        raise ValueError("no samples")
    ordered = sorted(samples)

    def rank(percent: int) -> float:
        return ordered[max(0, math.ceil(percent / 100 * len(ordered)) - 1)]

    return {
        "command": command,
        "runs": len(ordered),
        "p50_ms": rank(50),
        "p95_ms": rank(95),
        "min_ms": ordered[0],
        "max_ms": ordered[-1],
    }


def _run(executable: list[str]) -> tuple[float, str]:
    started = time.perf_counter()
    completed = subprocess.run(
        executable, capture_output=True, text=True, timeout=RUN_TIMEOUT_SECONDS, check=False
    )
    elapsed = (time.perf_counter() - started) * 1000
    if completed.returncode != 0:
        raise SystemExit(f"{executable} failed with exit code {completed.returncode}")
    return round(elapsed, 3), completed.stdout.strip()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--td", default="td", help="td executable to measure")
    parser.add_argument(
        "--td-prefix", action="append", default=[], help="argument placed before each command"
    )
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=1, help="unmeasured runs per command")
    parser.add_argument("--command", action="append", help="td arguments as one string; repeatable")
    args = parser.parse_args(argv)
    base = [args.td, *args.td_prefix]
    _, version = _run([*base, "--version"])
    results = []
    for text in args.command or DEFAULT_COMMANDS:
        command = text.split()
        for _ in range(args.warmup):
            _run([*base, *command])
        samples = [_run([*base, *command])[0] for _ in range(args.runs)]
        results.append(summarize(command, samples))
    print(json.dumps({"td": args.td, "td_version": version, "results": results}, indent=2))


if __name__ == "__main__":
    main()
