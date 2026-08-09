import json
from pathlib import Path
from runpy import run_path

BridgeApplication = run_path(str(Path("tools/td_diagnostic_bridge/core.py")))["BridgeApplication"]
start_server = run_path(str(Path("tools/td_diagnostic_bridge/server.py")))["start_server"]
BridgeClient = run_path(str(Path("tools/td_diagnostic_bridge/client.py")))["BridgeClient"]


def test_health_requires_the_session_token() -> None:
    bridge = BridgeApplication(token="session-secret", touchdesigner_build="2025.32050")

    unauthorized = bridge.handle_request("GET", "/health", {}, b"")
    authorized = bridge.handle_request(
        "GET",
        "/health",
        {"Authorization": "Bearer session-secret"},
        b"",
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.payload == {
        "bridge_version": 1,
        "touchdesigner_build": "2025.32050",
        "queue_depth": 0,
        "busy": False,
    }


def test_authenticated_job_runs_and_returns_output() -> None:
    bridge = BridgeApplication(token="session-secret", touchdesigner_build="2025.32050")
    headers = {"Authorization": "Bearer session-secret"}

    submitted = bridge.handle_request(
        "POST",
        "/jobs",
        headers,
        json.dumps({"code": "print('probe')\nresult = 6 * 7", "mode": "exec"}).encode(),
    )
    bridge.execute_next({})
    completed = bridge.handle_request(
        "GET",
        f"/jobs/{submitted.payload['job_id']}",
        headers,
        b"",
    )

    assert submitted.status_code == 202
    assert completed.status_code == 200
    assert completed.payload["status"] == "succeeded"
    assert completed.payload["value"] == "42"
    assert completed.payload["stdout"] == "probe\n"
    assert completed.payload["traceback"] == ""


def test_authenticated_shutdown_is_deferred_to_the_main_thread() -> None:
    bridge = BridgeApplication(token="session-secret", touchdesigner_build="2025.32050")

    response = bridge.handle_request(
        "POST",
        "/shutdown",
        {"Authorization": "Bearer session-secret"},
        b"",
    )

    assert response.status_code == 202
    assert response.payload == {"status": "stopping"}
    assert bridge.consume_shutdown_request() is True
    assert bridge.consume_shutdown_request() is False


def test_http_adapter_listens_on_loopback_and_serves_health() -> None:
    bridge = BridgeApplication(token="session-secret", touchdesigner_build="2025.32050")
    server = start_server(bridge, port=0)
    client = BridgeClient(server.base_url, "session-secret")

    try:
        assert server.host == "127.0.0.1"
        assert client.health()["touchdesigner_build"] == "2025.32050"
    finally:
        server.shutdown()
