import json

import pytest
from pydantic import ValidationError

from td_cli.protocol import Command, DiagnosticInput, RequestSnapshot, RequestStatus


def test_protocol_rejects_unknown_and_coerced_command_input_fields() -> None:
    with pytest.raises(ValidationError):
        DiagnosticInput.model_validate({"message": 12})
    with pytest.raises(ValidationError):
        DiagnosticInput.model_validate({"message": "ping", "extra": True})
    with pytest.raises(ValidationError):
        Command.model_validate({"name": "diagnostic.ping", "input": {"message": 12}})


def test_command_has_stable_canonical_json_independent_of_key_order() -> None:
    first = Command(name="diagnostic.ping", input={"message": "ping", "sequence": 1})
    second = Command(name="diagnostic.ping", input={"sequence": 1, "message": "ping"})

    assert first.canonical_json() == second.canonical_json()
    assert (
        first.canonical_json()
        == '{"input":{"message":"ping","sequence":1},"name":"diagnostic.ping"}'
    )


def test_request_snapshot_serializes_protocol_v1_public_shape() -> None:
    snapshot = RequestSnapshot.pending(
        request_id="018f47ec-7f3b-7a34-8f31-2ad70b6f6e2a",
        instance_id="8cf81688-b9a4-4c39-9f92-31c77319c761",
        command=Command(name="diagnostic.ping", input={"message": "ping"}),
        submitted_at="2026-08-08T01:02:03.004Z",
    )

    payload = json.loads(snapshot.model_dump_json())
    assert payload["status"] == RequestStatus.QUEUED
    assert payload["result"] is None
    assert payload["error"] is None
    assert payload["dispatched_at"] is None
