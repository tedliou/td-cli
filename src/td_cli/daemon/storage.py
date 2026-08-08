from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class RequestStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        integrity = self.connection.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError("daemon database is corrupt; preserve it for manual recovery")
        schema_version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if schema_version > 1:
            raise RuntimeError("daemon database schema is newer than this Daemon supports")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL,
                status TEXT NOT NULL, completed_at TEXT
            )"""
        )
        self.connection.execute("PRAGMA user_version=1")
        self.recover()
        self.cleanup()

    def recover(self) -> None:
        rows = self.connection.execute(
            "SELECT request_id, snapshot, status FROM requests WHERE status IN ('queued','dispatched','running')"
        ).fetchall()
        with self.connection:
            for row in rows:
                snapshot = json.loads(row["snapshot"])
                if row["status"] == "queued":
                    snapshot["status"] = "daemon_shutdown"
                    code = "daemon_shutdown"
                else:
                    snapshot["status"] = "unknown"
                    code = "request_outcome_unknown"
                snapshot["error"] = {
                    "code": code,
                    "message": code,
                    "details": {},
                    "retryable": False,
                }
                self.connection.execute(
                    "UPDATE requests SET snapshot=?, status=? WHERE request_id=?",
                    (
                        json.dumps(snapshot, separators=(",", ":")),
                        snapshot["status"],
                        row["request_id"],
                    ),
                )

    def insert(self, snapshot: dict[str, Any]) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO requests(request_id,snapshot,status,completed_at) VALUES(?,?,?,?)",
                (
                    snapshot["request_id"],
                    json.dumps(snapshot, separators=(",", ":")),
                    snapshot["status"],
                    snapshot["completed_at"],
                ),
            )

    def get(self, request_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT snapshot FROM requests WHERE request_id=?", (request_id,)
        ).fetchone()
        return json.loads(row["snapshot"]) if row else None

    def update(self, request_id: str, **changes: Any) -> dict[str, Any] | None:
        snapshot = self.get(request_id)
        if snapshot is None:
            return None
        snapshot.update(changes)
        with self.connection:
            self.connection.execute(
                "UPDATE requests SET snapshot=?, status=?, completed_at=? WHERE request_id=?",
                (
                    json.dumps(snapshot, separators=(",", ":")),
                    snapshot["status"],
                    snapshot["completed_at"],
                    request_id,
                ),
            )
        return snapshot

    def close(self) -> None:
        self.recover()
        self.connection.close()

    def cleanup(self, *, limit: int = 1000) -> int:
        cutoff = (
            (datetime.now(UTC) - timedelta(days=7))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        with self.connection:
            cursor = self.connection.execute(
                """DELETE FROM requests WHERE request_id IN (
                    SELECT request_id FROM requests
                    WHERE completed_at IS NOT NULL AND completed_at < ? LIMIT ?
                )""",
                (cutoff, limit),
            )
        return cursor.rowcount
