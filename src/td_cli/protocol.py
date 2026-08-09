"""Strict public Protocol v1 models shared by the Daemon and Agent Component."""

from __future__ import annotations

import json
import math
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _valid_operator_path(value: str) -> str:
    if (
        not value.startswith("/")
        or value != "/"
        and any(part in {"", ".", ".."} for part in value.split("/")[1:])
        or any(character in value for character in "*?[]")
    ):
        raise ValueError("operator_path must be canonical and absolute")
    return value


class OperatorInput(StrictModel):
    operator_path: str

    _operator_path = field_validator("operator_path")(_valid_operator_path)


class ChildrenInput(OperatorInput):
    op_type: str | None = None


class ParameterInput(OperatorInput):
    parameter: str

    @field_validator("parameter")
    @classmethod
    def parameter_is_not_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("parameter must not be empty")
        return value


ParameterValue = bool | int | float | str


class SetParameterInput(ParameterInput):
    mode: Literal["constant", "expression"]
    value: ParameterValue

    @model_validator(mode="after")
    def value_matches_protocol_limits(self) -> SetParameterInput:
        value = self.value
        if type(value) is int and abs(value) > 9_007_199_254_740_991:
            raise ValueError("integer is outside the JavaScript safe-integer range")
        if type(value) is float and not math.isfinite(value):
            raise ValueError("number must be finite")
        if self.mode == "expression" and type(value) is not str:
            raise ValueError("expression value must be source text")
        return self


CommandInput = OperatorInput | ChildrenInput | ParameterInput | SetParameterInput


class Command(StrictModel):
    name: Literal[
        "ops.get",
        "ops.children",
        "parameters.get",
        "parameters.set",
        "parameters.pulse",
    ]
    input: CommandInput

    @model_validator(mode="before")
    @classmethod
    def input_matches_name(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        models: dict[str, type[StrictModel]] = {
            "ops.get": OperatorInput,
            "ops.children": ChildrenInput,
            "parameters.get": ParameterInput,
            "parameters.set": SetParameterInput,
            "parameters.pulse": ParameterInput,
        }
        name = value.get("name")
        if not isinstance(name, str):
            return value
        model = models.get(name)
        if model is None:
            return value
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
