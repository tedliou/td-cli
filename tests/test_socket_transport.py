import asyncio
import socket
import threading
from pathlib import Path

import pytest
import socketio
import uvicorn
from aiohttp import ClientSession

from td_cli.daemon.transport import create_transport_app

TOKEN = "b" * 64
INSTANCE_ID = "8cf81688-b9a4-4c39-9f92-31c77319c761"
REQUEST_ID = "018f47ec-7f3b-7a34-8f31-2ad70b6f6e2a"


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.asyncio
async def test_agent_authenticates_registers_and_heartbeats_with_connection_generation(
    tmp_path: Path,
) -> None:
    port = unused_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_transport_app(tmp_path, token=TOKEN),
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.01)

    client = socketio.AsyncClient(reconnection=False)
    registered: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    heartbeat: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    client.on("registered", lambda data: registered.set_result(data))
    client.on("heartbeat_recorded", lambda data: heartbeat.set_result(data))
    try:
        await client.connect(f"http://127.0.0.1:{port}", auth={"token": TOKEN})
        await client.emit(
            "register",
            {"instance_id": INSTANCE_ID, "protocol_versions": [1], "agent_version": "0.1.0"},
        )
        registration = await asyncio.wait_for(registered, 2)
        assert registration["protocol_version"] == 1
        assert registration["connection_id"] != INSTANCE_ID

        await client.emit("heartbeat", registration)
        assert (await asyncio.wait_for(heartbeat, 2))["connection_id"] == registration[
            "connection_id"
        ]
    finally:
        if client.connected:
            await client.disconnect()
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_agent_with_wrong_token_is_rejected(tmp_path: Path) -> None:
    port = unused_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_transport_app(tmp_path, token=TOKEN),
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.01)
    client = socketio.AsyncClient(reconnection=False)
    try:
        with pytest.raises(socketio.exceptions.ConnectionError):
            await client.connect(f"http://127.0.0.1:{port}", auth={"token": "wrong"})
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_request_for_offline_instance_is_rejected_before_acceptance(tmp_path: Path) -> None:
    port = unused_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_transport_app(tmp_path, token=TOKEN),
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        async with ClientSession() as session:
            response = await session.post(
                f"http://127.0.0.1:{port}/v1/requests",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={
                    "request_id": REQUEST_ID,
                    "instance_id": INSTANCE_ID,
                    "command": {"name": "diagnostic.ping", "input": {"message": "ping"}},
                },
            )
            assert response.status == 409
            assert (await response.json())["detail"] == "instance_offline"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_durable_request_dispatches_and_result_is_acknowledged(tmp_path: Path) -> None:
    port = unused_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_transport_app(tmp_path, token=TOKEN),
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.01)

    client = socketio.AsyncClient(reconnection=False)
    registered = asyncio.Event()
    result_recorded: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()

    async def on_registered(data: dict[str, object]) -> None:
        client.connection_id = data["connection_id"]  # type: ignore[attr-defined]
        registered.set()

    async def on_request(request: dict[str, object]) -> None:
        envelope = {
            "request_id": request["request_id"],
            "instance_id": INSTANCE_ID,
            "connection_id": client.connection_id,  # type: ignore[attr-defined]
        }
        await client.emit("request_accepted", envelope)
        await client.emit("request_result", {**envelope, "result": {"message": "pong"}})

    client.on("registered", on_registered)
    client.on("request_dispatch", on_request)
    client.on("result_recorded", lambda data: result_recorded.set_result(data))
    try:
        await client.connect(f"http://127.0.0.1:{port}", auth={"token": TOKEN})
        await client.emit(
            "register",
            {"instance_id": INSTANCE_ID, "protocol_versions": [1], "agent_version": "0.1.0"},
        )
        await asyncio.wait_for(registered.wait(), 2)
        async with ClientSession() as session:
            response = await session.post(
                f"http://127.0.0.1:{port}/v1/requests",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={
                    "request_id": REQUEST_ID,
                    "instance_id": INSTANCE_ID,
                    "command": {"name": "diagnostic.ping", "input": {"message": "ping"}},
                },
            )
            assert response.status == 201
            await asyncio.wait_for(result_recorded, 2)
            query = await session.get(
                f"http://127.0.0.1:{port}/v1/requests/{REQUEST_ID}",
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
            snapshot = await query.json()
            assert snapshot["status"] == "succeeded"
            assert snapshot["result"] == {"message": "pong"}
    finally:
        if client.connected:
            await client.disconnect()
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_requests_dispatch_one_at_a_time_in_fifo_order(tmp_path: Path) -> None:
    port = unused_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_transport_app(tmp_path, token=TOKEN),
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        await asyncio.sleep(0.01)
    client = socketio.AsyncClient(reconnection=False)
    registered = asyncio.Event()
    dispatched: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    connection_id = ""

    async def on_registered(data: dict[str, object]) -> None:
        nonlocal connection_id
        connection_id = str(data["connection_id"])
        registered.set()

    client.on("registered", on_registered)
    client.on("request_dispatch", lambda data: dispatched.put_nowait(data))
    try:
        await client.connect(f"http://127.0.0.1:{port}", auth={"token": TOKEN})
        await client.emit("register", {"instance_id": INSTANCE_ID, "protocol_versions": [1]})
        await asyncio.wait_for(registered.wait(), 2)
        async with ClientSession() as session:
            request_ids = [REQUEST_ID, "018f47ec-7f3b-7a34-8f31-2ad70b6f6e2b"]
            for request_id in request_ids:
                response = await session.post(
                    f"http://127.0.0.1:{port}/v1/requests",
                    headers={"Authorization": f"Bearer {TOKEN}"},
                    json={
                        "request_id": request_id,
                        "instance_id": INSTANCE_ID,
                        "command": {"name": "diagnostic.ping", "input": {"message": request_id}},
                    },
                )
                assert response.status == 201
            first = await asyncio.wait_for(dispatched.get(), 2)
            assert first["request_id"] == request_ids[0]
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(dispatched.get(), 0.1)
            await client.emit(
                "request_result",
                {
                    "request_id": request_ids[0],
                    "instance_id": INSTANCE_ID,
                    "connection_id": connection_id,
                    "result": {"message": "done"},
                },
            )
            second = await asyncio.wait_for(dispatched.get(), 2)
            assert second["request_id"] == request_ids[1]
    finally:
        if client.connected:
            await client.disconnect()
        server.should_exit = True
        thread.join(timeout=5)
