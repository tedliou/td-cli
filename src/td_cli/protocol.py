"""Strict public Protocol v4 models shared by the Daemon and Agent Component."""

from __future__ import annotations

import codecs
import json
from enum import StrEnum
from typing import Any

from pydantic import Field, ValidationError, model_validator
from pydantic_core import InitErrorDetails

from td_cli.command_catalog import (
    COMMAND_CATALOG,
    NESTING_COMMANDS,
    CommandInput,
    OperatorInput,
    StrictModel,
)

__all__ = ["OperatorInput"]

PROTOCOL_VERSION = 4
PROTOCOL_VERSIONS = (PROTOCOL_VERSION,)


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
        try:
            return {**value, "input": model.model_validate(value.get("input"))}
        except ValidationError as error:
            # Report input errors under "input" so they cannot collide with Command fields.
            details: list[InitErrorDetails] = []
            for item in error.errors(include_url=False):
                detail = InitErrorDetails(
                    type=item["type"], loc=("input", *item["loc"]), input=item["input"]
                )
                if "ctx" in item:
                    detail["ctx"] = item["ctx"]
                details.append(detail)
            raise ValidationError.from_exception_data(error.title, details) from error

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )


class RequestStatus(StrEnum):
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    INSTANCE_OFFLINE = "instance_offline"
    DAEMON_SHUTDOWN = "daemon_shutdown"


# A client stops waiting at these statuses; `unknown` may still be refined by a retained outcome.
TERMINAL_REQUEST_STATUSES = frozenset(
    {
        RequestStatus.SUCCEEDED,
        RequestStatus.FAILED,
        RequestStatus.UNKNOWN,
        RequestStatus.INSTANCE_OFFLINE,
        RequestStatus.DAEMON_SHUTDOWN,
    }
)

# Longest long poll a Request query may ask for; the Daemon rejects a longer ``wait_ms``.
# It stays well under the 60-second single-wait limit of AGENTS.md.
MAX_REQUEST_WAIT_MS = 25_000


class RequestSnapshot(StrictModel):
    request_id: str
    instance_id: str
    command: Command
    status: RequestStatus
    execution_id: str | None
    submitted_at: str
    dispatched_at: str | None
    accepted_at: str | None
    execute_authorized_at: str | None
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
            execution_id=None,
            submitted_at=submitted_at,
            dispatched_at=None,
            accepted_at=None,
            execute_authorized_at=None,
            completed_at=None,
            result=None,
            error=None,
        )


# A ``commands execute`` plan document, validated whole before its first Command is sent.
MAX_PLAN_BYTES = 1024 * 1024


def plan_document_fits(document: bytes) -> bool:
    """A plan is at most ``MAX_PLAN_BYTES`` of UTF-8 without a byte order mark."""
    return len(document) <= MAX_PLAN_BYTES and not document.startswith(codecs.BOM_UTF8)


class CommandPlan(StrictModel):
    commands: list[Command] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def no_nested_batch(self) -> CommandPlan:
        if any(command.name in NESTING_COMMANDS for command in self.commands):
            raise ValueError("nested batch is unsupported")
        return self
