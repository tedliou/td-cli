"""Bounded client orchestration of independent, existing Requests."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError, model_validator

from td_cli.client import ClientError, DaemonClient
from td_cli.command_catalog import StrictModel
from td_cli.protocol import Command

MAX_PLAN_BYTES = 1024 * 1024


class CommandPlan(StrictModel):
    commands: list[Command] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def no_nested_batch(self) -> CommandPlan:
        if any(command.name == "batch.execute" for command in self.commands):
            raise ValueError("nested batch is unsupported")
        return self


def read_plan(path: Path) -> list[Command]:
    try:
        with path.open("rb") as source:
            data = source.read(MAX_PLAN_BYTES + 1)
        if len(data) > MAX_PLAN_BYTES or data.startswith(b"\xef\xbb\xbf"):
            raise ValueError("invalid plan size or encoding")
        return CommandPlan.model_validate_json(data).commands
    except (OSError, ValueError, ValidationError) as error:
        raise ClientError("invalid_arguments") from error


def run_plan(
    commands: list[Command],
    *,
    client: DaemonClient,
    selector: str | None,
    timeout: float,
    new_request_id: Callable[[], str],
    emit: Callable[[dict[str, Any]], None],
) -> None:
    deadline = time.monotonic() + timeout
    try:
        instance = client.select_instance(selector)
    except ClientError as error:
        emit(
            {
                "event": "stopped",
                "index": 0,
                "request_id": None,
                "error": {"code": error.code, "details": error.details},
                "succeeded": 0,
                "unsubmitted": len(commands),
            }
        )
        raise
    succeeded = 0
    for index, command in enumerate(commands):
        request_id = new_request_id()
        submitting = False
        try:
            if time.monotonic() >= deadline:
                raise ClientError("wait_timeout")
            emit(
                {
                    "event": "submitting",
                    "index": index,
                    "request_id": request_id,
                    "instance_id": instance["instance_id"],
                }
            )
            submitting = True
            client.submit(request_id, instance["instance_id"], command.model_dump(mode="json"))
            snapshot = client.wait(request_id)
            emit({"event": "completed", "index": index, "request": snapshot})
            if snapshot["status"] != "succeeded":
                terminal_error = snapshot.get("error") or {"code": "internal_error"}
                raise ClientError(str(terminal_error["code"]), details={"request": snapshot})
            succeeded += 1
        except ClientError as error:
            if submitting:
                error.details.setdefault("request_id", request_id)
            emit(
                {
                    "event": "stopped",
                    "index": index,
                    "request_id": request_id if submitting else None,
                    "error": {"code": error.code, "details": error.details},
                    "succeeded": succeeded,
                    "unsubmitted": len(commands) - index - int(submitting),
                }
            )
            raise
    emit({"event": "finished", "succeeded": succeeded})
