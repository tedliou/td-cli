from __future__ import annotations

import json
import sqlite3
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
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL,
                status TEXT NOT NULL, completed_at TEXT
            )"""
        )
        self.recover()

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

    def close(self) -> None:
        self.connection.close()
