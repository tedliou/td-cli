import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from td_cli.daemon import cli


def test_status_reports_authenticated_unhealthy_daemon(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "daemon.json").write_text(
        json.dumps({"pid": 42, "release_version": "test", "protocol_versions": [3]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli,
        "_probe",
        lambda _: {"ready": False, "release_version": "test", "protocol_versions": [3]},
    )

    assert cli._status_payload(tmp_path)["status"] == "starting/unhealthy"


def test_status_on_fresh_layout_does_not_create_auth_token(tmp_path: Path) -> None:
    assert cli._status_payload(tmp_path)["status"] == "stopped"
    assert not (tmp_path / "state" / "auth.token").exists()


@pytest.mark.parametrize(
    ("frozen", "expected"),
    [
        (True, [r"C:\Programs\td-daemon.exe", "serve"]),
        (
            False,
            [r"C:\Programs\td-daemon.exe", "-m", "td_cli.daemon.cli", "serve"],
        ),
    ],
)
def test_start_spawns_the_public_serve_command_for_each_runtime(
    tmp_path: Path, monkeypatch, frozen: bool, expected: list[str]
) -> None:
    spawned = []
    probes = iter([None, {"ready": True, "protocol_versions": [3]}])
    monkeypatch.setattr(cli, "data_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "secure_layout", lambda _: None)
    monkeypatch.setattr(cli, "_probe", lambda _, **kw: next(probes))
    monkeypatch.setattr(
        cli,
        "launch_detached",
        lambda argv, **options: spawned.append((argv, options)),
    )
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Programs\td-daemon.exe")

    result = CliRunner().invoke(cli.app, ["start"])

    assert result.exit_code == 0, result.output
    assert len(spawned) == 1
    command, options = spawned[0]
    assert command == expected
    assert options["hidden"] is True
    assert result.output == ""


def test_dead_pid_metadata_is_stopped(tmp_path, monkeypatch):
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "daemon.json").write_text('{"pid": 23276}')
    monkeypatch.setattr(cli, "_probe", lambda _: None)
    monkeypatch.setattr(cli, "_pid_alive", lambda _: False)
    assert cli._status_payload(tmp_path)["status"] == "stopped"


def test_start_uses_the_explicit_total_startup_budget(monkeypatch):
    observed = []
    monkeypatch.setattr(cli, "ensure_running", lambda **kw: observed.append(kw["timeout"]))
    result = CliRunner().invoke(cli.app, ["start", "--timeout", "45"])
    assert result.exit_code == 0, result.output
    assert observed == [45]


@pytest.mark.parametrize("late_probe", [1, 2])
def test_startup_rejects_ready_after_total_deadline(monkeypatch, tmp_path, late_probe):
    clock = [0.0]
    observed = []
    monkeypatch.setattr(cli, "data_root", lambda: tmp_path)
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cli, "launch_detached", lambda *a, **kw: None)

    def probe(root, *, timeout):
        observed.append(timeout)
        if len(observed) == late_probe:
            clock[0] = 0.11
            return {"ready": True, "protocol_versions": [3]}
        return None

    monkeypatch.setattr(cli, "_probe", probe)
    with pytest.raises(cli.LaunchError, match="deadline expired|timed out"):
        cli.ensure_running(timeout=0.1)
    assert observed == [0.1] * late_probe
