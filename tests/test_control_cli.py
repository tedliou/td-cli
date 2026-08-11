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


def test_json_output_is_ascii_portable_and_unicode_lossless(monkeypatch) -> None:
    class UnicodeDaemonClient(FakeDaemonClient):
        def wait(self, request_id):
            return {
                "request_id": request_id,
                "status": "succeeded",
                "result": {
                    "operator_path": "/project1/notes",
                    "dat_kind": "text",
                    "text": "繁體 😀",
                    "utf8_bytes": 11,
                },
            }

    monkeypatch.setattr(cli, "DaemonClient", UnicodeDaemonClient)
    result = CliRunner().invoke(cli.app, ["--json", "dat", "text", "get", "/project1/notes"])

    assert result.exit_code == 0, result.output
    assert result.stdout.isascii()
    assert json.loads(result.stdout)["data"]["text"] == "繁體 😀"


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
                "parameters",
                "set",
                "/project1/target",
                "Gain",
                "--bind-source-operator",
                "/project1/source",
                "--bind-parameter",
                "Gain",
            ],
            {
                "name": "parameters.set",
                "input": {
                    "operator_path": "/project1/target",
                    "parameter": "Gain",
                    "mode": "bind",
                    "value": None,
                    "source": {
                        "kind": "bind_parameter",
                        "operator_path": "/project1/source",
                        "channel": None,
                        "parameter": "Gain",
                    },
                },
            },
        ),
        (
            ["--json", "parameters", "sequence-get", "/project1/target", "Items"],
            {
                "name": "parameters.sequence.get",
                "input": {
                    "operator_path": "/project1/target",
                    "sequence": "Items",
                    "max_blocks": 128,
                    "max_parameters": 256,
                },
            },
        ),
        (
            [
                "--json",
                "parameters",
                "sequence-replace",
                "/project1/target",
                "Items",
                "--blocks-json",
                '[{"name":"first","parameters":[{"parameter":"value","mode":"constant","value":1.5}]}]',
            ],
            {
                "name": "parameters.sequence.replace",
                "input": {
                    "operator_path": "/project1/target",
                    "sequence": "Items",
                    "max_blocks": 128,
                    "max_parameters": 256,
                    "blocks": [
                        {
                            "name": "first",
                            "parameters": [
                                {"parameter": "value", "mode": "constant", "value": 1.5}
                            ],
                        }
                    ],
                },
            },
        ),
    ],
)
def test_typed_parameter_cli_commands_reach_submission_seam(monkeypatch, argv, expected) -> None:
    monkeypatch.setattr(cli, "DaemonClient", FakeDaemonClient)
    result = CliRunner().invoke(cli.app, argv)
    assert result.exit_code == 0, result.output
    assert FakeDaemonClient.submitted == expected


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
def test_project_export_batch_and_events_cli_reach_submission_seam(
    monkeypatch, argv, expected
) -> None:
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
        (
            ["--json", "ops", "connections", "/project1/a", "--max-connections", "12"],
            {
                "name": "ops.connections",
                "input": {"operator_path": "/project1/a", "max_connections": 12},
            },
        ),
        (
            ["--json", "ops", "state", "get", "/project1/a"],
            {"name": "ops.state.get", "input": {"operator_path": "/project1/a"}},
        ),
        (
            ["--json", "dat", "text", "get", "/project1/notes", "--max-bytes", "128"],
            {
                "name": "dat.text.get",
                "input": {"operator_path": "/project1/notes", "max_bytes": 128},
            },
        ),
        (
            ["--json", "dat", "text", "set", "/project1/notes", "繁體\n"],
            {
                "name": "dat.text.set",
                "input": {"operator_path": "/project1/notes", "text": "繁體\n"},
            },
        ),
        (
            [
                "--json",
                "dat",
                "table",
                "get",
                "/project1/grid",
                "--row-offset",
                "1",
                "--column-offset",
                "2",
                "--row-count",
                "3",
                "--column-count",
                "4",
                "--max-bytes",
                "1024",
            ],
            {
                "name": "dat.table.get",
                "input": {
                    "operator_path": "/project1/grid",
                    "row_offset": 1,
                    "column_offset": 2,
                    "row_count": 3,
                    "column_count": 4,
                    "max_bytes": 1024,
                },
            },
        ),
        (
            ["--json", "dat", "table", "replace", "/project1/grid", '[["a","b"],["c",""]]'],
            {
                "name": "dat.table.replace",
                "input": {"operator_path": "/project1/grid", "rows": [["a", "b"], ["c", ""]]},
            },
        ),
        (
            [
                "--json",
                "dat",
                "table",
                "patch",
                "/project1/grid",
                '[["x"]]',
                "--row-offset",
                "1",
                "--column-offset",
                "2",
            ],
            {
                "name": "dat.table.patch",
                "input": {
                    "operator_path": "/project1/grid",
                    "row_offset": 1,
                    "column_offset": 2,
                    "rows": [["x"]],
                },
            },
        ),
        (
            [
                "--json",
                "ops",
                "state",
                "set",
                "/project1/a",
                "--node-x",
                "-10",
                "--node-width",
                "140",
                "--color",
                "0.1",
                "0.2",
                "0.3",
                "--comment",
                "source node",
                "--bypass",
                "--no-expose",
            ],
            {
                "name": "ops.state.set",
                "input": {
                    "operator_path": "/project1/a",
                    "node_x": -10,
                    "node_y": None,
                    "node_width": 140,
                    "node_height": None,
                    "color": {"red": 0.1, "green": 0.2, "blue": 0.3},
                    "comment": "source node",
                    "bypass": True,
                    "lock": None,
                    "viewer": None,
                    "expose": False,
                },
            },
        ),
        (
            [
                "--json",
                "ops",
                "destroy",
                "/project1/old",
                "--recursive",
                "--allow-connected",
                "--max-operators",
                "20",
            ],
            {
                "name": "ops.destroy",
                "input": {
                    "operator_path": "/project1/old",
                    "recursive": True,
                    "allow_connected": True,
                    "max_operators": 20,
                },
            },
        ),
        (
            [
                "--json",
                "ops",
                "move",
                "/project1/source",
                "/project1/group",
                "moved",
                "--allow-connected",
                "--max-operators",
                "30",
            ],
            {
                "name": "ops.move",
                "input": {
                    "source_path": "/project1/source",
                    "target_parent_path": "/project1/group",
                    "new_name": "moved",
                    "node_x": None,
                    "node_y": None,
                    "allow_connected": True,
                    "max_operators": 30,
                },
            },
        ),
        (
            [
                "--json",
                "ops",
                "copy",
                "/project1/source",
                "/project1/group",
                "copy",
                "--node-x",
                "-20",
                "--node-y",
                "40",
                "--include-docked",
                "--max-operators",
                "20",
            ],
            {
                "name": "ops.copy",
                "input": {
                    "source_path": "/project1/source",
                    "target_parent_path": "/project1/group",
                    "new_name": "copy",
                    "node_x": -20,
                    "node_y": 40,
                    "include_docked": True,
                    "max_operators": 20,
                },
            },
        ),
    ],
)
def test_operator_parameter_and_dat_cli_commands_reach_submission_seam(
    monkeypatch, argv, expected
) -> None:
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


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "ops", "connections", "/project1/a", "--max-connections", "0"],
        ["--json", "ops", "destroy", "/project1/a", "--max-operators", "0"],
        [
            "--json",
            "ops",
            "copy",
            "/project1/a",
            "/project1",
            "copy",
            "--max-operators",
            "0",
        ],
        [
            "--json",
            "ops",
            "move",
            "/project1/a",
            "/project1",
            "moved",
            "--max-operators",
            "0",
        ],
    ],
)
def test_structural_commands_reject_zero_bounds(monkeypatch, argv) -> None:
    monkeypatch.setattr(cli, "DaemonClient", FakeDaemonClient)
    result = CliRunner().invoke(cli.app, argv)
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "invalid_arguments"


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "ops", "state", "set", "/project1/a"],
        ["--json", "ops", "state", "set", "--node-x", "10"],
        ["--json", "ops", "state", "set", "/project1/a", "--node-width", "0"],
    ],
)
def test_operator_state_cli_rejects_empty_incomplete_and_out_of_bounds_patches(
    monkeypatch, argv
) -> None:
    monkeypatch.setattr(cli, "DaemonClient", FakeDaemonClient)
    result = CliRunner().invoke(cli.app, argv)
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "invalid_arguments"
