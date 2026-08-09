from __future__ import annotations

import json
from importlib.metadata import version
from pathlib import Path

from typer.testing import CliRunner

from td_cli.agent_tool import app as agent_app
from td_cli.cli import app as cli_app
from td_cli.daemon.cli import app as daemon_app


def test_all_executable_interfaces_report_the_release_version() -> None:
    expected = version("touchdesigner-cli")

    for app in (daemon_app, agent_app):
        result = CliRunner().invoke(app, ["--version"])
        assert result.exit_code == 0, result.output
        assert result.stdout.strip() == expected

    result = CliRunner().invoke(cli_app, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == f"td {expected} (protocol 1)"


def test_agent_manifest_uses_the_release_version() -> None:
    expected = version("touchdesigner-cli")
    manifest = json.loads(Path("agent/manifest.json").read_text(encoding="utf-8"))
    assert manifest["agent_version"] == expected
