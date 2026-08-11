import asyncio
import socket
import threading
from pathlib import Path

import pytest
import socketio
import uvicorn
from aiohttp import ClientSession

from td_cli.daemon.transport import _normalize_command_result, create_transport_app

TOKEN = "b" * 64
INSTANCE_ID = "8cf81688-b9a4-4c39-9f92-31c77319c761"
REQUEST_ID = "018f47ec-7f3b-7a34-8f31-2ad70b6f6e2a"


def test_locked_socketio_omissions_are_restored_at_the_public_result_boundary() -> None:
    connected = _normalize_command_result(
        {"name": "ops.connect"}, {"connected": True, "replaced": False}
    )
    listed = _normalize_command_result(
        {"name": "parameters.list"},
        {
            "items": [1, {"__td_cli_null__": True}, 2],
            "parameters": [
                {
                    "name": "gain",
                    "value_kind": "number",
                    "expression": {"supported": True},
                },
                {
                    "name": "mode",
                    "value_kind": "menu",
                    "expression": {"supported": True},
                },
            ],
        },
    )

    assert connected["previous_connection"] is None
    assert listed["parameters"][0] == {
        "name": "gain",
        "value_kind": "number",
        "page": None,
        "expression": {"supported": True, "source": None},
        "menu_names": None,
        "menu_labels": None,
    }
    assert listed["parameters"][1]["menu_names"] == []
    assert listed["parameters"][1]["menu_labels"] == []
    assert listed["items"] == [1, None, 2]

    connections = _normalize_command_result(
        {"name": "ops.connections"},
        {
            "operator_path": "/project1/source",
            "inputs": [{"input_index": 0, "description": "Input"}],
            "outputs": [],
            "connection_count": 0,
        },
    )
    assert connections["inputs"][0]["connection"] is None

    copied = _normalize_command_result(
        {"name": "ops.copy"},
        {"path": "/project1/copied", "include_docked": 0},
    )
    assert copied["include_docked"] is False

    batched = _normalize_command_result(
        {
            "name": "batch.execute",
            "input": {
                "commands": [{"name": "parameters.list", "input": {"operator_path": "/project1/a"}}]
            },
        },
        {
            "results": [
                {
                    "parameters": [
                        {
                            "name": "gain",
                            "value_kind": "number",
                            "expression": {"supported": True},
                        }
                    ]
                }
            ]
        },
    )
    assert batched["results"][0]["parameters"][0]["page"] is None
    assert batched["results"][0]["parameters"][0]["menu_names"] is None


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def registration_payload() -> dict[str, object]:
    return {
        "instance_id": INSTANCE_ID,
        "protocol_versions": [1],
        "agent_version": "0.1.0",
        "td_build": "2025.32050",
        "capabilities": ["ops.get"],
    }


@pytest.mark.asyncio
async def test_incompatible_touchdesigner_build_is_rejected(tmp_path: Path) -> None:
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
    rejected: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    client.on("registration_error", lambda data: rejected.set_result(data))
    try:
        await client.connect(f"http://127.0.0.1:{port}", auth={"token": TOKEN})
        await client.emit("register", {**registration_payload(), "td_build": "2025.99999"})
        assert await asyncio.wait_for(rejected, 2) == {"code": "protocol_incompatible"}
        for _ in range(100):
            if not client.connected:
                break
            await asyncio.sleep(0.01)
        assert client.connected is False
    finally:
        if client.connected:
            await client.disconnect()
        server.should_exit = True
        thread.join(timeout=5)


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
            registration_payload(),
        )
        registration = await asyncio.wait_for(registered, 2)
        assert registration["protocol_version"] == 1
        assert registration["connection_id"] != INSTANCE_ID

        await client.emit("heartbeat", registration)
        assert (await asyncio.wait_for(heartbeat, 2))["connection_id"] == registration[
            "connection_id"
        ]
        async with ClientSession() as session:
            response = await session.get(
                f"http://127.0.0.1:{port}/v1/instances",
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
            instance = (await response.json())[0]
            assert instance["last_heartbeat_at"].endswith("Z")
            assert instance["offline_expires_at"] is None
    finally:
        if client.connected:
            await client.disconnect()
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_draining_instance_remains_visible_but_rejects_new_requests(tmp_path: Path) -> None:
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
    registration: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    heartbeat = asyncio.Event()
    client.on("registered", lambda data: registration.set_result(data))
    client.on("heartbeat_recorded", lambda _: heartbeat.set())
    try:
        await client.connect(f"http://127.0.0.1:{port}", auth={"token": TOKEN})
        await client.emit("register", registration_payload())
        registered = await asyncio.wait_for(registration, 2)
        await client.emit("heartbeat", {**registered, "status": "draining"})
        await asyncio.wait_for(heartbeat.wait(), 2)
        async with ClientSession() as session:
            headers = {"Authorization": f"Bearer {TOKEN}"}
            instances = await session.get(f"http://127.0.0.1:{port}/v1/instances", headers=headers)
            assert (await instances.json())[0]["status"] == "draining"
            rejected = await session.post(
                f"http://127.0.0.1:{port}/v1/requests",
                headers=headers,
                json={
                    "request_id": REQUEST_ID,
                    "instance_id": INSTANCE_ID,
                    "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
                },
            )
            assert rejected.status == 409
            assert (await rejected.json())["detail"] == "instance_draining"
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
                    "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
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
            registration_payload(),
        )
        await asyncio.wait_for(registered.wait(), 2)
        async with ClientSession() as session:
            response = await session.post(
                f"http://127.0.0.1:{port}/v1/requests",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={
                    "request_id": REQUEST_ID,
                    "instance_id": INSTANCE_ID,
                    "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
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
        await client.emit("register", registration_payload())
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
                        "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
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


@pytest.mark.asyncio
async def test_thirty_third_request_is_rejected_without_persistence(tmp_path: Path) -> None:
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
    client.on("registered", lambda _: registered.set())
    try:
        await client.connect(f"http://127.0.0.1:{port}", auth={"token": TOKEN})
        await client.emit("register", registration_payload())
        await asyncio.wait_for(registered.wait(), 2)
        async with ClientSession() as session:
            headers = {"Authorization": f"Bearer {TOKEN}"}
            for index in range(32):
                request_id = f"018f47ec-7f3b-7a34-8f31-{index:012d}"
                response = await session.post(
                    f"http://127.0.0.1:{port}/v1/requests",
                    headers=headers,
                    json={
                        "request_id": request_id,
                        "instance_id": INSTANCE_ID,
                        "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
                    },
                )
                assert response.status == 201
            rejected_id = "018f47ec-7f3b-7a34-8f31-999999999999"
            rejected = await session.post(
                f"http://127.0.0.1:{port}/v1/requests",
                headers=headers,
                json={
                    "request_id": rejected_id,
                    "instance_id": INSTANCE_ID,
                    "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
                },
            )
            assert rejected.status == 409
            assert (await rejected.json())["detail"] == "instance_busy"
            query = await session.get(
                f"http://127.0.0.1:{port}/v1/requests/{rejected_id}", headers=headers
            )
            assert query.status == 404
    finally:
        if client.connected:
            await client.disconnect()
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_disconnect_marks_in_flight_unknown_and_reconnect_resumes_queue(
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
    while not server.started:
        await asyncio.sleep(0.01)
    first_client = socketio.AsyncClient(reconnection=False)
    first_registered = asyncio.Event()
    first_dispatched: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    first_client.on("registered", lambda _: first_registered.set())
    first_client.on("request_dispatch", lambda data: first_dispatched.put_nowait(data))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    request_ids = [REQUEST_ID, "018f47ec-7f3b-7a34-8f31-2ad70b6f6e2c"]
    second_client = socketio.AsyncClient(reconnection=False)
    second_registered: asyncio.Future[dict[str, object]] = (
        asyncio.get_running_loop().create_future()
    )
    second_dispatched: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    second_client.on("registered", lambda data: second_registered.set_result(data))
    second_client.on("request_dispatch", lambda data: second_dispatched.put_nowait(data))
    try:
        await first_client.connect(f"http://127.0.0.1:{port}", auth={"token": TOKEN})
        await first_client.emit("register", registration_payload())
        await asyncio.wait_for(first_registered.wait(), 2)
        async with ClientSession() as session:
            for request_id in request_ids:
                response = await session.post(
                    f"http://127.0.0.1:{port}/v1/requests",
                    headers=headers,
                    json={
                        "request_id": request_id,
                        "instance_id": INSTANCE_ID,
                        "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
                    },
                )
                assert response.status == 201
            assert (await asyncio.wait_for(first_dispatched.get(), 2))["request_id"] == request_ids[
                0
            ]
            await first_client.disconnect()
            for _ in range(50):
                query = await session.get(
                    f"http://127.0.0.1:{port}/v1/requests/{request_ids[0]}", headers=headers
                )
                if (await query.json())["status"] == "unknown":
                    break
                await asyncio.sleep(0.02)
            assert (await query.json())["status"] == "unknown"

            await second_client.connect(f"http://127.0.0.1:{port}", auth={"token": TOKEN})
            await second_client.emit("register", registration_payload())
            second_registration = await asyncio.wait_for(second_registered, 2)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(second_dispatched.get(), 0.1)
            await second_client.emit(
                "results_replayed",
                second_registration,
            )
            assert (await asyncio.wait_for(second_dispatched.get(), 2))[
                "request_id"
            ] == request_ids[1]
    finally:
        if first_client.connected:
            await first_client.disconnect()
        if second_client.connected:
            await second_client.disconnect()
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_unadvertised_command_capability_is_rejected_before_fifo(tmp_path: Path) -> None:
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
    client.on("registered", lambda _: registered.set())
    try:
        await client.connect(f"http://127.0.0.1:{port}", auth={"token": TOKEN})
        payload = registration_payload()
        payload["capabilities"] = []
        await client.emit("register", payload)
        await asyncio.wait_for(registered.wait(), 2)
        async with ClientSession() as session:
            response = await session.post(
                f"http://127.0.0.1:{port}/v1/requests",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={
                    "request_id": REQUEST_ID,
                    "instance_id": INSTANCE_ID,
                    "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
                },
            )
            assert response.status == 409
            assert (await response.json())["detail"] == "command_unsupported"
    finally:
        if client.connected:
            await client.disconnect()
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_daemon_shutdown_drains_then_recovers_in_flight_and_queued_requests(
    tmp_path: Path,
) -> None:
    port = unused_port()
    shutdown_called = asyncio.Event()
    server = uvicorn.Server(
        uvicorn.Config(
            create_transport_app(
                tmp_path, token=TOKEN, drain_timeout=0.1, shutdown=shutdown_called.set
            ),
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
    dispatched = asyncio.Event()
    draining = asyncio.Event()
    client.on("registered", lambda _: registered.set())
    client.on("request_dispatch", lambda _: dispatched.set())
    client.on("daemon_draining", lambda _: draining.set())
    request_ids = [REQUEST_ID, "018f47ec-7f3b-7a34-8f31-2ad70b6f6e2d"]
    headers = {"Authorization": f"Bearer {TOKEN}"}
    late_client = socketio.AsyncClient(reconnection=False)
    late_draining = asyncio.Event()
    late_client.on("daemon_draining", lambda _: late_draining.set())
    try:
        await client.connect(f"http://127.0.0.1:{port}", auth={"token": TOKEN})
        await client.emit("register", registration_payload())
        await asyncio.wait_for(registered.wait(), 2)
        async with ClientSession() as session:
            for request_id in request_ids:
                accepted = await session.post(
                    f"http://127.0.0.1:{port}/v1/requests",
                    headers=headers,
                    json={
                        "request_id": request_id,
                        "instance_id": INSTANCE_ID,
                        "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
                    },
                )
                assert accepted.status == 201
            await asyncio.wait_for(dispatched.wait(), 2)
            stopped = await session.post(f"http://127.0.0.1:{port}/v1/shutdown", headers=headers)
            assert stopped.status == 202
            await asyncio.wait_for(draining.wait(), 2)
            await asyncio.wait_for(shutdown_called.wait(), 2)
            states = []
            for request_id in request_ids:
                response = await session.get(
                    f"http://127.0.0.1:{port}/v1/requests/{request_id}", headers=headers
                )
                states.append((await response.json())["status"])
            assert states == ["unknown", "daemon_shutdown"]
            await late_client.connect(f"http://127.0.0.1:{port}", auth={"token": TOKEN})
            late_registered = asyncio.Event()
            late_client.on("registered", lambda _: late_registered.set())
            await late_client.emit("register", registration_payload())
            await asyncio.wait_for(late_registered.wait(), 2)
            await asyncio.wait_for(late_draining.wait(), 2)
            instances = await session.get(f"http://127.0.0.1:{port}/v1/instances", headers=headers)
            assert (await instances.json())[0]["status"] == "draining"
    finally:
        if client.connected:
            await client.disconnect()
        if late_client.connected:
            await late_client.disconnect()
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_missing_application_heartbeat_makes_instance_offline(tmp_path: Path) -> None:
    port = unused_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_transport_app(tmp_path, token=TOKEN, heartbeat_timeout=0.1, offline_retention=1),
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
    client.on("registered", lambda _: registered.set())
    try:
        await client.connect(f"http://127.0.0.1:{port}", auth={"token": TOKEN})
        await client.emit("register", registration_payload())
        await asyncio.wait_for(registered.wait(), 2)
        async with ClientSession() as session:
            headers = {"Authorization": f"Bearer {TOKEN}"}
            online = await session.get(f"http://127.0.0.1:{port}/v1/instances", headers=headers)
            assert (await online.json())[0]["status"] == "online"
            instances = []
            for _ in range(50):
                observed = await session.get(
                    f"http://127.0.0.1:{port}/v1/instances", headers=headers
                )
                instances = await observed.json()
                if instances and instances[0]["status"] == "offline":
                    break
                await asyncio.sleep(0.02)
            assert instances[0]["status"] == "offline"
    finally:
        if client.connected:
            await client.disconnect()
        server.should_exit = True
        thread.join(timeout=5)
