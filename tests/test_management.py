import asyncio
import json
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
        response = client.get("/v3/health")
        assert response.status_code == 404
        assert response.json() == {"detail": "Not Found"}


def test_authenticated_health_reports_runtime_logging_failure(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path, token=TOKEN, runtime_health=lambda: False)) as client:
        response = client.get("/v3/health", headers=headers())
        assert response.status_code == 200
        assert response.json()["ready"] is False
        assert response.json()["logging_healthy"] is False
        assert response.json()["protocol_versions"] == [3]


def test_request_identity_includes_touchdesigner_instance(tmp_path: Path) -> None:
    payload = {
        "request_id": REQUEST_ID,
        "instance_id": INSTANCE_ID,
        "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
    }
    with TestClient(create_app(tmp_path, token=TOKEN)) as client:
        assert client.post("/v3/requests", headers=headers(), json=payload).status_code == 201
        conflict = client.post(
            "/v3/requests",
            headers=headers(),
            json={**payload, "instance_id": "4b5fd041-06ed-4c3f-b761-173530d99589"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "request_id_conflict"


def test_request_is_accepted_durably_and_queryable(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path, token=TOKEN)) as client:
        response = client.post(
            "/v3/requests",
            headers=headers(),
            json={
                "request_id": REQUEST_ID,
                "instance_id": INSTANCE_ID,
                "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
            },
        )
        assert response.status_code == 201
        assert response.json()["status"] == "queued"
        assert client.get(f"/v3/requests/{REQUEST_ID}", headers=headers()).json() == response.json()


def test_restart_recovers_queued_request_as_daemon_shutdown(tmp_path: Path) -> None:
    payload = {
        "request_id": REQUEST_ID,
        "instance_id": INSTANCE_ID,
        "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
    }
    with TestClient(create_app(tmp_path, token=TOKEN)) as client:
        assert client.post("/v3/requests", headers=headers(), json=payload).status_code == 201

    with TestClient(create_app(tmp_path, token=TOKEN)) as restarted:
        recovered = restarted.get(f"/v3/requests/{REQUEST_ID}", headers=headers()).json()
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
        first = client.post("/v3/requests", headers=headers(), json=payload)
        duplicate = client.post("/v3/requests", headers=headers(), json=payload)
        assert duplicate.status_code == 200
        assert duplicate.json() == first.json()

        changed = {**payload, "command": {"name": "ops.get", "input": {"operator_path": "/other"}}}
        conflict = client.post("/v3/requests", headers=headers(), json=changed)
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "request_id_conflict"


def test_submission_requires_uuid7_request_id(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path, token=TOKEN)) as client:
        response = client.post(
            "/v3/requests",
            headers=headers(),
            json={
                "request_id": "8cf81688-b9a4-4c39-9f92-31c77319c761",
                "instance_id": INSTANCE_ID,
                "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
            },
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_cancelled_http_admission_completes_persistence_after_lifecycle_admission(
    tmp_path: Path,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_admission(snapshot):
        entered.set()
        await release.wait()
        return await app.state.request_store.create_or_get(snapshot)

    app = create_app(tmp_path, token=TOKEN, admit=blocked_admission)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        submission = asyncio.create_task(
            client.post(
                "/v3/requests",
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
        release.set()
        for _ in range(50):
            persisted = await app.state.request_store.get(REQUEST_ID)
            if persisted is not None:
                break
            await asyncio.sleep(0.01)
        assert persisted is not None
        assert persisted["status"] == "queued"


SUBMISSION = {
    "request_id": REQUEST_ID,
    "instance_id": INSTANCE_ID,
    "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
}


@pytest.mark.asyncio
async def test_waiting_query_answers_when_the_request_settles(tmp_path: Path) -> None:
    app = create_app(tmp_path, token=TOKEN)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.post("/v3/requests", headers=headers(), json=SUBMISSION)).is_success
        store = app.state.request_store
        reads: asyncio.Queue[None] = asyncio.Queue()
        read = store.get

        async def counted(request_id: str) -> dict[str, object] | None:
            reads.put_nowait(None)  # the query reads only after registering as a waiter
            return await read(request_id)

        store.get = counted
        query = asyncio.create_task(
            client.get(f"/v3/requests/{REQUEST_ID}?wait_ms=10000", headers=headers())
        )
        await asyncio.wait_for(reads.get(), 1)
        await store.compare_and_set(
            REQUEST_ID, expected_statuses={"queued"}, changes={"status": "failed"}
        )
        response = await asyncio.wait_for(query, 1)
        assert response.json()["status"] == "failed"

        expired = await client.get(f"/v3/requests/{REQUEST_ID}?wait_ms=0", headers=headers())
        assert expired.json()["status"] == "failed"
        missing = await client.get(
            "/v3/requests/018f47ec-7f3b-7a34-8f31-000000000000?wait_ms=50", headers=headers()
        )
        assert missing.status_code == 404
        assert missing.json()["detail"] == "request_not_found"


@pytest.mark.parametrize("wait_ms", ["-1", "25001", "soon"])
def test_waiting_query_rejects_a_wait_outside_its_bound(tmp_path: Path, wait_ms: str) -> None:
    with TestClient(create_app(tmp_path, token=TOKEN)) as client:
        client.post("/v3/requests", headers=headers(), json=SUBMISSION)
        response = client.get(f"/v3/requests/{REQUEST_ID}?wait_ms={wait_ms}", headers=headers())
        assert response.status_code == 422


def test_waiting_query_returns_the_current_snapshot_when_the_wait_ends(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path, token=TOKEN)) as client:
        client.post("/v3/requests", headers=headers(), json=SUBMISSION)
        response = client.get(f"/v3/requests/{REQUEST_ID}?wait_ms=50", headers=headers())
        assert response.status_code == 200
        assert response.json()["status"] == "queued"


OTHER_ID = "4b5fd041-06ed-4c3f-b761-173530d99589"


def instance(instance_id: str, selector: str, status: str = "online") -> dict[str, object]:
    return {"instance_id": instance_id, "selector": selector, "status": status}


def selecting_app(tmp_path: Path, *items: dict[str, object]):
    return create_app(tmp_path, token=TOKEN, instances=lambda: list(items))


def test_submission_by_selector_resolves_the_instance_before_admission(tmp_path: Path) -> None:
    command = {"name": "ops.get", "input": {"operator_path": "/project1"}}
    with TestClient(
        selecting_app(tmp_path, instance(INSTANCE_ID, "8cf8"), instance(OTHER_ID, "4b5f"))
    ) as client:
        response = client.post(
            "/v3/requests",
            headers=headers(),
            json={"request_id": REQUEST_ID, "instance_selector": "4b", "command": command},
        )
        assert response.status_code == 201
        assert response.json()["instance_id"] == OTHER_ID
        both = client.post(
            "/v3/requests",
            headers=headers(),
            json={
                "request_id": REQUEST_ID,
                "instance_id": OTHER_ID,
                "instance_selector": "4b",
                "command": command,
            },
        )
        assert both.status_code == 422
        assert both.json()["detail"] == "invalid_arguments"


def test_submission_without_an_instance_takes_the_only_online_one(tmp_path: Path) -> None:
    command = {"name": "ops.get", "input": {"operator_path": "/project1"}}
    with TestClient(
        selecting_app(
            tmp_path, instance(INSTANCE_ID, "8cf8"), instance(OTHER_ID, "4b5f", "offline")
        )
    ) as client:
        response = client.post(
            "/v3/requests", headers=headers(), json={"request_id": REQUEST_ID, "command": command}
        )
        assert response.status_code == 201
        assert response.json()["instance_id"] == INSTANCE_ID


@pytest.mark.parametrize(
    ("selector", "code"),
    [
        (None, "instance_selector_ambiguous"),
        ("ff", "instance_not_found"),
        ("4b", "instance_draining"),
        ("8c", "instance_offline"),
    ],
)
def test_submission_by_selector_reports_why_no_instance_was_chosen(
    tmp_path: Path, selector: str | None, code: str
) -> None:
    items = [instance(INSTANCE_ID, "8cf8", "offline"), instance(OTHER_ID, "4b5f", "draining")]
    if selector is None:
        items = [instance(INSTANCE_ID, "8cf8"), instance(OTHER_ID, "4b5f")]
    body = {
        "request_id": REQUEST_ID,
        "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
        **({} if selector is None else {"instance_selector": selector}),
    }
    with TestClient(selecting_app(tmp_path, *items)) as client:
        response = client.post("/v3/requests", headers=headers(), json=body)
        assert response.json()["detail"] == code
        assert response.status_code == (404 if code == "instance_not_found" else 409)
        assert client.get(f"/v3/requests/{REQUEST_ID}", headers=headers()).status_code == 404


def test_invalid_command_is_reported_with_details_before_any_instance_error(
    tmp_path: Path,
) -> None:
    body = {
        "request_id": REQUEST_ID,
        "instance_selector": "missing",
        "command": {"name": "ops.get", "input": {"operator_path": 7}},
    }
    with TestClient(selecting_app(tmp_path)) as client:
        response = client.post("/v3/requests", headers=headers(), json=body)
        assert response.status_code == 422
        payload = response.json()
        assert payload["detail"] == "invalid_arguments"
        errors = payload["details"]["validation_errors"]
        assert errors[0]["location"] == ["input", "operator_path"]
        assert payload["details"]["validation_errors_truncated"] is False
        for broken in (b"not json", b"[]"):
            response = client.post("/v3/requests", headers=headers(), content=broken)
            assert (response.status_code, response.json()["detail"]) == (422, "invalid_arguments")


def test_instance_resolution_applies_the_selector_rule(tmp_path: Path) -> None:
    with TestClient(
        selecting_app(
            tmp_path, instance(INSTANCE_ID, "8cf8", "offline"), instance(OTHER_ID, "4b5f")
        )
    ) as client:

        def resolve(**query: str) -> tuple[int, object]:
            response = client.get("/v3/instances/resolve", headers=headers(), params=query)
            return response.status_code, response.json()

        assert resolve(selector="8c") == (200, instance(INSTANCE_ID, "8cf8", "offline"))
        assert resolve(selector="8c", online_only="true") == (409, {"detail": "instance_offline"})
        assert resolve(online_only="true") == (200, instance(OTHER_ID, "4b5f"))
        assert resolve(selector="") == (409, {"detail": "instance_selector_ambiguous"})
        assert resolve(selector="zz") == (404, {"detail": "instance_not_found"})


def test_plan_validation_checks_the_whole_document(tmp_path: Path) -> None:
    get = {"name": "ops.get", "input": {"operator_path": "/project1"}}
    with TestClient(create_app(tmp_path, token=TOKEN)) as client:

        def validate(document: bytes) -> tuple[int, dict[str, object]]:
            response = client.post(
                "/v3/command-plans/validate", headers=headers(), content=document
            )
            return response.status_code, response.json()

        status, valid = validate(json.dumps({"commands": [get, get]}).encode())
        assert status == 200
        assert valid == {"commands": [get, get]}
        status, invalid = validate(
            json.dumps({"commands": [get, {"name": "ops.get", "input": {}}]}).encode()
        )
        assert status == 422
        assert invalid["detail"] == "invalid_arguments"
        assert invalid["details"]["validation_errors"][0]["location"][:2] == ["commands", 1]
        nested = {"name": "batch.execute", "input": {"commands": [get]}}
        assert validate(json.dumps({"commands": [nested]}).encode())[0] == 422
        assert validate(b"\xef\xbb\xbf{}")[1]["detail"] == "invalid_arguments"
        assert validate(b" " * (1024 * 1024 + 1))[1]["detail"] == "invalid_arguments"
