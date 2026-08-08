import asyncio
import socket
import threading
from pathlib import Path

import pytest
import socketio
import uvicorn

from td_cli.daemon.transport import create_transport_app

TOKEN = "b" * 64
INSTANCE_ID = "8cf81688-b9a4-4c39-9f92-31c77319c761"


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
