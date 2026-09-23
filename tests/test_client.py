from pathlib import Path

import httpx
import pytest

from td_cli.client import ClientError, DaemonClient
from td_cli.error_catalog import ERROR_CATALOG


def client(tmp_path: Path) -> DaemonClient:
    state = tmp_path / "state"
    state.mkdir()
    (state / "auth.token").write_text("a" * 64, encoding="ascii")
    return DaemonClient(timeout=1, root=tmp_path)


def test_save_precondition_failure_remains_observable(tmp_path, monkeypatch):
    snapshot = {"status": "failed", "error": {"code": "project_file_changed"}}
    monkeypatch.setattr(DaemonClient, "request", lambda *a, **k: snapshot)
    assert client(tmp_path).get_request("save-request") == snapshot


def test_autostart_occurs_once_before_http_and_never_retries_a_mutation(tmp_path, monkeypatch):
    events = []
    instance_client = client(tmp_path)
    instance_client._autostart = True
    monkeypatch.setattr("td_cli.client.ensure_running", lambda **kw: events.append("start"))

    def request(method, *a, **kw):
        events.append(method)
        if method == "POST":
            raise httpx.ConnectError("lost response")
        return httpx.Response(200, json=[])

    monkeypatch.setattr(httpx, "request", request)
    assert instance_client.instances() == []
    with pytest.raises(ClientError) as error:
        instance_client.submit("known-request", "instance", {})
    assert events == ["start", "GET", "POST"]
    assert error.value.details["request_id"] == "known-request"


def test_poll_failure_retains_request_id(tmp_path, monkeypatch):
    monkeypatch.setattr(
        DaemonClient,
        "get_request",
        lambda *a: (_ for _ in ()).throw(ClientError("daemon_unavailable")),
    )
    with pytest.raises(ClientError) as error:
        client(tmp_path).wait("known-request")
    assert error.value.details["request_id"] == "known-request"


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


def test_accepted_request_remains_queryable_while_execution_is_authorized(
    tmp_path: Path, monkeypatch
) -> None:
    snapshot = {
        "request_id": "request-1",
        "status": "accepted",
        "command": {"name": "ops.children", "input": {"operator_path": "/project1"}},
        "result": None,
        "error": None,
    }
    monkeypatch.setattr(
        httpx, "request", lambda *args, **kwargs: httpx.Response(200, json=snapshot)
    )

    assert client(tmp_path).get_request("request-1") == snapshot


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


@pytest.mark.parametrize("code", sorted(ERROR_CATALOG.codes))
def test_every_catalogued_error_code_remains_typed(tmp_path: Path, monkeypatch, code: str) -> None:
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
                "error": ERROR_CATALOG.error(code),
            },
        ),
    )
    assert client(tmp_path).get_request("request-1")["error"]["code"] == code


def test_uncatalogued_error_code_is_incompatible_without_losing_the_request(
    tmp_path: Path, monkeypatch
) -> None:
    snapshot = {
        "request_id": "request-1",
        "status": "unknown",
        "command": {"name": "parameters.set", "input": {}},
        "result": None,
        "error": ERROR_CATALOG.error("future_outcome_unknown"),
    }
    monkeypatch.setattr(httpx, "request", lambda *a, **k: httpx.Response(200, json=snapshot))

    with pytest.raises(ClientError) as caught:
        client(tmp_path).wait("request-1")

    assert caught.value.code == "protocol_incompatible"
    assert caught.value.details["request_id"] == "request-1"
    assert caught.value.details["request"]["status"] == "unknown"


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


def test_startup_uses_visible_budget_and_http_receives_only_remaining(tmp_path, monkeypatch):
    clock = [100.0]
    observed = []
    instance_client = client(tmp_path)
    instance_client.timeout = 30
    instance_client._autostart = True
    monkeypatch.setattr("td_cli.client.time.monotonic", lambda: clock[0])

    def startup(*, timeout):
        observed.append(timeout)
        clock[0] += 12

    def request(*args, **kwargs):
        observed.append(kwargs["timeout"])
        return httpx.Response(200, json=[])

    monkeypatch.setattr("td_cli.client.ensure_running", startup)
    monkeypatch.setattr(httpx, "request", request)
    assert instance_client.instances() == []
    assert observed == [30, 18]
