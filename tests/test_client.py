from pathlib import Path

import httpx
import pytest

from td_cli.client import ClientError, DaemonClient


def client(tmp_path: Path) -> DaemonClient:
    state = tmp_path / "state"
    state.mkdir()
    (state / "auth.token").write_text("a" * 64, encoding="ascii")
    return DaemonClient(timeout=1, root=tmp_path)


def test_read_only_query_retries_with_fixed_backoffs(tmp_path: Path, monkeypatch) -> None:
    attempts = 0
    sleeps = []

    def request(*args, **kwargs):
        nonlocal attempts
        del args, kwargs
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("not ready")
        return httpx.Response(200, json=[])

    monkeypatch.setattr(httpx, "request", request)
    monkeypatch.setattr("td_cli.client.time.sleep", sleeps.append)

    assert client(tmp_path).instances() == []
    assert attempts == 3
    assert sleeps == [0.1, 0.3]


def test_command_transport_failure_preserves_known_request_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        httpx, "request", lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ConnectError("down"))
    )

    with pytest.raises(ClientError) as caught:
        client(tmp_path).submit("request-7", "instance-1", {"name": "ops.get", "input": {}})

    assert caught.value.code == "daemon_unavailable"
    assert caught.value.details == {"request_id": "request-7"}


def test_read_timeout_is_not_retried(tmp_path: Path, monkeypatch) -> None:
    attempts = 0

    def request(*args, **kwargs):
        nonlocal attempts
        del args, kwargs
        attempts += 1
        raise httpx.ReadTimeout("response stalled")

    monkeypatch.setattr(httpx, "request", request)

    with pytest.raises(ClientError) as caught:
        client(tmp_path).instances()

    assert caught.value.code == "daemon_unavailable"
    assert attempts == 1


def test_unknown_parameter_result_enum_is_protocol_incompatible(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *args, **kwargs: httpx.Response(
            200,
            json={
                "request_id": "request-1",
                "status": "succeeded",
                "command": {"name": "parameters.get", "input": {}},
                "result": {"mode": "future", "value_type": "future"},
                "error": None,
            },
        ),
    )

    with pytest.raises(ClientError) as caught:
        client(tmp_path).get_request("request-1")

    assert caught.value.code == "protocol_incompatible"


def test_network_mutation_error_remains_typed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *args, **kwargs: httpx.Response(
            200,
            json={
                "request_id": "request-1",
                "status": "failed",
                "command": {"name": "ops.connect", "input": {}},
                "result": None,
                "error": {
                    "code": "connector_occupied",
                    "message": "connector_occupied",
                    "details": {},
                    "retryable": False,
                },
            },
        ),
    )

    assert client(tmp_path).get_request("request-1")["error"]["code"] == "connector_occupied"


@pytest.mark.parametrize(
    "code",
    [
        "operator_rename_forbidden",
        "operator_rename_failed",
        "operator_rename_rollback_failed",
        "connection_not_found",
        "connector_disconnect_failed",
        "connector_replace_failed",
        "connector_replace_rollback_failed",
        "operator_type_unsupported",
        "operator_type_conditional",
        "operator_mutation_forbidden",
        "operator_not_empty",
        "operator_connected",
        "operator_destroy_failed",
        "operator_destroy_outcome_unknown",
        "operator_copy_failed",
        "operator_copy_rollback_failed",
        "operator_docked",
        "operator_move_failed",
        "operator_move_rollback_failed",
        "operator_move_outcome_unknown",
        "operator_state_unavailable",
        "operator_state_failed",
        "operator_state_rollback_failed",
        "operator_state_outcome_unknown",
        "dat_type_mismatch",
        "dat_content_unavailable",
        "dat_content_not_writable",
        "dat_content_too_large",
        "text_dat_write_failed",
        "text_dat_rollback_failed",
        "text_dat_outcome_unknown",
        "table_dat_patch_out_of_bounds",
        "table_dat_write_failed",
        "table_dat_rollback_failed",
        "table_dat_outcome_unknown",
    ],
)
def test_execution_errors_remain_typed(tmp_path: Path, monkeypatch, code: str) -> None:
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *args, **kwargs: httpx.Response(
            200,
            json={
                "request_id": "request-1",
                "status": "failed",
                "command": {"name": "ops.rename", "input": {}},
                "result": None,
                "error": {"code": code, "message": code, "details": {}, "retryable": False},
            },
        ),
    )
    assert client(tmp_path).get_request("request-1")["error"]["code"] == code


@pytest.mark.parametrize("mode", ["constant", "expression", "export", "bind"])
@pytest.mark.parametrize(
    "value_kind",
    [
        "boolean",
        "integer",
        "number",
        "string",
        "menu",
        "operator",
        "pulse",
        "python",
        "sequence",
        "unknown",
    ],
)
def test_parameter_list_accepts_locked_introspection_enums(
    tmp_path: Path, monkeypatch, mode: str, value_kind: str
) -> None:
    snapshot = {
        "request_id": "request-1",
        "status": "succeeded",
        "command": {"name": "parameters.list", "input": {}},
        "result": {"parameters": [{"mode": mode, "value_kind": value_kind}]},
        "error": None,
    }
    monkeypatch.setattr(
        httpx, "request", lambda *args, **kwargs: httpx.Response(200, json=snapshot)
    )
    assert client(tmp_path).get_request("request-1") == snapshot


@pytest.mark.parametrize(
    "parameter",
    [{"mode": "future", "value_kind": "number"}, {"mode": "constant", "value_kind": "future"}],
)
def test_parameter_list_rejects_unknown_introspection_enums(
    tmp_path: Path, monkeypatch, parameter: dict[str, str]
) -> None:
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *args, **kwargs: httpx.Response(
            200,
            json={
                "request_id": "request-1",
                "status": "succeeded",
                "command": {"name": "parameters.list", "input": {}},
                "result": {"parameters": [parameter]},
                "error": None,
            },
        ),
    )
    with pytest.raises(ClientError, match="protocol_incompatible"):
        client(tmp_path).get_request("request-1")
