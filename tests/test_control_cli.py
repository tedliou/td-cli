import json
import sys

import pytest
from typer.testing import CliRunner

from td_cli import cli

INSTANCE = {
    "instance_id": "8cf81688-b9a4-4c39-9f92-31c77319c761",
    "selector": "8cf8",
    "status": "online",
}


class FakeDaemonClient:
    submitted = None

    def __init__(self, **kwargs) -> None:
        del kwargs

    def instances(self):
        return [INSTANCE]

    def select_instance(self, selector, *, online_only=True):
        del selector, online_only
        return INSTANCE

    def submit(self, request_id, instance_id, command):
        self.__class__.submitted = command
        return {"request_id": request_id, "instance_id": instance_id, "status": "queued"}

    def wait(self, request_id):
        return {
            "request_id": request_id,
            "status": "succeeded",
            "result": {
                "path": "/project1",
                "name": "project1",
                "op_type": "base",
                "family": "COMP",
            },
        }


def test_ops_get_submits_typed_command_and_emits_protocol_envelope(monkeypatch) -> None:
    monkeypatch.setattr(cli, "DaemonClient", FakeDaemonClient)

    result = CliRunner().invoke(cli.app, ["--json", "ops", "get", "/project1"])

    assert result.exit_code == 0, result.output
    assert FakeDaemonClient.submitted == {
        "name": "ops.get",
        "input": {"operator_path": "/project1"},
    }
    assert json.loads(result.stdout) == {
        "protocol_version": 1,
        "data": {"path": "/project1", "name": "project1", "op_type": "base", "family": "COMP"},
        "request": {
            "request_id": json.loads(result.stdout)["request"]["request_id"],
            "status": "succeeded",
        },
    }


def test_command_rejects_mixed_input_modes_as_json(monkeypatch) -> None:
    monkeypatch.setattr(cli, "DaemonClient", FakeDaemonClient)

    result = CliRunner().invoke(
        cli.app,
        ["--json", "ops", "get", "/project1", "--input", '{"operator_path":"/project1"}'],
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "invalid_arguments"


def test_parameters_set_bool_consumes_an_explicit_boolean_value(monkeypatch) -> None:
    monkeypatch.setattr(cli, "DaemonClient", FakeDaemonClient)

    result = CliRunner().invoke(
        cli.app,
        ["--json", "parameters", "set", "/project1", "display", "--bool", "false"],
    )

    assert result.exit_code == 0, result.output
    assert FakeDaemonClient.submitted == {
        "name": "parameters.set",
        "input": {
            "operator_path": "/project1",
            "parameter": "display",
            "mode": "constant",
            "value": False,
        },
    }


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (
            [
                "--json",
                "ops",
                "create",
                "/project1",
                "constantTOP",
                "source",
                "--node-x",
                "-100",
                "--node-y",
                "25",
            ],
            {
                "name": "ops.create",
                "input": {
                    "parent_path": "/project1",
                    "op_type": "constantTOP",
                    "name": "source",
                    "node_x": -100,
                    "node_y": 25,
                    "allow_conditional": False,
                },
            },
        ),
        (
            ["--json", "ops", "connect", "/project1/source", "/project1/output"],
            {
                "name": "ops.connect",
                "input": {
                    "source_path": "/project1/source",
                    "target_path": "/project1/output",
                    "output_index": 0,
                    "input_index": 0,
                    "replace": False,
                },
            },
        ),
        (
            ["--json", "ops", "children", "/project1", "--op-type", "base"],
            {"name": "ops.children", "input": {"operator_path": "/project1", "op_type": "base"}},
        ),
        (
            ["--json", "parameters", "get", "/project1", "display"],
            {
                "name": "parameters.get",
                "input": {"operator_path": "/project1", "parameter": "display"},
            },
        ),
        (
            ["--json", "parameters", "pulse", "/project1", "reset"],
            {
                "name": "parameters.pulse",
                "input": {"operator_path": "/project1", "parameter": "reset"},
            },
        ),
        (
            [
                "--json",
                "parameters",
                "set",
                "--input",
                '{"operator_path":"/project1","parameter":"display","mode":"expression","value":"True"}',
            ],
            {
                "name": "parameters.set",
                "input": {
                    "operator_path": "/project1",
                    "parameter": "display",
                    "mode": "expression",
                    "value": "True",
                },
            },
        ),
    ],
)
def test_every_typed_cli_command_reaches_the_public_submission_seam(
    monkeypatch, argv: list[str], expected: dict[str, object]
) -> None:
    monkeypatch.setattr(cli, "DaemonClient", FakeDaemonClient)

    result = CliRunner().invoke(cli.app, argv)

    assert result.exit_code == 0, result.output
    assert FakeDaemonClient.submitted == expected


def test_recognizable_json_parser_failure_uses_protocol_envelope(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["td", "ops", "--json", "get", "/project1"])

    with pytest.raises(SystemExit) as stopped:
        cli.run()

    captured = capsys.readouterr()
    assert stopped.value.code == 2
    assert json.loads(captured.out)["error"]["code"] == "invalid_arguments"
    assert "No such option: --json" in captured.err


def test_instance_option_is_rejected_for_queries(monkeypatch) -> None:
    monkeypatch.setattr(cli, "DaemonClient", FakeDaemonClient)

    result = CliRunner().invoke(cli.app, ["--json", "--instance", "8cf8", "instances", "list"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "invalid_arguments"


def test_create_conditional_operator_requires_explicit_cli_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(cli, "DaemonClient", FakeDaemonClient)

    result = CliRunner().invoke(
        cli.app,
        [
            "--json",
            "ops",
            "create",
            "/project1",
            "videodeviceinTOP",
            "camera",
            "--allow-conditional",
        ],
    )

    assert result.exit_code == 0, result.output
    assert FakeDaemonClient.submitted == {
        "name": "ops.create",
        "input": {
            "parent_path": "/project1",
            "op_type": "videodeviceinTOP",
            "name": "camera",
            "node_x": 0,
            "node_y": 0,
            "allow_conditional": True,
        },
    }


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--json", "project", "metadata"], {"name": "project.metadata", "input": {}}),
        (
            ["--json", "project", "snapshot", "/project1"],
            {
                "name": "project.snapshot",
                "input": {"operator_path": "/project1", "max_depth": 4, "max_operators": 256},
            },
        ),
        (
            ["--json", "binary", "export", "/project1", "--format", "tox"],
            {
                "name": "binary.export",
                "input": {"operator_path": "/project1", "format": "tox", "max_bytes": 194560},
            },
        ),
        (
            [
                "--json",
                "batch",
                "execute",
                "--input",
                '{"commands":[{"name":"ops.get","input":{"operator_path":"/project1"}}]}',
            ],
            {
                "name": "batch.execute",
                "input": {
                    "commands": [{"name": "ops.get", "input": {"operator_path": "/project1"}}]
                },
            },
        ),
        (
            ["--json", "events", "read", "--after", "7", "--limit", "10"],
            {
                "name": "events.read",
                "input": {"after": 7, "limit": 10, "include_errors": True},
            },
        ),
    ],
)
def test_phase_3_commands_reach_public_submission_seam(monkeypatch, argv, expected) -> None:
    monkeypatch.setattr(cli, "DaemonClient", FakeDaemonClient)

    result = CliRunner().invoke(cli.app, argv)

    assert result.exit_code == 0, result.output
    assert FakeDaemonClient.submitted == expected


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (
            ["--json", "ops", "rename", "/project1/old", "new_name"],
            {
                "name": "ops.rename",
                "input": {"operator_path": "/project1/old", "new_name": "new_name"},
            },
        ),
        (
            ["--json", "ops", "disconnect", "/project1/a", "/project1/b", "--input-index", "2"],
            {
                "name": "ops.disconnect",
                "input": {
                    "source_path": "/project1/a",
                    "target_path": "/project1/b",
                    "output_index": 0,
                    "input_index": 2,
                },
            },
        ),
        (
            ["--json", "ops", "connect", "/project1/a", "/project1/b", "--replace"],
            {
                "name": "ops.connect",
                "input": {
                    "source_path": "/project1/a",
                    "target_path": "/project1/b",
                    "output_index": 0,
                    "input_index": 0,
                    "replace": True,
                },
            },
        ),
        (
            ["--json", "parameters", "list", "/project1/a"],
            {"name": "parameters.list", "input": {"operator_path": "/project1/a"}},
        ),
    ],
)
def test_v011_dedicated_cli_commands_reach_submission_seam(monkeypatch, argv, expected) -> None:
    monkeypatch.setattr(cli, "DaemonClient", FakeDaemonClient)
    result = CliRunner().invoke(cli.app, argv)
    assert result.exit_code == 0, result.output
    assert FakeDaemonClient.submitted == expected


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "ops", "rename", "/project1/old"],
        ["--json", "ops", "disconnect", "/project1/a", "--input-index", "1"],
        ["--json", "ops", "connect", "--replace"],
    ],
)
def test_v011_cli_rejects_incomplete_dedicated_modes(monkeypatch, argv) -> None:
    monkeypatch.setattr(cli, "DaemonClient", FakeDaemonClient)
    result = CliRunner().invoke(cli.app, argv)
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "invalid_arguments"
