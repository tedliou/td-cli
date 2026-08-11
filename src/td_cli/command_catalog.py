"""Command definitions and input validation for Protocol v1."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from td_cli.operator_catalog import OPERATOR_CATALOG


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


class ConnectionsInput(OperatorInput):
    max_connections: int = Field(default=256, ge=1, le=1000)


class OperatorColorInput(StrictModel):
    red: float = Field(ge=0, le=1, allow_inf_nan=False)
    green: float = Field(ge=0, le=1, allow_inf_nan=False)
    blue: float = Field(ge=0, le=1, allow_inf_nan=False)


OPERATOR_STATE_FIELDS = (
    "node_x",
    "node_y",
    "node_width",
    "node_height",
    "color",
    "comment",
    "bypass",
    "viewer",
    "expose",
    "lock",
)
OPERATOR_STATE_BOOLEAN_FIELDS = ("bypass", "viewer", "expose", "lock")
MAX_DAT_CONTENT_BYTES = 32_768
MAX_TABLE_ROWS = 256
MAX_TABLE_COLUMNS = 256
MAX_TABLE_CELLS = 4096
MAX_TABLE_CELL_BYTES = 16_384


class SetOperatorStateInput(OperatorInput):
    node_x: int | None = Field(default=None, ge=-32768, le=32767)
    node_y: int | None = Field(default=None, ge=-32768, le=32767)
    node_width: int | None = Field(default=None, ge=1, le=32767)
    node_height: int | None = Field(default=None, ge=1, le=32767)
    color: OperatorColorInput | None = None
    comment: str | None = Field(default=None, max_length=4096)
    bypass: bool | None = None
    viewer: bool | None = None
    expose: bool | None = None
    lock: bool | None = None

    @model_validator(mode="after")
    def patch_is_not_empty(self) -> SetOperatorStateInput:
        if all(getattr(self, field) is None for field in OPERATOR_STATE_FIELDS):
            raise ValueError("at least one Operator state field is required")
        return self


class TextDatReadInput(OperatorInput):
    max_bytes: int = Field(default=MAX_DAT_CONTENT_BYTES, ge=1, le=MAX_DAT_CONTENT_BYTES)


class TextDatSetInput(OperatorInput):
    text: str

    @field_validator("text")
    @classmethod
    def text_fits_content_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_DAT_CONTENT_BYTES:
            raise ValueError("text exceeds the UTF-8 content limit")
        return value


class TableDatReadInput(OperatorInput):
    row_offset: int = Field(default=0, ge=0, le=MAX_TABLE_ROWS - 1)
    column_offset: int = Field(default=0, ge=0, le=MAX_TABLE_COLUMNS - 1)
    row_count: int = Field(default=64, ge=1, le=MAX_TABLE_ROWS)
    column_count: int = Field(default=64, ge=1, le=MAX_TABLE_COLUMNS)
    max_bytes: int = Field(default=MAX_DAT_CONTENT_BYTES, ge=1, le=MAX_DAT_CONTENT_BYTES)

    @model_validator(mode="after")
    def window_fits_cell_limit(self) -> TableDatReadInput:
        if self.row_count * self.column_count > MAX_TABLE_CELLS:
            raise ValueError("table window exceeds the cell limit")
        return self


def _validate_table_rows(rows: list[list[str]], *, allow_empty: bool) -> list[list[str]]:
    if not rows:
        if allow_empty:
            return rows
        raise ValueError("table patch must contain at least one row")
    column_count = len(rows[0])
    if column_count == 0:
        raise ValueError("table rows must contain at least one cell")
    if len(rows) > MAX_TABLE_ROWS or column_count > MAX_TABLE_COLUMNS:
        raise ValueError("table dimensions exceed the limit")
    if len(rows) * column_count > MAX_TABLE_CELLS:
        raise ValueError("table content exceeds the cell limit")
    total_bytes = 0
    for row in rows:
        if len(row) != column_count:
            raise ValueError("table rows must be rectangular")
        for cell in row:
            cell_bytes = len(cell.encode("utf-8"))
            if cell_bytes > MAX_TABLE_CELL_BYTES:
                raise ValueError("table cell exceeds the UTF-8 byte limit")
            total_bytes += cell_bytes
    if total_bytes > MAX_DAT_CONTENT_BYTES:
        raise ValueError("table content exceeds the UTF-8 byte limit")
    return rows


class TableDatReplaceInput(OperatorInput):
    rows: list[list[str]]

    @field_validator("rows")
    @classmethod
    def rows_are_bounded_and_rectangular(cls, value: list[list[str]]) -> list[list[str]]:
        return _validate_table_rows(value, allow_empty=True)


class TableDatPatchInput(OperatorInput):
    row_offset: int = Field(default=0, ge=0, le=MAX_TABLE_ROWS - 1)
    column_offset: int = Field(default=0, ge=0, le=MAX_TABLE_COLUMNS - 1)
    rows: list[list[str]]

    @field_validator("rows")
    @classmethod
    def rows_are_bounded_and_rectangular(cls, value: list[list[str]]) -> list[list[str]]:
        return _validate_table_rows(value, allow_empty=False)

    @model_validator(mode="after")
    def patch_bounds_fit_protocol_dimensions(self) -> TableDatPatchInput:
        if (
            self.row_offset + len(self.rows) > MAX_TABLE_ROWS
            or self.column_offset + len(self.rows[0]) > MAX_TABLE_COLUMNS
        ):
            raise ValueError("table patch exceeds the dimension limit")
        return self


class CreateOperatorInput(StrictModel):
    parent_path: str
    op_type: str
    name: str
    node_x: int = Field(default=0, ge=-32768, le=32767)
    node_y: int = Field(default=0, ge=-32768, le=32767)
    allow_conditional: bool = False

    _parent_path = field_validator("parent_path")(_valid_operator_path)

    @field_validator("name")
    @classmethod
    def name_is_safe_and_exact(cls, value: str) -> str:
        return _valid_operator_name(value)

    @model_validator(mode="after")
    def operator_type_is_in_locked_catalog(self) -> CreateOperatorInput:
        OPERATOR_CATALOG.require_creatable(self.op_type, allow_conditional=self.allow_conditional)
        return self


def _valid_operator_name(value: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", value) is None:
        raise ValueError("name must be a safe exact Operator name")
    return value


class RenameOperatorInput(OperatorInput):
    new_name: str

    _new_name = field_validator("new_name")(_valid_operator_name)


class DestroyOperatorInput(OperatorInput):
    recursive: bool = False
    allow_connected: bool = False
    max_operators: int = Field(default=256, ge=1, le=1000)


class StructuralDestinationInput(StrictModel):
    source_path: str
    target_parent_path: str
    new_name: str
    node_x: int | None = Field(default=None, ge=-32768, le=32767)
    node_y: int | None = Field(default=None, ge=-32768, le=32767)
    max_operators: int = Field(default=256, ge=1, le=1000)

    _source_path = field_validator("source_path")(_valid_operator_path)
    _target_parent_path = field_validator("target_parent_path")(_valid_operator_path)
    _new_name = field_validator("new_name")(_valid_operator_name)


class CopyOperatorInput(StructuralDestinationInput):
    include_docked: bool = False


class MoveOperatorInput(StructuralDestinationInput):
    allow_connected: bool = False


class ConnectOperatorsInput(StrictModel):
    source_path: str
    target_path: str
    output_index: int = Field(default=0, ge=0, le=255)
    input_index: int = Field(default=0, ge=0, le=255)
    replace: bool = False

    _source_path = field_validator("source_path")(_valid_operator_path)
    _target_path = field_validator("target_path")(_valid_operator_path)


class DisconnectOperatorsInput(StrictModel):
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


MAX_PARAMETER_SOURCE_BYTES = 16_384
MAX_MULTI_OP_PATHS = 256
MAX_SEQUENCE_BLOCKS = 128
MAX_SEQUENCE_PARAMETERS = 256

ParameterValue = bool | int | float | str | None | list[str]


class ParameterSourceInput(StrictModel):
    kind: Literal["export_channel", "bind_parameter"]
    operator_path: str
    channel: str | None = Field(default=None, min_length=1, max_length=256)
    parameter: str | None = Field(default=None, min_length=1, max_length=256)

    _operator_path = field_validator("operator_path")(_valid_operator_path)

    @field_validator("parameter")
    @classmethod
    def parameter_is_runtime_identifier(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", value) is None:
            raise ValueError("parameter must be a TouchDesigner runtime identifier")
        return value

    @model_validator(mode="after")
    def identity_matches_kind(self) -> ParameterSourceInput:
        if self.kind == "export_channel" and (self.channel is None or self.parameter is not None):
            raise ValueError("export_channel requires only channel")
        if self.kind == "bind_parameter" and (self.parameter is None or self.channel is not None):
            raise ValueError("bind_parameter requires only parameter")
        return self


class SetParameterInput(ParameterInput):
    mode: Literal["constant", "expression", "export", "bind"]
    value: ParameterValue = None
    source: ParameterSourceInput | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def value_matches_protocol_limits(self) -> SetParameterInput:
        value = self.value
        if type(value) is int and abs(value) > 9_007_199_254_740_991:
            raise ValueError("integer is outside the JavaScript safe-integer range")
        if type(value) is float and not math.isfinite(value):
            raise ValueError("number must be finite")
        if isinstance(value, list) and len(value) > MAX_MULTI_OP_PATHS:
            raise ValueError("too many Operator paths")
        if isinstance(value, list):
            for path in value:
                _valid_operator_path(path)
        if self.mode == "expression" and type(value) is not str:
            raise ValueError("expression value must be source text")
        if isinstance(value, str) and len(value.encode("utf-8")) > MAX_PARAMETER_SOURCE_BYTES:
            raise ValueError("expression source is too large")
        if self.mode in {"export", "bind"}:
            expected = "export_channel" if self.mode == "export" else "bind_parameter"
            if self.source is None or self.source.kind != expected or value is not None:
                raise ValueError(f"{self.mode} requires a matching typed source")
        elif self.source is not None:
            raise ValueError("constant and expression modes do not accept source")
        return self


class SequenceInput(OperatorInput):
    sequence: str = Field(min_length=1, max_length=256)
    max_blocks: int = Field(default=MAX_SEQUENCE_BLOCKS, ge=1, le=MAX_SEQUENCE_BLOCKS)
    max_parameters: int = Field(default=MAX_SEQUENCE_PARAMETERS, ge=1, le=MAX_SEQUENCE_PARAMETERS)


class SequenceParameterWrite(StrictModel):
    parameter: str = Field(min_length=1, max_length=256)
    mode: Literal["constant", "expression", "export", "bind"]
    value: ParameterValue = None
    source: ParameterSourceInput | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def value_matches_protocol_limits(self) -> SequenceParameterWrite:
        validated = SetParameterInput.model_validate(
            {
                "operator_path": "/",
                "parameter": self.parameter,
                "mode": self.mode,
                "value": self.value,
                "source": self.source,
            }
        )
        self.value = validated.value
        self.source = validated.source
        return self


class SequenceBlockWrite(StrictModel):
    name: str | None = Field(default=None, max_length=256)
    parameters: list[SequenceParameterWrite] = Field(max_length=MAX_SEQUENCE_PARAMETERS)


class ReplaceSequenceInput(SequenceInput):
    blocks: list[SequenceBlockWrite] = Field(max_length=MAX_SEQUENCE_BLOCKS)


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
        CommandDefinition("ops.connections", ConnectionsInput, batchable=True),
        CommandDefinition("ops.hierarchy.connections", ConnectionsInput, batchable=True),
        CommandDefinition("ops.state.get", OperatorInput, batchable=True),
        CommandDefinition("ops.state.set", SetOperatorStateInput),
        CommandDefinition("dat.text.get", TextDatReadInput, batchable=True),
        CommandDefinition("dat.text.set", TextDatSetInput),
        CommandDefinition("dat.table.get", TableDatReadInput, batchable=True),
        CommandDefinition("dat.table.replace", TableDatReplaceInput),
        CommandDefinition("dat.table.patch", TableDatPatchInput),
        CommandDefinition("parameters.get", ParameterInput, batchable=True),
        CommandDefinition("parameters.list", OperatorInput, batchable=True),
        CommandDefinition("parameters.set", SetParameterInput, batchable=True),
        CommandDefinition("parameters.pulse", ParameterInput, batchable=True),
        CommandDefinition("parameters.sequence.get", SequenceInput, batchable=True),
        CommandDefinition("parameters.sequence.replace", ReplaceSequenceInput),
        CommandDefinition("ops.create", CreateOperatorInput),
        CommandDefinition("ops.rename", RenameOperatorInput),
        CommandDefinition("ops.destroy", DestroyOperatorInput),
        CommandDefinition("ops.copy", CopyOperatorInput),
        CommandDefinition("ops.move", MoveOperatorInput),
        CommandDefinition("ops.connect", ConnectOperatorsInput),
        CommandDefinition("ops.disconnect", DisconnectOperatorsInput),
        CommandDefinition("ops.hierarchy.connect", ConnectOperatorsInput),
        CommandDefinition("ops.hierarchy.disconnect", DisconnectOperatorsInput),
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
    | ConnectionsInput
    | SetOperatorStateInput
    | TextDatReadInput
    | TextDatSetInput
    | TableDatReadInput
    | TableDatReplaceInput
    | TableDatPatchInput
    | CreateOperatorInput
    | RenameOperatorInput
    | DestroyOperatorInput
    | CopyOperatorInput
    | MoveOperatorInput
    | ConnectOperatorsInput
    | DisconnectOperatorsInput
    | ParameterInput
    | SetParameterInput
    | SequenceInput
    | ReplaceSequenceInput
    | SnapshotInput
    | ProjectMetadataInput
    | BinaryExportInput
    | EventsReadInput
    | BatchExecuteInput
)
