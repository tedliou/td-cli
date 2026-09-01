from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")
SCHEMA_VERSION = 2
LOGGER = logging.getLogger("td_cli.store")
_MUTABLE_COLUMNS = {
    "status": "status",
    "execution_id": "execution_id",
    "dispatched_at": "dispatched_at",
    "accepted_at": "accepted_at",
    "execute_authorized_at": "execute_authorized_at",
    "completed_at": "completed_at",
    "result": "result_json",
    "error": "error_json",
}


class RequestIdentityConflict(RuntimeError):
    """A Request ID was reused for a different immutable Request identity."""


class RequestStore:
    """Async Request persistence whose SQLite connection has exactly one worker owner."""

    def __init__(self, connection: sqlite3.Connection, executor: ThreadPoolExecutor) -> None:
        self._connection = connection
        self._executor = executor
        self._closed = False

    @classmethod
    async def open(cls, path: Path) -> RequestStore:
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="request-store")
        loop = asyncio.get_running_loop()
        try:
            connection = await loop.run_in_executor(executor, _open_connection, path)
        except BaseException:
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        return cls(connection, executor)

    async def create_or_get(self, snapshot: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        return await self._run(_create_or_get, self._connection, snapshot)

    async def get(self, request_id: str) -> dict[str, Any] | None:
        return await self._run(_get, self._connection, request_id)

    async def find_by_statuses(self, statuses: set[str]) -> list[dict[str, Any]]:
        if not statuses:
            return []
        return await self._run(_find_by_statuses, self._connection, statuses)

    async def compare_and_set(
        self,
        request_id: str,
        *,
        expected_statuses: set[str],
        changes: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not expected_statuses:
            raise ValueError("expected_statuses must not be empty")
        unknown = changes.keys() - _MUTABLE_COLUMNS.keys()
        if unknown:
            raise ValueError(f"unsupported Request columns: {', '.join(sorted(unknown))}")
        if not changes:
            raise ValueError("changes must not be empty")
        return await self._run(
            _compare_and_set,
            self._connection,
            request_id,
            expected_statuses,
            changes,
        )

    async def cleanup(self, *, limit: int = 1000) -> int:
        if limit < 1:
            raise ValueError("limit must be positive")
        return await self._run(_cleanup, self._connection, limit)

    async def schema_version(self) -> int:
        return await self._run(_schema_version, self._connection)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._run(_close, self._connection, allow_closed=True)
        self._executor.shutdown(wait=True, cancel_futures=True)

    async def _run(
        self,
        function: Callable[..., T],
        *args: object,
        allow_closed: bool = False,
    ) -> T:
        if self._closed and not allow_closed:
            raise RuntimeError("RequestStore is closed")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, function, *args)


def _open_connection(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        if connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() != "wal":
            raise RuntimeError("daemon database could not enable WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise RuntimeError("daemon database could not enable foreign keys")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=5000")
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("daemon database is corrupt; preserve it for manual recovery")
        version = _schema_version(connection)
        if version > SCHEMA_VERSION:
            raise RuntimeError("daemon database schema is newer than this Daemon supports")
        if version == 0:
            _create_schema(connection)
        elif version == 1:
            _migrate_v1(connection)
        _cleanup(connection, 1000)
        return connection
    except BaseException:
        connection.close()
        raise


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        _create_requests_table(connection, "requests")
        connection.execute(
            "CREATE INDEX requests_completed_at_idx ON requests(completed_at) "
            "WHERE completed_at IS NOT NULL"
        )
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def _create_requests_table(connection: sqlite3.Connection, name: str) -> None:
    connection.execute(
        f"""CREATE TABLE {name} (
            request_id TEXT PRIMARY KEY,
            instance_id TEXT NOT NULL,
            command_json TEXT NOT NULL,
            status TEXT NOT NULL,
            execution_id TEXT,
            submitted_at TEXT NOT NULL,
            dispatched_at TEXT,
            accepted_at TEXT,
            execute_authorized_at TEXT,
            completed_at TEXT,
            result_json TEXT,
            error_json TEXT
        )"""
    )


def _migrate_v1(connection: sqlite3.Connection) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        _create_requests_table(connection, "requests_v2")
        rows = connection.execute("SELECT snapshot, status FROM requests").fetchall()
        completed_at = _now()
        for row in rows:
            snapshot = json.loads(row["snapshot"])
            status = str(row["status"])
            if status == "queued":
                snapshot.update(
                    status="daemon_shutdown",
                    error=_error("daemon_shutdown"),
                    completed_at=completed_at,
                )
            elif status in {"dispatched", "running"}:
                snapshot.update(
                    status="unknown",
                    error=_error("request_outcome_unknown"),
                    completed_at=completed_at,
                )
            _insert_row(connection, "requests_v2", snapshot)
        connection.execute("DROP TABLE requests")
        connection.execute("ALTER TABLE requests_v2 RENAME TO requests")
        connection.execute(
            "CREATE INDEX requests_completed_at_idx ON requests(completed_at) "
            "WHERE completed_at IS NOT NULL"
        )
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        connection.execute("COMMIT")
    except BaseException as error:
        connection.execute("ROLLBACK")
        raise RuntimeError("daemon database v1 migration failed") from error


def _create_or_get(
    connection: sqlite3.Connection, snapshot: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        cursor = _insert_row(connection, "requests", snapshot, ignore=True)
        row = connection.execute(
            "SELECT * FROM requests WHERE request_id=?", (snapshot["request_id"],)
        ).fetchone()
        assert row is not None
        created = cursor.rowcount == 1
        if not created and (
            row["instance_id"] != snapshot["instance_id"]
            or row["command_json"] != _json(snapshot["command"])
        ):
            raise RequestIdentityConflict(str(snapshot["request_id"]))
        result = _snapshot(row)
        connection.execute("COMMIT")
        return result, created
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def _insert_row(
    connection: sqlite3.Connection,
    table: str,
    snapshot: dict[str, Any],
    *,
    ignore: bool = False,
) -> sqlite3.Cursor:
    clause = "OR IGNORE " if ignore else ""
    return connection.execute(
        f"""INSERT {clause}INTO {table}(
            request_id, instance_id, command_json, status, execution_id,
            submitted_at, dispatched_at, accepted_at, execute_authorized_at,
            completed_at, result_json, error_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            snapshot["request_id"],
            snapshot["instance_id"],
            _json(snapshot["command"]),
            snapshot["status"],
            snapshot.get("execution_id"),
            snapshot["submitted_at"],
            snapshot.get("dispatched_at"),
            snapshot.get("accepted_at"),
            snapshot.get("execute_authorized_at", snapshot.get("started_at")),
            snapshot.get("completed_at"),
            _json(snapshot.get("result")) if snapshot.get("result") is not None else None,
            _json(snapshot.get("error")) if snapshot.get("error") is not None else None,
        ),
    )


def _get(connection: sqlite3.Connection, request_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
    return _snapshot(row) if row is not None else None


def _find_by_statuses(connection: sqlite3.Connection, statuses: set[str]) -> list[dict[str, Any]]:
    ordered = sorted(statuses)
    placeholders = ",".join("?" for _ in ordered)
    rows = connection.execute(
        f"SELECT * FROM requests WHERE status IN ({placeholders}) ORDER BY submitted_at, request_id",
        ordered,
    ).fetchall()
    return [_snapshot(row) for row in rows]


def _compare_and_set(
    connection: sqlite3.Connection,
    request_id: str,
    expected_statuses: set[str],
    changes: dict[str, Any],
) -> dict[str, Any] | None:
    assignments: list[str] = []
    values: list[object] = []
    for public_name, value in changes.items():
        assignments.append(f"{_MUTABLE_COLUMNS[public_name]}=?")
        values.append(
            _json(value) if public_name in {"result", "error"} and value is not None else value
        )
    statuses = sorted(expected_statuses)
    placeholders = ",".join("?" for _ in statuses)
    connection.execute("BEGIN IMMEDIATE")
    try:
        cursor = connection.execute(
            f"UPDATE requests SET {', '.join(assignments)} "
            f"WHERE request_id=? AND status IN ({placeholders})",
            (*values, request_id, *statuses),
        )
        result = _get(connection, request_id) if cursor.rowcount == 1 else None
        connection.execute("COMMIT")
        LOGGER.info(
            _json(
                {
                    "event": "request.cas",
                    "request_id": request_id,
                    "expected_statuses": statuses,
                    "to_status": changes.get("status"),
                    "applied": cursor.rowcount == 1,
                }
            )
        )
        return result
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def _snapshot(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "request_id": row["request_id"],
        "instance_id": row["instance_id"],
        "command": json.loads(row["command_json"]),
        "status": row["status"],
        "execution_id": row["execution_id"],
        "submitted_at": row["submitted_at"],
        "dispatched_at": row["dispatched_at"],
        "accepted_at": row["accepted_at"],
        "execute_authorized_at": row["execute_authorized_at"],
        "completed_at": row["completed_at"],
        "result": json.loads(row["result_json"]) if row["result_json"] is not None else None,
        "error": json.loads(row["error_json"]) if row["error_json"] is not None else None,
    }


def _cleanup(connection: sqlite3.Connection, limit: int) -> int:
    cutoff = (
        (datetime.now(UTC) - timedelta(days=7))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    connection.execute("BEGIN IMMEDIATE")
    try:
        cursor = connection.execute(
            """DELETE FROM requests WHERE request_id IN (
                SELECT request_id FROM requests WHERE completed_at IS NOT NULL
                AND completed_at < ? ORDER BY completed_at LIMIT ?
            )""",
            (cutoff, limit),
        )
        connection.execute("COMMIT")
        return cursor.rowcount
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def _schema_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _close(connection: sqlite3.Connection) -> None:
    connection.close()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _error(code: str) -> dict[str, object]:
    return {"code": code, "message": code, "details": {}, "retryable": False}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
