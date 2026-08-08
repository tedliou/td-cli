from pathlib import Path

from fastapi.testclient import TestClient

from td_cli.daemon.app import create_app

TOKEN = "a" * 64
INSTANCE_ID = "8cf81688-b9a4-4c39-9f92-31c77319c761"
REQUEST_ID = "018f47ec-7f3b-7a34-8f31-2ad70b6f6e2a"


def headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_health_discloses_nothing_without_authentication(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path, token=TOKEN)) as client:
        response = client.get("/v1/health")
        assert response.status_code == 404
        assert response.json() == {"detail": "Not Found"}


def test_request_is_accepted_durably_and_queryable(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path, token=TOKEN)) as client:
        response = client.post(
            "/v1/requests",
            headers=headers(),
            json={
                "request_id": REQUEST_ID,
                "instance_id": INSTANCE_ID,
                "command": {"name": "diagnostic.ping", "input": {"message": "ping"}},
            },
        )
        assert response.status_code == 201
        assert response.json()["status"] == "queued"
        assert client.get(f"/v1/requests/{REQUEST_ID}", headers=headers()).json() == response.json()


def test_restart_recovers_queued_request_as_daemon_shutdown(tmp_path: Path) -> None:
    payload = {
        "request_id": REQUEST_ID,
        "instance_id": INSTANCE_ID,
        "command": {"name": "diagnostic.ping", "input": {"message": "ping"}},
    }
    with TestClient(create_app(tmp_path, token=TOKEN)) as client:
        assert client.post("/v1/requests", headers=headers(), json=payload).status_code == 201

    with TestClient(create_app(tmp_path, token=TOKEN)) as restarted:
        recovered = restarted.get(f"/v1/requests/{REQUEST_ID}", headers=headers()).json()
        assert recovered["status"] == "daemon_shutdown"
        assert recovered["error"]["code"] == "daemon_shutdown"
