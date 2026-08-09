import json

import pytest
from pydantic import ValidationError

from td_cli.protocol import Command, OperatorInput, RequestSnapshot, RequestStatus


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
