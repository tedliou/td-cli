import asyncio
import json
import logging
import socket
import threading
from pathlib import Path

import pytest
import socketio
import uvicorn
from aiohttp import ClientSession

from td_cli.daemon.transport import _cancel_and_wait, _run_cleanup_steps, create_transport_app

TOKEN = "b" * 64
INSTANCE_ID = "8cf81688-b9a4-4c39-9f92-31c77319c761"
REQUEST_ID = "018f47ec-7f3b-7a34-8f31-2ad70b6f6e2a"


@pytest.mark.asyncio
async def test_cleanup_runs_every_step_after_owned_task_failure() -> None:
    calls: list[str] = []

    async def failed_owner() -> None:
        raise RuntimeError("deadline failed")

    async def record(name: str) -> None:
        calls.append(name)

    owner = asyncio.create_task(failed_owner())
    await asyncio.sleep(0)

    with pytest.raises(RuntimeError, match="deadline failed"):
        await _run_cleanup_steps(
            [
                lambda: _cancel_and_wait(owner),
                lambda: record("lifecycle"),
                lambda: record("effect"),
                lambda: record("sender-one"),
                lambda: record("sender-two"),
            ]
        )

    assert calls == ["lifecycle", "effect", "sender-one", "sender-two"]


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def registration_payload() -> dict[str, object]:
    return {
        "instance_id": INSTANCE_ID,
        "protocol_versions": [2],
        "agent_version": "0.3.1",
        "td_build": "2025.32050",
        "capabilities": ["ops.get"],
    }


async def start_server(app) -> tuple[uvicorn.Server, threading.Thread, int]:
    port = unused_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            return server, thread, port
        await asyncio.sleep(0.01)
    raise RuntimeError("test server did not start")


def stop_server(server: uvicorn.Server, thread: threading.Thread) -> None:
    server.should_exit = True
    thread.join(timeout=5)


async def register(
    client: socketio.AsyncClient, port: int, *, synchronize: bool = True
) -> dict[str, object]:
    registered: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    client.on("registered", lambda data: registered.set_result(data))
    await client.connect(f"http://127.0.0.1:{port}", auth={"token": TOKEN})
    await client.emit("register", registration_payload())
    connection = await asyncio.wait_for(registered, 2)
    if synchronize:
        await client.emit("execution_sync", {**connection, "records": []})
    return connection


async def get_json(port: int, path: str) -> tuple[int, object]:
    async with ClientSession() as session:
        response = await session.get(
            f"http://127.0.0.1:{port}{path}",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        return response.status, await response.json()


@pytest.mark.asyncio
async def test_authentication_and_registration_fail_closed(tmp_path: Path) -> None:
    server, thread, port = await start_server(create_transport_app(tmp_path, token=TOKEN))
    unauthenticated = socketio.AsyncClient(reconnection=False)
    incompatible = socketio.AsyncClient(reconnection=False)
    registration_error: asyncio.Future[dict[str, object]] = (
        asyncio.get_running_loop().create_future()
    )
    incompatible.on("registration_error", lambda data: registration_error.set_result(data))
    try:
        with pytest.raises(socketio.exceptions.ConnectionError):
            await unauthenticated.connect(f"http://127.0.0.1:{port}", auth={"token": "wrong"})
        await incompatible.connect(f"http://127.0.0.1:{port}", auth={"token": TOKEN})
        await incompatible.emit("register", {**registration_payload(), "td_build": "2025.99999"})
        assert (await asyncio.wait_for(registration_error, 2))["code"] == "protocol_incompatible"
    finally:
        if incompatible.connected:
            await incompatible.disconnect()
        stop_server(server, thread)


@pytest.mark.asyncio
async def test_registration_stays_synchronizing_until_agent_replay(tmp_path: Path) -> None:
    server, thread, port = await start_server(create_transport_app(tmp_path, token=TOKEN))
    client = socketio.AsyncClient(reconnection=False)
    try:
        connection = await register(client, port, synchronize=False)
        _, before = await get_json(port, "/v2/instances")
        assert before[0]["status"] == "synchronizing"
        await client.emit("execution_sync", {**connection, "records": []})
        for _ in range(50):
            _, after = await get_json(port, "/v2/instances")
            if after[0]["status"] == "online":
                break
            await asyncio.sleep(0.01)
        assert after[0]["status"] == "online"
    finally:
        if client.connected:
            await client.disconnect()
        stop_server(server, thread)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome_status", "outcome_result", "outcome_error"),
    [
        ("succeeded", {"path": "/secret-path"}, None),
        (
            "failed",
            None,
            {
                "code": "operator_not_found",
                "message": "operator_not_found",
                "details": {},
                "retryable": False,
            },
        ),
    ],
)
async def test_full_v2_handshake_is_ordered_durable_and_redacted(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    outcome_status: str,
    outcome_result: object,
    outcome_error: object,
) -> None:
    caplog.set_level(logging.INFO)
    server, thread, port = await start_server(create_transport_app(tmp_path, token=TOKEN))
    client = socketio.AsyncClient(reconnection=False)
    dispatched: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    execute: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    recorded: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    order = []
    client.on(
        "request_dispatch", lambda data: (order.append("dispatch"), dispatched.set_result(data))
    )
    client.on("request_execute", lambda data: (order.append("execute"), execute.set_result(data)))
    client.on(
        "outcome_recorded", lambda data: (order.append("recorded"), recorded.set_result(data))
    )
    try:
        connection = await register(client, port)
        async with ClientSession() as session:
            response = await session.post(
                f"http://127.0.0.1:{port}/v2/requests",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={
                    "request_id": REQUEST_ID,
                    "instance_id": INSTANCE_ID,
                    "command": {"name": "ops.get", "input": {"operator_path": "/secret-path"}},
                },
            )
            assert response.status == 201
        request = await asyncio.wait_for(dispatched, 2)
        assert request["status"] == "dispatched"
        await client.emit("request_accepted", {**connection, "request_id": REQUEST_ID})
        authorization = await asyncio.wait_for(execute, 2)
        assert authorization["execution_id"]
        outcome = {
            **connection,
            "request_id": REQUEST_ID,
            "execution_id": authorization["execution_id"],
            "status": outcome_status,
            "result": outcome_result,
            "error": outcome_error,
        }
        await client.emit(
            "request_outcome_chunk",
            {
                **connection,
                "request_id": REQUEST_ID,
                "execution_id": authorization["execution_id"],
                "chunk_index": 0,
                "chunk_count": 1,
                "payload": json.dumps(outcome, separators=(",", ":"), sort_keys=True),
            },
        )
        acknowledgment = await asyncio.wait_for(recorded, 2)
        assert acknowledgment["execution_id"] == authorization["execution_id"]
        assert order == ["dispatch", "execute", "recorded"]
        _, snapshot = await get_json(port, f"/v2/requests/{REQUEST_ID}")
        assert snapshot["status"] == outcome_status
        assert snapshot["result"] == outcome_result
        assert snapshot["error"] == outcome_error
        serialized = "\n".join(record.getMessage() for record in caplog.records)
        assert TOKEN not in serialized
        assert "/secret-path" not in serialized
        assert '"result"' not in serialized
    finally:
        if client.connected:
            await client.disconnect()
        stop_server(server, thread)


@pytest.mark.asyncio
async def test_reconnect_reconciles_matching_retained_outcome(tmp_path: Path) -> None:
    server, thread, port = await start_server(create_transport_app(tmp_path, token=TOKEN))
    first = socketio.AsyncClient(reconnection=False)
    dispatched: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    execute: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    first.on("request_dispatch", lambda data: dispatched.set_result(data))
    first.on("request_execute", lambda data: execute.set_result(data))
    second = socketio.AsyncClient(reconnection=False)
    recorded: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    second.on("outcome_recorded", lambda data: recorded.set_result(data))
    try:
        first_connection = await register(first, port)
        async with ClientSession() as session:
            response = await session.post(
                f"http://127.0.0.1:{port}/v2/requests",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={
                    "request_id": REQUEST_ID,
                    "instance_id": INSTANCE_ID,
                    "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
                },
            )
            assert response.status == 201
        await asyncio.wait_for(dispatched, 2)
        await first.emit("request_accepted", {**first_connection, "request_id": REQUEST_ID})
        authorization = await asyncio.wait_for(execute, 2)
        await first.disconnect()
        for _ in range(50):
            _, unknown = await get_json(port, f"/v2/requests/{REQUEST_ID}")
            if unknown["status"] == "unknown":
                break
            await asyncio.sleep(0.01)
        assert unknown["status"] == "unknown"

        second_connection = await register(second, port, synchronize=False)
        await second.emit(
            "execution_sync",
            {
                **second_connection,
                "records": [
                    {
                        "phase": "outcome",
                        "request_id": REQUEST_ID,
                        "execution_id": authorization["execution_id"],
                        "status": "succeeded",
                        "result": {"path": "/project1"},
                        "error": None,
                    }
                ],
            },
        )
        await asyncio.wait_for(recorded, 2)
        _, succeeded = await get_json(port, f"/v2/requests/{REQUEST_ID}")
        assert succeeded["status"] == "succeeded"
    finally:
        for client in (first, second):
            if client.connected:
                await client.disconnect()
        stop_server(server, thread)


@pytest.mark.asyncio
async def test_reconnect_reassembles_bounded_outcome_chunks_before_synchronizing(
    tmp_path: Path,
) -> None:
    server, thread, port = await start_server(create_transport_app(tmp_path, token=TOKEN))
    first = socketio.AsyncClient(reconnection=False)
    second = socketio.AsyncClient(reconnection=False)
    dispatched: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    execute: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    recorded: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    first.on("request_dispatch", lambda data: dispatched.set_result(data))
    first.on("request_execute", lambda data: execute.set_result(data))
    second.on("outcome_recorded", lambda data: recorded.set_result(data))
    try:
        first_connection = await register(first, port)
        async with ClientSession() as session:
            response = await session.post(
                f"http://127.0.0.1:{port}/v2/requests",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={
                    "request_id": REQUEST_ID,
                    "instance_id": INSTANCE_ID,
                    "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
                },
            )
            assert response.status == 201
        await asyncio.wait_for(dispatched, 2)
        await first.emit("request_accepted", {**first_connection, "request_id": REQUEST_ID})
        authorization = await asyncio.wait_for(execute, 2)
        await first.disconnect()

        second_connection = await register(second, port, synchronize=False)
        outcome = {
            **second_connection,
            "request_id": REQUEST_ID,
            "execution_id": authorization["execution_id"],
            "status": "succeeded",
            "result": {"payload": "x" * 70_000},
            "error": None,
        }
        encoded = json.dumps(outcome, separators=(",", ":"), sort_keys=True)
        size = 24 * 1024
        payloads = [encoded[offset : offset + size] for offset in range(0, len(encoded), size)]
        for index, payload in enumerate(payloads):
            await second.emit(
                "request_outcome_chunk",
                {
                    **second_connection,
                    "request_id": REQUEST_ID,
                    "execution_id": authorization["execution_id"],
                    "chunk_index": index,
                    "chunk_count": len(payloads),
                    "payload": payload,
                },
            )
        await second.emit("execution_sync", {**second_connection, "records": []})

        await asyncio.wait_for(recorded, 2)
        _, succeeded = await get_json(port, f"/v2/requests/{REQUEST_ID}")
        assert succeeded["status"] == "succeeded"
        assert succeeded["result"] == {"payload": "x" * 70_000}
        _, instances = await get_json(port, "/v2/instances")
        assert instances[0]["status"] == "online"
    finally:
        for client in (first, second):
            if client.connected:
                await client.disconnect()
        stop_server(server, thread)


@pytest.mark.asyncio
async def test_replacement_disconnects_old_sender_before_new_generation_dispatch(
    tmp_path: Path,
) -> None:
    server, thread, port = await start_server(create_transport_app(tmp_path, token=TOKEN))
    first = socketio.AsyncClient(reconnection=False)
    second = socketio.AsyncClient(reconnection=False)
    dispatched: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    second.on("request_dispatch", lambda data: dispatched.set_result(data))
    try:
        await register(first, port)
        await register(second, port)
        for _ in range(100):
            if not first.connected:
                break
            await asyncio.sleep(0.01)
        assert first.connected is False
        async with ClientSession() as session:
            response = await session.post(
                f"http://127.0.0.1:{port}/v2/requests",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={
                    "request_id": REQUEST_ID,
                    "instance_id": INSTANCE_ID,
                    "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
                },
            )
            assert response.status == 201
        assert (await asyncio.wait_for(dispatched, 2))["request_id"] == REQUEST_ID
    finally:
        for client in (first, second):
            if client.connected:
                await client.disconnect()
        stop_server(server, thread)


@pytest.mark.asyncio
async def test_draining_and_offline_states_control_admission(tmp_path: Path) -> None:
    server, thread, port = await start_server(
        create_transport_app(tmp_path, token=TOKEN, heartbeat_timeout=0.1, offline_retention=1.0)
    )
    client = socketio.AsyncClient(reconnection=False)
    heartbeat = asyncio.Event()
    client.on("heartbeat_recorded", lambda _: heartbeat.set())
    try:
        connection = await register(client, port)
        await client.emit("heartbeat", {**connection, "status": "draining"})
        await asyncio.wait_for(heartbeat.wait(), 2)
        async with ClientSession() as session:
            rejected = await session.post(
                f"http://127.0.0.1:{port}/v2/requests",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={
                    "request_id": REQUEST_ID,
                    "instance_id": INSTANCE_ID,
                    "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
                },
            )
            assert rejected.status == 409
            assert (await rejected.json())["detail"] == "instance_draining"
        for _ in range(100):
            if not client.connected:
                break
            await asyncio.sleep(0.01)
        assert client.connected is False
        _, offline = await get_json(port, "/v2/instances")
        assert offline[0]["status"] == "offline"
        await asyncio.sleep(1.1)
        _, expired = await get_json(port, "/v2/instances")
        assert expired == []
    finally:
        if client.connected:
            await client.disconnect()
        stop_server(server, thread)


@pytest.mark.asyncio
async def test_sender_failure_degrades_health_without_logging_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    original_emit = socketio.AsyncServer.emit

    async def fail_registered(self, event, *args, **kwargs):
        if event == "registered":
            raise RuntimeError("sender failed")
        return await original_emit(self, event, *args, **kwargs)

    monkeypatch.setattr(socketio.AsyncServer, "emit", fail_registered)
    caplog.set_level(logging.INFO, logger="td_cli.lifecycle")
    server, thread, port = await start_server(create_transport_app(tmp_path, token=TOKEN))
    client = socketio.AsyncClient(reconnection=False)
    try:
        await client.connect(f"http://127.0.0.1:{port}", auth={"token": TOKEN})
        await client.emit("register", registration_payload())
        for _ in range(100):
            _, health = await get_json(port, "/v2/health")
            if health["ready"] is False:
                break
            await asyncio.sleep(0.01)
        assert health["ready"] is False
        serialized = "\n".join(record.getMessage() for record in caplog.records)
        assert TOKEN not in serialized
        assert "/secret-path" not in serialized
        assert "result" not in serialized
    finally:
        if client.connected:
            await client.disconnect()
        stop_server(server, thread)


@pytest.mark.asyncio
async def test_sender_saturation_degrades_health_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_emit = socketio.AsyncServer.emit
    gate = asyncio.Event()

    async def block_heartbeat(self, event, *args, **kwargs):
        if event == "heartbeat_recorded":
            await gate.wait()
        return await original_emit(self, event, *args, **kwargs)

    monkeypatch.setattr(socketio.AsyncServer, "emit", block_heartbeat)
    server, thread, port = await start_server(
        create_transport_app(tmp_path, token=TOKEN, outbound_capacity=1)
    )
    client = socketio.AsyncClient(reconnection=False)
    try:
        connection = await register(client, port)
        for _ in range(8):
            await client.emit("heartbeat", connection)
        for _ in range(100):
            _, health = await get_json(port, "/v2/health")
            if health["ready"] is False:
                break
            await asyncio.sleep(0.01)
        assert health["ready"] is False
    finally:
        gate.set()
        if client.connected:
            await client.disconnect()
        stop_server(server, thread)
