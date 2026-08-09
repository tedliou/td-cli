"""Command definitions and input validation for Protocol v1."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class CreateOperatorInput(StrictModel):
    parent_path: str
    op_type: Literal["constantTOP", "noiseTOP", "levelTOP", "nullTOP"]
    name: str
    node_x: int = Field(default=0, ge=-32768, le=32767)
    node_y: int = Field(default=0, ge=-32768, le=32767)

    _parent_path = field_validator("parent_path")(_valid_operator_path)

    @field_validator("name")
    @classmethod
    def name_is_safe_and_exact(cls, value: str) -> str:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", value) is None:
            raise ValueError("name must be a safe exact Operator name")
        return value


class ConnectOperatorsInput(StrictModel):
    source_path: str
    target_path: str
    output_index: int = Field(default=0, ge=0, le=255)
    input_index: int = Field(default=0, ge=0, le=255)

    _source_path = field_validator("source_path")(_valid_operator_path)
    _target_path = field_validator("target_path")(_valid_operator_path)


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


class SnapshotInput(OperatorInput):
    max_depth: int = Field(default=4, ge=0, le=8)
    max_operators: int = Field(default=256, ge=1, le=1000)


class ProjectMetadataInput(StrictModel):
    pass


class BinaryExportInput(OperatorInput):
    format: Literal["tox", "png"]
    max_bytes: int = Field(default=194_560, ge=1, le=194_560)


class EventsReadInput(StrictModel):
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=200)
    include_errors: bool = True


class BatchItem(StrictModel):
    name: str
    input: dict[str, Any]

    @model_validator(mode="after")
    def validate_input(self) -> BatchItem:
        self.input = COMMAND_CATALOG.validate_input(self.name, self.input, batch=True)
        return self


class BatchExecuteInput(StrictModel):
    commands: list[BatchItem] = Field(min_length=1, max_length=16)


@dataclass(frozen=True)
class CommandDefinition:
    name: str
    input_model: type[StrictModel]
    batchable: bool = False


class CommandCatalog:
    """Small interface over all public Command contract facts."""

    def __init__(self, definitions: tuple[CommandDefinition, ...]) -> None:
        self._definitions = definitions
        self._by_name = {definition.name: definition for definition in definitions}
        if len(self._by_name) != len(definitions):
            raise ValueError("Command names must be unique")

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(definition.name for definition in self._definitions)

    @property
    def batch_names(self) -> tuple[str, ...]:
        return tuple(definition.name for definition in self._definitions if definition.batchable)

    def validate_input(self, name: str, value: Any, *, batch: bool = False) -> dict[str, Any]:
        definition = self._by_name.get(name)
        if definition is None or batch and not definition.batchable:
            raise ValueError("unsupported Command")
        return definition.input_model.model_validate(value).model_dump(mode="json")

    def input_model(self, name: str) -> type[StrictModel] | None:
        definition = self._by_name.get(name)
        return definition.input_model if definition is not None else None


COMMAND_CATALOG = CommandCatalog(
    (
        CommandDefinition("ops.get", OperatorInput, batchable=True),
        CommandDefinition("ops.children", ChildrenInput, batchable=True),
        CommandDefinition("parameters.get", ParameterInput, batchable=True),
        CommandDefinition("parameters.set", SetParameterInput, batchable=True),
        CommandDefinition("parameters.pulse", ParameterInput, batchable=True),
        CommandDefinition("ops.create", CreateOperatorInput),
        CommandDefinition("ops.connect", ConnectOperatorsInput),
        CommandDefinition("project.snapshot", SnapshotInput),
        CommandDefinition("project.metadata", ProjectMetadataInput),
        CommandDefinition("binary.export", BinaryExportInput),
        CommandDefinition("events.read", EventsReadInput),
        CommandDefinition("batch.execute", BatchExecuteInput),
    )
)


CommandInput = (
    OperatorInput
    | ChildrenInput
    | CreateOperatorInput
    | ConnectOperatorsInput
    | ParameterInput
    | SetParameterInput
    | SnapshotInput
    | ProjectMetadataInput
    | BinaryExportInput
    | EventsReadInput
    | BatchExecuteInput
)
