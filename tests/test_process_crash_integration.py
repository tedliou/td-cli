import asyncio
import socket
import sys
from pathlib import Path

import pytest
import socketio
from aiohttp import ClientSession

TOKEN = "c" * 64
INSTANCE_ID = "8cf81688-b9a4-4c39-9f92-31c77319c761"


def unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def start_daemon_process(root: Path, port: int) -> asyncio.subprocess.Process:
    script = """
import sys
from pathlib import Path
import uvicorn
from td_cli.daemon.transport import create_transport_app

uvicorn.run(
    create_transport_app(Path(sys.argv[1]), token=sys.argv[3]),
    host="127.0.0.1",
    port=int(sys.argv[2]),
    log_level="error",
)
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        str(root),
        str(port),
        TOKEN,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    async with ClientSession() as session:
        for _ in range(200):
            if process.returncode is not None:
                raise RuntimeError("Daemon subprocess exited during startup")
            try:
                response = await session.get(
                    f"http://127.0.0.1:{port}/v2/health",
                    headers={"Authorization": f"Bearer {TOKEN}"},
                )
                if response.status == 200:
                    return process
            except OSError:
                pass
            await asyncio.sleep(0.01)
    process.kill()
    await process.wait()
    raise RuntimeError("Daemon subprocess did not become healthy")


async def kill_process(process: asyncio.subprocess.Process) -> None:
    process.kill()
    await asyncio.wait_for(process.wait(), 5)


@pytest.mark.parametrize(
    ("phase", "request_id", "expected"),
    [
        ("queued", "018f47ec-7f3b-7a34-8f31-2ad70b6f6e20", "daemon_shutdown"),
        ("dispatched", "018f47ec-7f3b-7a34-8f31-2ad70b6f6e21", "daemon_shutdown"),
        ("running", "018f47ec-7f3b-7a34-8f31-2ad70b6f6e22", "unknown"),
    ],
)
@pytest.mark.asyncio
async def test_public_transport_recovers_each_abrupt_process_boundary(
    tmp_path: Path, phase: str, request_id: str, expected: str
) -> None:
    root = tmp_path / phase
    port = unused_port()
    process = await start_daemon_process(root, port)
    agent = socketio.AsyncClient(reconnection=False)
    registered: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    dispatched: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    execute: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    agent.on("registered", lambda data: registered.set_result(data))
    agent.on("request_dispatch", lambda data: dispatched.set_result(data))
    agent.on("request_execute", lambda data: execute.set_result(data))
    try:
        await agent.connect(f"http://127.0.0.1:{port}", auth={"token": TOKEN})
        await agent.emit(
            "register",
            {
                "instance_id": INSTANCE_ID,
                "protocol_versions": [2],
                "agent_version": "test-agent",
                "td_build": "2025.32050",
                "capabilities": ["ops.get"],
            },
        )
        connection = await asyncio.wait_for(registered, 2)
        if phase != "queued":
            await agent.emit("execution_sync", {**connection, "records": []})
        async with ClientSession() as session:
            response = await session.post(
                f"http://127.0.0.1:{port}/v2/requests",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={
                    "request_id": request_id,
                    "instance_id": INSTANCE_ID,
                    "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
                },
            )
            assert response.status == 201
            assert (await response.json())["status"] == "queued"
        if phase != "queued":
            dispatch = await asyncio.wait_for(dispatched, 2)
            if phase == "running":
                await agent.emit(
                    "request_accepted", {**connection, "request_id": dispatch["request_id"]}
                )
                await asyncio.wait_for(execute, 2)
        await kill_process(process)
        await agent.disconnect()

        process = await start_daemon_process(root, port)
        async with ClientSession() as session:
            response = await session.get(
                f"http://127.0.0.1:{port}/v2/requests/{request_id}",
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
            assert response.status == 200
            recovered = await response.json()
        assert recovered["status"] == expected
        if phase == "running":
            assert recovered["execution_id"] is not None
            assert recovered["error"]["code"] == "request_outcome_unknown"
    finally:
        if agent.connected:
            await agent.disconnect()
        if process.returncode is None:
            await kill_process(process)
