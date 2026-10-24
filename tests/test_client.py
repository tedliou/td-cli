import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from td_cli.client import ClientError, DaemonClient, DaemonConnectError, DaemonConnection
from td_cli.daemon.runtime_files import ENDPOINT
from td_cli.error_catalog import ERROR_CATALOG


def patch_http(monkeypatch, request) -> None:
    """Route every Daemon exchange to ``request(method, url, **kwargs)``.

    ``request`` answers with an ``httpx.Response`` or raises an httpx error; a connect error
    becomes a failure to open the connection and any other one a failure after sending.
    """

    def exchange(self, method, path, *, headers, body, timeout):
        try:
            response = request(
                method,
                f"{ENDPOINT}{path}",
                headers=headers,
                json=None if body is None else json.loads(body),
                timeout=timeout,
            )
        except httpx.ConnectError as error:
            raise DaemonConnectError(str(error)) from error
        except httpx.HTTPError as error:
            raise TimeoutError(str(error)) from error
        return response.status_code, response.content

    monkeypatch.setattr(DaemonConnection, "exchange", exchange)


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

    patch_http(monkeypatch, request)
    assert instance_client.instances() == []
    with pytest.raises(ClientError) as error:
        instance_client.submit("known-request", "instance", {})
    assert events == ["GET", "start", "GET", "POST"]  # unready health probe, then start
    assert error.value.details["request_id"] == "known-request"


def test_poll_failure_retains_request_id(tmp_path, monkeypatch):
    monkeypatch.setattr(
        DaemonClient,
        "get_request",
        lambda *a, **k: (_ for _ in ()).throw(ClientError("daemon_unavailable")),
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

    patch_http(monkeypatch, request)
    monkeypatch.setattr("td_cli.client.time.sleep", sleeps.append)

    assert client(tmp_path).instances() == []
    assert attempts == 3
    assert sleeps == [0.1, 0.3]


def test_command_transport_failure_preserves_known_request_id(tmp_path: Path, monkeypatch) -> None:
    patch_http(
        monkeypatch, lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ConnectError("down"))
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
    patch_http(monkeypatch, lambda *args, **kwargs: httpx.Response(200, json=snapshot))

    assert client(tmp_path).get_request("request-1") == snapshot


def test_read_timeout_is_not_retried(tmp_path: Path, monkeypatch) -> None:
    attempts = 0

    def request(*args, **kwargs):
        nonlocal attempts
        del args, kwargs
        attempts += 1
        raise httpx.ReadTimeout("response stalled")

    patch_http(monkeypatch, request)

    with pytest.raises(ClientError) as caught:
        client(tmp_path).instances()

    assert caught.value.code == "daemon_unavailable"
    assert attempts == 1


def test_unknown_parameter_result_enum_is_protocol_incompatible(
    tmp_path: Path, monkeypatch
) -> None:
    patch_http(
        monkeypatch,
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
    patch_http(
        monkeypatch,
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
    patch_http(
        monkeypatch,
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
    patch_http(monkeypatch, lambda *a, **k: httpx.Response(200, json=snapshot))

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
    patch_http(monkeypatch, lambda *args, **kwargs: httpx.Response(200, json=snapshot))
    assert client(tmp_path).get_request("request-1") == snapshot


@pytest.mark.parametrize(
    "parameter",
    [{"mode": "future", "value_kind": "number"}, {"mode": "constant", "value_kind": "future"}],
)
def test_parameter_list_rejects_unknown_introspection_enums(
    tmp_path: Path, monkeypatch, parameter: dict[str, str]
) -> None:
    patch_http(
        monkeypatch,
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
    patch_http(monkeypatch, request)
    assert instance_client.instances() == []
    assert observed == [0.5, 30, 18]  # health probe, start budget, remaining HTTP budget


def test_client_start_reaches_the_daemon_start_routine(monkeypatch) -> None:
    import td_cli.client
    import td_cli.daemon.cli

    observed = []
    monkeypatch.setattr(td_cli.daemon.cli, "ensure_running", lambda **kw: observed.append(kw))
    td_cli.client.ensure_running(timeout=3.5)
    assert observed == [{"timeout": 3.5}]


HEALTHY = {"ready": True, "protocol_versions": [3], "release_version": "test"}


def test_healthy_daemon_serves_a_command_on_one_connection_without_starting(
    tmp_path: Path, monkeypatch
) -> None:
    calls = []
    starts = []
    monkeypatch.setattr("td_cli.client.ensure_running", lambda **kw: starts.append(kw))

    def request(method, url, **kwargs):
        calls.append((method, url.rsplit("/", 1)[-1]))
        return httpx.Response(200, json=HEALTHY if url.endswith("/health") else [])

    patch_http(monkeypatch, request)
    instance_client = client(tmp_path)
    instance_client._autostart = True
    assert instance_client.instances() == []
    assert instance_client.instances() == []
    assert starts == []
    assert calls == [("GET", "health"), ("GET", "instances"), ("GET", "instances")]


@pytest.mark.parametrize(
    "health",
    [
        {"ready": False, "protocol_versions": [3]},
        {"ready": True, "protocol_versions": [2]},
        {"ready": True, "protocol_versions": "3"},
        [],
    ],
)
def test_doubtful_health_is_left_to_the_start_routine(tmp_path: Path, monkeypatch, health) -> None:
    from td_cli.processes import LaunchError

    starts = []

    def start(**kwargs):
        starts.append(kwargs)
        raise LaunchError("Daemon is starting/unhealthy")

    monkeypatch.setattr("td_cli.client.ensure_running", start)
    patch_http(monkeypatch, lambda *a, **k: httpx.Response(200, json=health))
    instance_client = client(tmp_path)
    instance_client._autostart = True
    with pytest.raises(ClientError) as error:
        instance_client.instances()
    assert error.value.code == "daemon_unavailable"
    assert error.value.details == {"reason": "Daemon is starting/unhealthy"}
    assert len(starts) == 1


def test_missing_token_starts_the_daemon_without_probing(tmp_path: Path, monkeypatch) -> None:
    events = []

    def start(**kwargs):
        events.append("start")
        state = tmp_path / "state"
        state.mkdir()
        (state / "auth.token").write_text("a" * 64, encoding="ascii")

    def request(method, url, **kwargs):
        events.append(url.rsplit("/", 1)[-1])
        return httpx.Response(200, json=[])

    monkeypatch.setattr("td_cli.client.ensure_running", start)
    patch_http(monkeypatch, request)
    instance_client = DaemonClient(timeout=1, root=tmp_path, autostart=True)
    assert instance_client.instances() == []
    assert events == ["start", "instances"]


def test_unreachable_probe_starts_with_the_remaining_budget(tmp_path: Path, monkeypatch) -> None:
    clock = [100.0]
    observed = []
    monkeypatch.setattr("td_cli.client.time.monotonic", lambda: clock[0])

    def request(method, url, **kwargs):
        if url.endswith("/health"):
            clock[0] += 0.5
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json=[])

    monkeypatch.setattr("td_cli.client.ensure_running", lambda **kw: observed.append(kw))
    patch_http(monkeypatch, request)
    instance_client = client(tmp_path)
    instance_client.timeout = 10
    instance_client._autostart = True
    assert instance_client.instances() == []
    assert observed == [{"timeout": 9.5}]


def test_a_closed_client_reports_the_daemon_unavailable(tmp_path: Path) -> None:
    instance_client = client(tmp_path)
    instance_client.close()
    with pytest.raises(ClientError, match="daemon_unavailable"):
        instance_client.request("POST", "/v3/requests")


@pytest.mark.parametrize("health", [HEALTHY, None])
def test_deadline_spent_in_the_health_probe_sends_nothing_more(
    tmp_path: Path, monkeypatch, health
) -> None:
    clock = [100.0]
    calls = []
    starts = []
    monkeypatch.setattr("td_cli.client.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("td_cli.client.ensure_running", lambda **kw: starts.append(kw))

    def request(method, url, **kwargs):
        calls.append(url.rsplit("/", 1)[-1])
        clock[0] += 2
        if health is None:
            raise httpx.ConnectTimeout("probe stalled")
        return httpx.Response(200, json=health)

    patch_http(monkeypatch, request)
    instance_client = client(tmp_path)
    instance_client._autostart = True
    with pytest.raises(ClientError) as error:
        instance_client.instances()
    assert error.value.code == "daemon_unavailable"
    assert error.value.details == {"reason": "command deadline expired"}
    assert calls == ["health"]
    assert starts == []


def test_wait_long_polls_with_the_remaining_budget(tmp_path: Path, monkeypatch) -> None:
    clock = [100.0]
    urls = []
    monkeypatch.setattr("td_cli.client.time.monotonic", lambda: clock[0])

    def request(method, url, **kwargs):
        urls.append(url.split("/v3/requests/", 1)[1])
        return httpx.Response(200, json={"status": "succeeded", "request_id": "r"})

    patch_http(monkeypatch, request)
    instance_client = client(tmp_path)
    instance_client.timeout = 10
    assert instance_client.wait("r")["status"] == "succeeded"
    instance_client.timeout = 60
    instance_client.wait("r")
    assert urls == ["r?wait_ms=9900", "r?wait_ms=25000"]


def test_retried_long_poll_waits_only_for_the_budget_left(tmp_path: Path, monkeypatch) -> None:
    clock = [100.0]
    attempts = []
    monkeypatch.setattr("td_cli.client.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(
        "td_cli.client.time.sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )

    def request(method, url, **kwargs):
        attempts.append((url.split("/v3/requests/", 1)[1], kwargs["timeout"]))
        if len(attempts) == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"status": "succeeded", "request_id": "r"})

    patch_http(monkeypatch, request)
    instance_client = client(tmp_path)
    instance_client.timeout = 10
    assert instance_client.wait("r")["status"] == "succeeded"
    assert [url for url, _ in attempts] == ["r?wait_ms=9900", "r?wait_ms=9800"]
    assert attempts[1][1] == pytest.approx(9.9)  # the HTTP timeout outlasts the Daemon wait


def test_wait_keeps_spacing_when_a_daemon_answers_unsettled_at_once(
    tmp_path: Path, monkeypatch
) -> None:
    answers = iter(["running", "running", "succeeded"])
    sleeps = []
    monkeypatch.setattr("td_cli.client.time.sleep", sleeps.append)
    patch_http(
        monkeypatch,
        lambda *a, **k: httpx.Response(200, json={"status": next(answers), "request_id": "r"}),
    )
    assert client(tmp_path).wait("r")["status"] == "succeeded"
    assert sleeps == [0.05, 0.05]


class _CountingHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    connections: ClassVar[list[socket.socket]] = []

    def setup(self) -> None:
        super().setup()
        self.connections.append(self.connection)

    def do_GET(self) -> None:
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


def _serve() -> tuple[ThreadingHTTPServer, str]:
    _CountingHandler.connections = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CountingHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_daemon_connection_keeps_one_connection_and_replaces_one_the_daemon_closed() -> None:
    server, endpoint = _serve()
    connection = DaemonConnection(endpoint)
    try:
        for _ in range(3):
            assert connection.exchange("GET", "/", headers={}, body=None, timeout=2) == (
                200,
                b'{"ok":true}',
            )
        assert len(_CountingHandler.connections) == 1
        _CountingHandler.connections[0].shutdown(socket.SHUT_RDWR)
        assert connection.exchange("GET", "/", headers={}, body=None, timeout=2)[0] == 200
        assert len(_CountingHandler.connections) == 2
    finally:
        connection.close()
        server.shutdown()
        server.server_close()


def test_daemon_connection_reports_a_refused_connection_as_nothing_sent() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(DaemonConnectError):
        DaemonConnection(f"http://127.0.0.1:{port}").exchange(
            "POST", "/v3/requests", headers={}, body=b"{}", timeout=2
        )


def test_new_contract_carries_selector_and_validation_details(tmp_path: Path, monkeypatch) -> None:
    sent = []
    details = {"validation_errors": [{"location": ["input"]}], "validation_errors_truncated": False}

    def request(method, url, **kwargs):
        sent.append((method, url.removeprefix(ENDPOINT), kwargs["json"]))
        if method == "POST":
            return httpx.Response(422, json={"detail": "invalid_arguments", "details": details})
        return httpx.Response(200, json={"instance_id": "i", "status": "offline"})

    patch_http(monkeypatch, request)
    instance_client = client(tmp_path)
    assert instance_client.select_instance("a b", online_only=False)["status"] == "offline"
    instance_client.select_instance(None)
    with pytest.raises(ClientError) as raised:
        instance_client.submit("r", None, {"name": "ops.get"}, instance_selector="8c")
    assert raised.value.code == "invalid_arguments"
    assert raised.value.details == {**details, "request_id": "r"}
    assert sent == [
        ("GET", "/v3/instances/resolve?online_only=false&selector=a+b", None),
        ("GET", "/v3/instances/resolve?online_only=true", None),
        (
            "POST",
            "/v3/requests",
            {"request_id": "r", "command": {"name": "ops.get"}, "instance_selector": "8c"},
        ),
    ]
