"""Strict public Protocol v1 models shared by the Daemon and Agent Component."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from pydantic import model_validator

from td_cli.command_catalog import (
    COMMAND_CATALOG,
    CommandInput,
    OperatorInput,
    StrictModel,
)

__all__ = ["OperatorInput"]


class Command(StrictModel):
    name: str
    input: CommandInput

    @model_validator(mode="before")
    @classmethod
    def input_matches_name(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        name = value.get("name")
        if not isinstance(name, str):
            return value
        model = COMMAND_CATALOG.input_model(name)
        if model is None:
            raise ValueError("unsupported Command")
        return {**value, "input": model.model_validate(value.get("input"))}

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
