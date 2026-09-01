import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from td_cli.daemon.storage import RequestIdentityConflict, RequestStore
from td_cli.protocol import Command, RequestSnapshot

REQUEST_ID = "018f47ec-7f3b-7a34-8f31-2ad70b6f6e2a"
INSTANCE_ID = "8cf81688-b9a4-4c39-9f92-31c77319c761"


def snapshot(
    *, instance_id: str = INSTANCE_ID, operator_path: str = "/project1"
) -> dict[str, object]:
    return RequestSnapshot.pending(
        request_id=REQUEST_ID,
        instance_id=instance_id,
        command=Command(name="ops.get", input={"operator_path": operator_path}),
        submitted_at="2026-09-01T00:00:00.000Z",
    ).model_dump(mode="json")


@pytest.mark.asyncio
async def test_create_or_get_is_atomic_and_compares_full_identity(tmp_path: Path) -> None:
    store = await RequestStore.open(tmp_path / "daemon.db")
    try:
        outcomes = await asyncio.gather(*(store.create_or_get(snapshot()) for _ in range(16)))
        assert sum(created for _, created in outcomes) == 1
        assert all(item == outcomes[0][0] for item, _ in outcomes)

        with pytest.raises(RequestIdentityConflict):
            await store.create_or_get(snapshot(instance_id="4b5fd041-06ed-4c3f-b761-173530d99589"))
        with pytest.raises(RequestIdentityConflict):
            await store.create_or_get(snapshot(operator_path="/other"))
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_compare_and_set_uses_caller_statuses_and_cannot_reverse_terminal(
    tmp_path: Path,
) -> None:
    store = await RequestStore.open(tmp_path / "daemon.db")
    try:
        await store.create_or_get(snapshot())
        succeeded = await store.compare_and_set(
            REQUEST_ID,
            expected_statuses={"queued"},
            changes={
                "status": "succeeded",
                "result": {"path": "/project1"},
                "completed_at": "2026-09-01T00:00:01.000Z",
            },
        )
        assert succeeded is not None and succeeded["status"] == "succeeded"
        assert (
            await store.compare_and_set(
                REQUEST_ID,
                expected_statuses={"queued", "running"},
                changes={"status": "failed"},
            )
            is None
        )
        assert (await store.get(REQUEST_ID)) == succeeded
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_v1_migration_preserves_terminal_and_recovers_nonterminal(tmp_path: Path) -> None:
    path = tmp_path / "daemon.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE requests(request_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, "
        "status TEXT NOT NULL, completed_at TEXT)"
    )
    queued = snapshot()
    terminal = {
        **snapshot(operator_path="/terminal"),
        "request_id": "018f47ec-7f3b-7a34-8f31-2ad70b6f6e2b",
        "status": "succeeded",
        "result": {"path": "/terminal"},
        "completed_at": "2026-09-01T00:00:02.000Z",
    }
    for item in (queued, terminal):
        connection.execute(
            "INSERT INTO requests VALUES(?,?,?,?)",
            (item["request_id"], json.dumps(item), item["status"], item["completed_at"]),
        )
    connection.execute("PRAGMA user_version=1")
    connection.commit()
    connection.close()

    store = await RequestStore.open(path)
    try:
        recovered = await store.get(REQUEST_ID)
        preserved = await store.get(str(terminal["request_id"]))
        assert recovered is not None and recovered["status"] == "daemon_shutdown"
        assert recovered["error"]["code"] == "daemon_shutdown"
        assert preserved == terminal
        assert await store.schema_version() == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_failed_v1_migration_rolls_back_and_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "daemon.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE requests(request_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, "
        "status TEXT NOT NULL, completed_at TEXT)"
    )
    connection.execute("INSERT INTO requests VALUES('broken','not-json','queued',NULL)")
    connection.execute("PRAGMA user_version=1")
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="migration"):
        await RequestStore.open(path)

    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    assert connection.execute("SELECT snapshot FROM requests").fetchone()[0] == "not-json"
    connection.close()


@pytest.mark.asyncio
async def test_wal_recovers_committed_request_after_abrupt_process_exit(tmp_path: Path) -> None:
    path = tmp_path / "daemon.db"
    script = """
import asyncio
import os
import sys
from pathlib import Path
from td_cli.daemon.storage import RequestStore

async def main():
    store = await RequestStore.open(Path(sys.argv[1]))
    await store.create_or_get({
        "request_id": "018f47ec-7f3b-7a34-8f31-2ad70b6f6e2a",
        "instance_id": "8cf81688-b9a4-4c39-9f92-31c77319c761",
        "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
        "status": "queued", "execution_id": None,
        "submitted_at": "2026-09-01T00:00:00.000Z",
        "dispatched_at": None, "accepted_at": None,
        "execute_authorized_at": None, "completed_at": None,
        "result": None, "error": None,
    })
    os._exit(0)

asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", script, str(path))
    assert await process.wait() == 0

    store = await RequestStore.open(path)
    try:
        recovered = await store.get(REQUEST_ID)
        assert recovered is not None
        assert recovered["status"] == "queued"
    finally:
        await store.close()
