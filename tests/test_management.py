import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from td_cli.daemon.app import create_app

TOKEN = "a" * 64
INSTANCE_ID = "8cf81688-b9a4-4c39-9f92-31c77319c761"
REQUEST_ID = "018f47ec-7f3b-7a34-8f31-2ad70b6f6e2a"


def headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_health_discloses_nothing_without_authentication(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path, token=TOKEN)) as client:
        assert client.get("/v1/health").status_code == 404
        response = client.get("/v2/health")
        assert response.status_code == 404
        assert response.json() == {"detail": "Not Found"}


def test_authenticated_health_reports_runtime_logging_failure(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path, token=TOKEN, runtime_health=lambda: False)) as client:
        response = client.get("/v2/health", headers=headers())
        assert response.status_code == 200
        assert response.json()["ready"] is False
        assert response.json()["logging_healthy"] is False
        assert response.json()["protocol_versions"] == [2]


def test_request_identity_includes_touchdesigner_instance(tmp_path: Path) -> None:
    payload = {
        "request_id": REQUEST_ID,
        "instance_id": INSTANCE_ID,
        "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
    }
    with TestClient(create_app(tmp_path, token=TOKEN)) as client:
        assert client.post("/v2/requests", headers=headers(), json=payload).status_code == 201
        conflict = client.post(
            "/v2/requests",
            headers=headers(),
            json={**payload, "instance_id": "4b5fd041-06ed-4c3f-b761-173530d99589"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "request_id_conflict"


def test_request_is_accepted_durably_and_queryable(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path, token=TOKEN)) as client:
        response = client.post(
            "/v2/requests",
            headers=headers(),
            json={
                "request_id": REQUEST_ID,
                "instance_id": INSTANCE_ID,
                "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
            },
        )
        assert response.status_code == 201
        assert response.json()["status"] == "queued"
        assert client.get(f"/v2/requests/{REQUEST_ID}", headers=headers()).json() == response.json()


def test_restart_recovers_queued_request_as_daemon_shutdown(tmp_path: Path) -> None:
    payload = {
        "request_id": REQUEST_ID,
        "instance_id": INSTANCE_ID,
        "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
    }
    with TestClient(create_app(tmp_path, token=TOKEN)) as client:
        assert client.post("/v2/requests", headers=headers(), json=payload).status_code == 201

    with TestClient(create_app(tmp_path, token=TOKEN)) as restarted:
        recovered = restarted.get(f"/v2/requests/{REQUEST_ID}", headers=headers()).json()
        assert recovered["status"] == "daemon_shutdown"
        assert recovered["error"]["code"] == "daemon_shutdown"
        assert recovered["completed_at"].endswith("Z")


def test_request_id_deduplicates_same_command_and_rejects_different_command(tmp_path: Path) -> None:
    payload = {
        "request_id": REQUEST_ID,
        "instance_id": INSTANCE_ID,
        "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
    }
    with TestClient(create_app(tmp_path, token=TOKEN)) as client:
        first = client.post("/v2/requests", headers=headers(), json=payload)
        duplicate = client.post("/v2/requests", headers=headers(), json=payload)
        assert duplicate.status_code == 200
        assert duplicate.json() == first.json()

        changed = {**payload, "command": {"name": "ops.get", "input": {"operator_path": "/other"}}}
        conflict = client.post("/v2/requests", headers=headers(), json=changed)
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "request_id_conflict"


def test_submission_requires_uuid7_request_id(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path, token=TOKEN)) as client:
        response = client.post(
            "/v2/requests",
            headers=headers(),
            json={
                "request_id": "8cf81688-b9a4-4c39-9f92-31c77319c761",
                "instance_id": INSTANCE_ID,
                "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
            },
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_cancelled_http_admission_does_not_create_partial_request(tmp_path: Path) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_admission(snapshot):
        del snapshot
        entered.set()
        await release.wait()
        raise AssertionError("cancelled admission resumed")

    app = create_app(tmp_path, token=TOKEN, admit=blocked_admission)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        submission = asyncio.create_task(
            client.post(
                "/v2/requests",
                headers=headers(),
                json={
                    "request_id": REQUEST_ID,
                    "instance_id": INSTANCE_ID,
                    "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
                },
            )
        )
        await entered.wait()
        submission.cancel()
        with pytest.raises(asyncio.CancelledError):
            await submission
        assert await app.state.request_store.get(REQUEST_ID) is None
