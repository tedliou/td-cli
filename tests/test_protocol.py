import json

import pytest
from pydantic import ValidationError

from td_cli.command_catalog import COMMAND_CATALOG
from td_cli.protocol import Command, OperatorInput, RequestSnapshot, RequestStatus


def test_command_catalog_is_the_single_command_contract() -> None:
    assert len(COMMAND_CATALOG.names) == len(set(COMMAND_CATALOG.names))
    assert set(COMMAND_CATALOG.batch_names) < set(COMMAND_CATALOG.names)
    assert COMMAND_CATALOG.validate_input("ops.get", {"operator_path": "/project1"}) == {
        "operator_path": "/project1"
    }
    with pytest.raises(ValueError, match="unsupported Command"):
        COMMAND_CATALOG.validate_input("future.command", {"operator_path": "/project1"})
    with pytest.raises(ValidationError):
        Command.model_validate({"name": "future.command", "input": {"operator_path": "/project1"}})


def test_protocol_rejects_unknown_and_coerced_command_input_fields() -> None:
    with pytest.raises(ValidationError):
        OperatorInput.model_validate({"operator_path": 12})
    with pytest.raises(ValidationError):
        OperatorInput.model_validate({"operator_path": "/project1", "extra": True})
    with pytest.raises(ValidationError):
        Command.model_validate({"name": "ops.get", "input": {"operator_path": 12}})


def test_command_has_stable_canonical_json_independent_of_key_order() -> None:
    first = Command(name="ops.children", input={"operator_path": "/project1", "op_type": "base"})
    second = Command(name="ops.children", input={"op_type": "base", "operator_path": "/project1"})

    assert first.canonical_json() == second.canonical_json()
    assert (
        first.canonical_json()
        == '{"input":{"op_type":"base","operator_path":"/project1"},"name":"ops.children"}'
    )


def test_request_snapshot_serializes_protocol_v1_public_shape() -> None:
    snapshot = RequestSnapshot.pending(
        request_id="018f47ec-7f3b-7a34-8f31-2ad70b6f6e2a",
        instance_id="8cf81688-b9a4-4c39-9f92-31c77319c761",
        command=Command(name="ops.get", input={"operator_path": "/project1"}),
        submitted_at="2026-08-08T01:02:03.004Z",
    )

    payload = json.loads(snapshot.model_dump_json())
    assert payload["status"] == RequestStatus.QUEUED
    assert payload["result"] is None
    assert payload["error"] is None
    assert payload["dispatched_at"] is None


@pytest.mark.parametrize(
    ("payload", "canonical"),
    [
        (
            {"name": "ops.get", "input": {"operator_path": "/project1"}},
            '{"input":{"operator_path":"/project1"},"name":"ops.get"}',
        ),
        (
            {"name": "ops.children", "input": {"operator_path": "/project1"}},
            '{"input":{"op_type":null,"operator_path":"/project1"},"name":"ops.children"}',
        ),
        (
            {
                "name": "parameters.get",
                "input": {"operator_path": "/project1", "parameter": "display"},
            },
            '{"input":{"operator_path":"/project1","parameter":"display"},"name":"parameters.get"}',
        ),
        (
            {
                "name": "parameters.set",
                "input": {
                    "operator_path": "/project1",
                    "parameter": "display",
                    "mode": "constant",
                    "value": True,
                },
            },
            '{"input":{"mode":"constant","operator_path":"/project1","parameter":"display","value":true},"name":"parameters.set"}',
        ),
        (
            {
                "name": "parameters.pulse",
                "input": {"operator_path": "/project1", "parameter": "reset"},
            },
            '{"input":{"operator_path":"/project1","parameter":"reset"},"name":"parameters.pulse"}',
        ),
    ],
)
def test_protocol_v1_typed_commands_validate_and_canonicalize(
    payload: dict[str, object], canonical: str
) -> None:
    assert Command.model_validate(payload).canonical_json() == canonical


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "ops.get", "input": {"operator_path": "project1"}},
        {"name": "ops.get", "input": {"operator_path": "/project1/../secret"}},
        {"name": "ops.children", "input": {"operator_path": "/project1", "extra": True}},
        {
            "name": "parameters.get",
            "input": {"operator_path": "/project1", "parameter": ""},
        },
        {
            "name": "parameters.set",
            "input": {
                "operator_path": "/project1",
                "parameter": "display",
                "mode": "constant",
                "value": 9_007_199_254_740_992,
            },
        },
        {
            "name": "parameters.set",
            "input": {
                "operator_path": "/project1",
                "parameter": "display",
                "mode": "constant",
                "value": float("inf"),
            },
        },
    ],
)
def test_protocol_v1_typed_commands_reject_invalid_input(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Command.model_validate(payload)


def test_phase_3_commands_are_strict_and_bounded() -> None:
    snapshot = Command.model_validate(
        {"name": "project.snapshot", "input": {"operator_path": "/project1"}}
    )
    assert snapshot.input.model_dump() == {
        "operator_path": "/project1",
        "max_depth": 4,
        "max_operators": 256,
    }
    batch = Command.model_validate(
        {
            "name": "batch.execute",
            "input": {
                "commands": [
                    {"name": "ops.get", "input": {"operator_path": "/project1"}},
                    {
                        "name": "parameters.set",
                        "input": {
                            "operator_path": "/project1",
                            "parameter": "display",
                            "mode": "constant",
                            "value": False,
                        },
                    },
                ]
            },
        }
    )
    assert len(batch.input.commands) == 2

    with pytest.raises(ValidationError):
        Command.model_validate({"name": "events.read", "input": {"after": 0, "limit": 201}})
    with pytest.raises(ValidationError):
        Command.model_validate(
            {
                "name": "batch.execute",
                "input": {"commands": [{"name": "batch.execute", "input": {}}]},
            }
        )


def test_network_mutation_commands_are_strict_bounded_and_not_batchable() -> None:
    created = Command.model_validate(
        {
            "name": "ops.create",
            "input": {
                "parent_path": "/project1",
                "op_type": "constantTOP",
                "name": "source",
                "node_x": -100,
                "node_y": 25,
            },
        }
    )
    assert created.input.model_dump() == {
        "parent_path": "/project1",
        "op_type": "constantTOP",
        "name": "source",
        "node_x": -100,
        "node_y": 25,
        "allow_conditional": False,
    }
    connected = Command.model_validate(
        {
            "name": "ops.connect",
            "input": {
                "source_path": "/project1/source",
                "target_path": "/project1/output",
            },
        }
    )
    assert connected.input.model_dump() == {
        "source_path": "/project1/source",
        "target_path": "/project1/output",
        "output_index": 0,
        "input_index": 0,
        "replace": False,
    }

    invalid = [
        {
            "name": "ops.create",
            "input": {
                "parent_path": "/project1",
                "op_type": "fileinTOP",
                "name": "source",
                "node_x": 0,
                "node_y": 0,
            },
        },
        {
            "name": "ops.create",
            "input": {
                "parent_path": "/project1",
                "op_type": "constantTOP",
                "name": "bad/name",
                "node_x": 0,
                "node_y": 0,
            },
        },
        {
            "name": "ops.connect",
            "input": {
                "source_path": "/project1/source",
                "target_path": "/project1/output",
                "output_index": 256,
                "input_index": 0,
            },
        },
        {
            "name": "batch.execute",
            "input": {
                "commands": [
                    {
                        "name": "ops.create",
                        "input": {
                            "parent_path": "/project1",
                            "op_type": "constantTOP",
                            "name": "source",
                            "node_x": 0,
                            "node_y": 0,
                        },
                    }
                ]
            },
        },
    ]
    for payload in invalid:
        with pytest.raises(ValidationError):
            Command.model_validate(payload)


@pytest.mark.parametrize(
    ("payload", "expected_input"),
    [
        (
            {
                "name": "ops.rename",
                "input": {"operator_path": "/project1/old", "new_name": "new_name"},
            },
            {"operator_path": "/project1/old", "new_name": "new_name"},
        ),
        (
            {
                "name": "ops.disconnect",
                "input": {"source_path": "/project1/a", "target_path": "/project1/b"},
            },
            {
                "source_path": "/project1/a",
                "target_path": "/project1/b",
                "output_index": 0,
                "input_index": 0,
            },
        ),
        (
            {
                "name": "ops.connect",
                "input": {
                    "source_path": "/project1/a",
                    "target_path": "/project1/b",
                    "replace": True,
                },
            },
            {
                "source_path": "/project1/a",
                "target_path": "/project1/b",
                "output_index": 0,
                "input_index": 0,
                "replace": True,
            },
        ),
        (
            {"name": "parameters.list", "input": {"operator_path": "/project1/a"}},
            {"operator_path": "/project1/a"},
        ),
    ],
)
def test_v011_commands_validate_at_the_protocol_seam(payload, expected_input) -> None:
    command = Command.model_validate(payload)
    assert command.input.model_dump() == expected_input


def test_connections_command_has_a_bounded_read_only_contract() -> None:
    command = Command.model_validate(
        {
            "name": "ops.connections",
            "input": {"operator_path": "/project1/source", "max_connections": 12},
        }
    )

    assert command.input.model_dump() == {
        "operator_path": "/project1/source",
        "max_connections": 12,
    }
    assert "ops.connections" in COMMAND_CATALOG.batch_names

    with pytest.raises(ValidationError):
        Command.model_validate(
            {
                "name": "ops.connections",
                "input": {"operator_path": "/project1/source", "max_connections": 1001},
            }
        )


def test_destroy_command_requires_explicit_bounded_destructive_options() -> None:
    command = Command.model_validate(
        {
            "name": "ops.destroy",
            "input": {
                "operator_path": "/project1/old",
                "recursive": True,
                "allow_connected": True,
                "max_operators": 20,
            },
        }
    )

    assert command.input.model_dump() == {
        "operator_path": "/project1/old",
        "recursive": True,
        "allow_connected": True,
        "max_operators": 20,
    }
    assert "ops.destroy" not in COMMAND_CATALOG.batch_names

    with pytest.raises(ValidationError):
        Command.model_validate(
            {
                "name": "ops.destroy",
                "input": {"operator_path": "/project1/old", "max_operators": 1001},
            }
        )


def test_copy_command_has_exact_destination_and_bounded_subtree_contract() -> None:
    command = Command.model_validate(
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
        }
    )

    assert command.input.model_dump() == {
        "source_path": "/project1/source",
        "target_parent_path": "/project1/group",
        "new_name": "copy",
        "node_x": -20,
        "node_y": 40,
        "include_docked": True,
        "max_operators": 20,
    }
    assert "ops.copy" not in COMMAND_CATALOG.batch_names

    with pytest.raises(ValidationError):
        Command.model_validate(
            {
                "name": "ops.copy",
                "input": {
                    "source_path": "/project1/source",
                    "target_parent_path": "/project1/group",
                    "new_name": "bad/name",
                },
            }
        )


def test_move_command_exposes_copy_destroy_and_detachment_authorization() -> None:
    command = Command.model_validate(
        {
            "name": "ops.move",
            "input": {
                "source_path": "/project1/source",
                "target_parent_path": "/project1/group",
                "new_name": "moved",
                "node_x": 10,
                "node_y": 20,
                "allow_connected": True,
                "max_operators": 30,
            },
        }
    )

    assert command.input.model_dump() == {
        "source_path": "/project1/source",
        "target_parent_path": "/project1/group",
        "new_name": "moved",
        "node_x": 10,
        "node_y": 20,
        "allow_connected": True,
        "max_operators": 30,
    }
    assert "ops.move" not in COMMAND_CATALOG.batch_names


@pytest.mark.parametrize("name", ["ops.rename", "ops.disconnect", "ops.connect"])
def test_v011_mutations_are_not_batchable(name: str) -> None:
    with pytest.raises(ValidationError):
        Command.model_validate(
            {"name": "batch.execute", "input": {"commands": [{"name": name, "input": {}}]}}
        )


def test_parameters_list_is_batchable() -> None:
    command = Command.model_validate(
        {
            "name": "batch.execute",
            "input": {
                "commands": [{"name": "parameters.list", "input": {"operator_path": "/project1/a"}}]
            },
        }
    )
    assert command.input.commands[0].name == "parameters.list"


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "ops.rename", "input": {"operator_path": "/project1/a", "new_name": "bad/name"}},
        {"name": "ops.rename", "input": {"operator_path": "/project1/a", "new_name": "9bad"}},
        {
            "name": "ops.disconnect",
            "input": {
                "source_path": "/project1/a",
                "target_path": "/project1/b",
                "input_index": 256,
            },
        },
        {
            "name": "ops.connect",
            "input": {
                "source_path": "/project1/a",
                "target_path": "/project1/b",
                "replace": 1,
            },
        },
    ],
)
def test_v011_commands_reject_unsafe_names_bounds_and_coercion(payload) -> None:
    with pytest.raises(ValidationError):
        Command.model_validate(payload)
