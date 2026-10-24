from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

latency_bench = SimpleNamespace(**runpy.run_path("tools/latency_bench.py"))


def test_summary_reports_nearest_rank_percentiles() -> None:
    samples = [float(value) for value in range(1, 21)]
    assert latency_bench.summarize(["td", "--version"], samples) == {
        "command": ["td", "--version"],
        "runs": 20,
        "p50_ms": 10.0,
        "p95_ms": 19.0,
        "min_ms": 1.0,
        "max_ms": 20.0,
    }


def test_summary_rejects_an_empty_run() -> None:
    with pytest.raises(ValueError):
        latency_bench.summarize(["td"], [])


def test_bench_runs_each_command_and_reports_the_td_version(tmp_path: Path, capsys) -> None:
    fake = tmp_path / "fake_td.py"
    fake.write_text("import sys; print('td 9.9.9' if '--version' in sys.argv else '{}')\n")
    latency_bench.main(
        [
            "--td",
            sys.executable,
            "--td-prefix",
            str(fake),
            "--runs",
            "3",
            "--command",
            "--json instances list",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert report["td_version"] == "td 9.9.9"
    [result] = report["results"]
    assert result["command"] == ["--json", "instances", "list"]
    assert result["runs"] == 3
    assert 0 < result["min_ms"] <= result["p50_ms"] <= result["p95_ms"] <= result["max_ms"]


def test_bench_stops_on_a_failing_command(tmp_path: Path) -> None:
    fake = tmp_path / "fake_td.py"
    fake.write_text("import sys; sys.exit(0 if '--version' in sys.argv else 3)\n")
    with pytest.raises(SystemExit, match="exit code 3"):
        latency_bench.main(
            ["--td", sys.executable, "--td-prefix", str(fake), "--command", "instances list"]
        )
