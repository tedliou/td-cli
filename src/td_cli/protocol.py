"""Strict public Protocol v1 models shared by the Daemon and Agent Component."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DiagnosticInput(StrictModel):
    """The sole Phase 1 command payload; not part of the five typed Commands."""

    message: str
    sequence: int | None = None


class Command(StrictModel):
    name: Literal["diagnostic.ping"]
    input: dict[str, Any]

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )


class RequestStatus(StrEnum):
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    INSTANCE_OFFLINE = "instance_offline"
    DAEMON_SHUTDOWN = "daemon_shutdown"


class RequestSnapshot(StrictModel):
    request_id: str
    instance_id: str
    command: Command
    status: RequestStatus
    submitted_at: str
    dispatched_at: str | None
    started_at: str | None
    completed_at: str | None
    result: dict[str, Any] | None
    error: dict[str, Any] | None

    @classmethod
    def pending(
        cls, *, request_id: str, instance_id: str, command: Command, submitted_at: str
    ) -> RequestSnapshot:
        return cls(
            request_id=request_id,
            instance_id=instance_id,
            command=command,
            status=RequestStatus.QUEUED,
            submitted_at=submitted_at,
            dispatched_at=None,
            started_at=None,
            completed_at=None,
            result=None,
            error=None,
        )
