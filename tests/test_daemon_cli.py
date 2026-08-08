import json

from typer.testing import CliRunner

from td_cli.daemon.cli import app


def test_status_observes_stopped_daemon_without_starting() -> None:
    result = CliRunner().invoke(app, ["status", "--json"])
    assert result.exit_code == 3
    assert json.loads(result.stdout) == {
        "status": "stopped",
        "pid": None,
        "endpoint": None,
        "release_version": None,
        "protocol_versions": [],
        "started_at": None,
    }
