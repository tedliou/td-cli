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
