import asyncio
import json
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
                    f"http://127.0.0.1:{port}/v3/health",
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


async def register_agent(
    port: int,
) -> tuple[socketio.AsyncClient, dict[str, object], dict[str, asyncio.Future[dict[str, object]]]]:
    agent = socketio.AsyncClient(reconnection=False)
    loop = asyncio.get_running_loop()
    events = {name: loop.create_future() for name in ("registered", "request_dispatch")}
    events["outcome_recorded"] = loop.create_future()
    for name, future in events.items():
        agent.on(name, lambda data, future=future: future.set_result(data))
    await agent.connect(f"http://127.0.0.1:{port}", auth={"token": TOKEN})
    await agent.emit(
        "register",
        {
            "instance_id": INSTANCE_ID,
            "protocol_versions": [4],
            "agent_version": "test-agent",
            "td_build": "2025.32050",
            "capabilities": ["ops.get"],
        },
    )
    connection = await asyncio.wait_for(events["registered"], 2)
    return agent, connection, events


async def get_request(port: int, request_id: str) -> dict[str, object]:
    async with ClientSession() as session:
        response = await session.get(
            f"http://127.0.0.1:{port}/v3/requests/{request_id}",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert response.status == 200
        return await response.json()


@pytest.mark.parametrize(
    ("phase", "request_id", "expected"),
    [
        ("queued", "018f47ec-7f3b-7a34-8f31-2ad70b6f6e20", "daemon_shutdown"),
        ("dispatched", "018f47ec-7f3b-7a34-8f31-2ad70b6f6e21", "unknown"),
    ],
)
@pytest.mark.asyncio
async def test_public_transport_recovers_each_abrupt_process_boundary(
    tmp_path: Path, phase: str, request_id: str, expected: str
) -> None:
    root = tmp_path / phase
    port = unused_port()
    process = await start_daemon_process(root, port)
    agents: list[socketio.AsyncClient] = []
    try:
        agent, connection, events = await register_agent(port)
        agents.append(agent)
        if phase != "queued":
            await agent.emit("execution_sync", {**connection, "records": []})
        async with ClientSession() as session:
            response = await session.post(
                f"http://127.0.0.1:{port}/v3/requests",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={
                    "request_id": request_id,
                    "instance_id": INSTANCE_ID,
                    "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
                },
            )
            assert response.status == 201
            assert (await response.json())["status"] == "queued"
        execution_id = None
        if phase != "queued":
            execution_id = (await asyncio.wait_for(events["request_dispatch"], 2))["execution_id"]
        await kill_process(process)
        await agent.disconnect()

        process = await start_daemon_process(root, port)
        recovered = await get_request(port, request_id)
        assert recovered["status"] == expected
        if phase == "queued":
            return
        assert recovered["execution_id"] == execution_id
        assert recovered["error"]["code"] == "request_outcome_unknown"

        # The Agent kept its outcome record, so the restarted Daemon refines the unknown.
        agent, connection, events = await register_agent(port)
        agents.append(agent)
        outcome = {
            **connection,
            "request_id": request_id,
            "execution_id": execution_id,
            "status": "succeeded",
            "result": {"path": "/project1"},
            "error": None,
        }
        await agent.emit(
            "request_outcome_chunk",
            {
                **connection,
                "request_id": request_id,
                "execution_id": execution_id,
                "chunk_index": 0,
                "chunk_count": 1,
                "payload": json.dumps(outcome),
            },
        )
        await asyncio.wait_for(events["outcome_recorded"], 2)
        refined = await get_request(port, request_id)
        assert refined["status"] == "succeeded"
        assert refined["result"] == {"path": "/project1"}
    finally:
        for agent in agents:
            if agent.connected:
                await agent.disconnect()
        if process.returncode is None:
            await kill_process(process)
