"""Command definitions and input validation for Protocol v2."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PureWindowsPath
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


MAX_INSPECTION_ITEMS = 100
MAX_INSPECTION_STRING_BYTES = 4096
MAX_TOX_FILE_BYTES = 67_108_864


class InspectOperatorInput(OperatorInput):
    max_items: int = Field(default=MAX_INSPECTION_ITEMS, ge=1, le=1000)


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


def _valid_local_windows_path(value: str) -> str:
    if len(value.encode("utf-8")) > MAX_INSPECTION_STRING_BYTES:
        raise ValueError("path exceeds the UTF-8 byte limit")
    path = PureWindowsPath(value)
    if not path.is_absolute() or not path.drive or str(path).startswith("\\\\"):
        raise ValueError("path must be an absolute local Windows path")
    if any(part in {"", ".", ".."} for part in path.parts[1:]):
        raise ValueError("path must be canonical")
    if ":" in str(path)[2:]:
        raise ValueError("alternate data streams are not supported")
    return value


class ImportToxInput(StrictModel):
    parent_path: str
    tox_path: str
    allowlist_root: str
    target_name: str
    trusted: Literal[True]
    replace: bool = False
    max_file_bytes: int = Field(default=MAX_TOX_FILE_BYTES, ge=1, le=MAX_TOX_FILE_BYTES)
    max_operators: int = Field(default=256, ge=1, le=1000)

    _parent_path = field_validator("parent_path")(_valid_operator_path)
    _tox_path = field_validator("tox_path")(_valid_local_windows_path)
    _allowlist_root = field_validator("allowlist_root")(_valid_local_windows_path)
    _target_name = field_validator("target_name")(_valid_operator_name)

    @field_validator("tox_path")
    @classmethod
    def tox_path_has_exact_extension(cls, value: str) -> str:
        if PureWindowsPath(value).suffix.lower() != ".tox":
            raise ValueError("tox_path must have a .tox extension")
        return value


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
    effect: CommandEffect
    execution_class: ExecutionClass
    batchable: bool = False

    def __post_init__(self) -> None:
        if self.batchable and self.effect is not CommandEffect.READ_ONLY:
            raise ValueError("Only read-only Commands may be batchable")


class CommandEffect(StrEnum):
    READ_ONLY = "read_only"
    MUTATION = "mutation"


class ExecutionClass(StrEnum):
    FAST_READ = "fast_read"
    BOUNDED_SCAN_OR_EXPORT = "bounded_scan_or_export"
    BOUNDED_MUTATION = "bounded_mutation"
    TRUSTED_ASSET_MUTATION = "trusted_asset_mutation"


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

    def effect(self, name: str) -> CommandEffect:
        return self._definition(name).effect

    def execution_class(self, name: str) -> ExecutionClass:
        return self._definition(name).execution_class

    def generic_exception_status(self, name: str) -> str:
        return "failed" if self.effect(name) is CommandEffect.READ_ONLY else "unknown"

    def normalize_result(self, command: object, result: object) -> object:
        """Restore nullable fields and wire-safe scalar types for a Command result."""
        if not isinstance(command, dict) or not isinstance(result, dict):
            return result
        normalized = dict(result)
        name = command.get("name")
        if name == "batch.execute":
            command_input = command.get("input")
            nested_commands = (
                command_input.get("commands") if isinstance(command_input, dict) else None
            )
            nested_results = normalized.get("results")
            if isinstance(nested_commands, list) and isinstance(nested_results, list):
                normalized["results"] = [
                    self.normalize_result(nested_command, nested_result)
                    for nested_command, nested_result in zip(
                        nested_commands, nested_results, strict=False
                    )
                ]
        elif name in {"ops.connect", "ops.hierarchy.connect"}:
            normalized.setdefault("previous_connection", None)
        elif name in {"ops.connections", "ops.hierarchy.connections"} and isinstance(
            normalized.get("inputs"), list
        ):
            normalized["inputs"] = [
                {**item, "connection": item.get("connection")} if isinstance(item, dict) else item
                for item in normalized["inputs"]
            ]
        elif name == "ops.copy" and "include_docked" in normalized:
            normalized["include_docked"] = bool(normalized["include_docked"])
        elif name == "ops.tox.import":
            for field in ("trusted", "replaced", "rollback_performed"):
                if field in normalized:
                    normalized[field] = bool(normalized[field])
        elif name in {"ops.state.get", "ops.state.set"} and isinstance(
            normalized.get("state"), dict
        ):
            state = dict(normalized["state"])
            for field in OPERATOR_STATE_BOOLEAN_FIELDS:
                if field in state:
                    state[field] = bool(state[field])
            normalized["state"] = state
        elif name == "ops.inspect":
            for section, fields in {
                "cook": ("cooked_this_frame", "cooked_previous_frame"),
                "flags": ("display", "render"),
            }.items():
                values = normalized.get(section)
                if isinstance(values, dict):
                    normalized[section] = {
                        **values,
                        **{field: bool(values[field]) for field in fields if field in values},
                    }
            details = normalized.get("details")
            if isinstance(details, dict):
                if normalized.get("family") == "DAT":
                    details.setdefault("editing_file", None)
                for field in ("time_slice", "export", "editable", "template", "compare"):
                    if field in details:
                        details[field] = bool(details[field])
                normalized["details"] = details
        elif name == "parameters.list" and isinstance(normalized.get("parameters"), list):
            parameters = []
            for item in normalized["parameters"]:
                if not isinstance(item, dict):
                    parameters.append(item)
                    continue
                descriptor = dict(item)
                for field in (
                    "page",
                    "unsupported_reason",
                    "sequence",
                    "source",
                    "bounds",
                    "max_operator_paths",
                ):
                    descriptor.setdefault(field, None)
                expression = descriptor.get("expression")
                if isinstance(expression, dict):
                    descriptor["expression"] = {
                        **expression,
                        "source": expression.get("source"),
                    }
                if descriptor.get("value_kind") == "menu":
                    descriptor.setdefault("menu_names", [])
                    descriptor.setdefault("menu_labels", [])
                else:
                    descriptor.setdefault("menu_names", None)
                    descriptor.setdefault("menu_labels", None)
                parameters.append(descriptor)
            normalized["parameters"] = parameters
        elif name in {"parameters.get", "parameters.set"}:
            normalized.setdefault("source", None)
            normalized.setdefault("unsupported_reason", None)
            if normalized.get("value_type") in {"operator", "python", "sequence", "unknown"}:
                normalized.setdefault("value", None)
        elif name in {"parameters.sequence.get", "parameters.sequence.replace"}:
            normalized.setdefault("max_blocks", None)
            for block in normalized.get("blocks", []):
                if not isinstance(block, dict):
                    continue
                block.setdefault("name", None)
                for parameter in block.get("parameters", []):
                    if isinstance(parameter, dict):
                        parameter.setdefault("value", None)
        return normalized

    def _definition(self, name: str) -> CommandDefinition:
        definition = self._by_name.get(name)
        if definition is None:
            raise ValueError("unsupported Command")
        return definition


COMMAND_CATALOG = CommandCatalog(
    (
        CommandDefinition(
            "ops.get", OperatorInput, CommandEffect.READ_ONLY, ExecutionClass.FAST_READ, True
        ),
        CommandDefinition(
            "ops.inspect",
            InspectOperatorInput,
            CommandEffect.READ_ONLY,
            ExecutionClass.BOUNDED_SCAN_OR_EXPORT,
            True,
        ),
        CommandDefinition(
            "ops.children",
            ChildrenInput,
            CommandEffect.READ_ONLY,
            ExecutionClass.BOUNDED_SCAN_OR_EXPORT,
            True,
        ),
        CommandDefinition(
            "ops.connections",
            ConnectionsInput,
            CommandEffect.READ_ONLY,
            ExecutionClass.BOUNDED_SCAN_OR_EXPORT,
            True,
        ),
        CommandDefinition(
            "ops.hierarchy.connections",
            ConnectionsInput,
            CommandEffect.READ_ONLY,
            ExecutionClass.BOUNDED_SCAN_OR_EXPORT,
            True,
        ),
        CommandDefinition(
            "ops.state.get", OperatorInput, CommandEffect.READ_ONLY, ExecutionClass.FAST_READ, True
        ),
        CommandDefinition(
            "ops.state.set",
            SetOperatorStateInput,
            CommandEffect.MUTATION,
            ExecutionClass.BOUNDED_MUTATION,
        ),
        CommandDefinition(
            "dat.text.get",
            TextDatReadInput,
            CommandEffect.READ_ONLY,
            ExecutionClass.BOUNDED_SCAN_OR_EXPORT,
            True,
        ),
        CommandDefinition(
            "dat.text.set", TextDatSetInput, CommandEffect.MUTATION, ExecutionClass.BOUNDED_MUTATION
        ),
        CommandDefinition(
            "dat.table.get",
            TableDatReadInput,
            CommandEffect.READ_ONLY,
            ExecutionClass.BOUNDED_SCAN_OR_EXPORT,
            True,
        ),
        CommandDefinition(
            "dat.table.replace",
            TableDatReplaceInput,
            CommandEffect.MUTATION,
            ExecutionClass.BOUNDED_MUTATION,
        ),
        CommandDefinition(
            "dat.table.patch",
            TableDatPatchInput,
            CommandEffect.MUTATION,
            ExecutionClass.BOUNDED_MUTATION,
        ),
        CommandDefinition(
            "parameters.get",
            ParameterInput,
            CommandEffect.READ_ONLY,
            ExecutionClass.FAST_READ,
            True,
        ),
        CommandDefinition(
            "parameters.list",
            OperatorInput,
            CommandEffect.READ_ONLY,
            ExecutionClass.BOUNDED_SCAN_OR_EXPORT,
            True,
        ),
        CommandDefinition(
            "parameters.set",
            SetParameterInput,
            CommandEffect.MUTATION,
            ExecutionClass.BOUNDED_MUTATION,
        ),
        CommandDefinition(
            "parameters.pulse",
            ParameterInput,
            CommandEffect.MUTATION,
            ExecutionClass.BOUNDED_MUTATION,
        ),
        CommandDefinition(
            "parameters.sequence.get",
            SequenceInput,
            CommandEffect.READ_ONLY,
            ExecutionClass.BOUNDED_SCAN_OR_EXPORT,
            True,
        ),
        CommandDefinition(
            "parameters.sequence.replace",
            ReplaceSequenceInput,
            CommandEffect.MUTATION,
            ExecutionClass.BOUNDED_MUTATION,
        ),
        CommandDefinition(
            "ops.create",
            CreateOperatorInput,
            CommandEffect.MUTATION,
            ExecutionClass.BOUNDED_MUTATION,
        ),
        CommandDefinition(
            "ops.rename",
            RenameOperatorInput,
            CommandEffect.MUTATION,
            ExecutionClass.BOUNDED_MUTATION,
        ),
        CommandDefinition(
            "ops.destroy",
            DestroyOperatorInput,
            CommandEffect.MUTATION,
            ExecutionClass.BOUNDED_MUTATION,
        ),
        CommandDefinition(
            "ops.copy", CopyOperatorInput, CommandEffect.MUTATION, ExecutionClass.BOUNDED_MUTATION
        ),
        CommandDefinition(
            "ops.move", MoveOperatorInput, CommandEffect.MUTATION, ExecutionClass.BOUNDED_MUTATION
        ),
        CommandDefinition(
            "ops.tox.import",
            ImportToxInput,
            CommandEffect.MUTATION,
            ExecutionClass.TRUSTED_ASSET_MUTATION,
        ),
        CommandDefinition(
            "ops.connect",
            ConnectOperatorsInput,
            CommandEffect.MUTATION,
            ExecutionClass.BOUNDED_MUTATION,
        ),
        CommandDefinition(
            "ops.disconnect",
            DisconnectOperatorsInput,
            CommandEffect.MUTATION,
            ExecutionClass.BOUNDED_MUTATION,
        ),
        CommandDefinition(
            "ops.hierarchy.connect",
            ConnectOperatorsInput,
            CommandEffect.MUTATION,
            ExecutionClass.BOUNDED_MUTATION,
        ),
        CommandDefinition(
            "ops.hierarchy.disconnect",
            DisconnectOperatorsInput,
            CommandEffect.MUTATION,
            ExecutionClass.BOUNDED_MUTATION,
        ),
        CommandDefinition(
            "project.snapshot",
            SnapshotInput,
            CommandEffect.READ_ONLY,
            ExecutionClass.BOUNDED_SCAN_OR_EXPORT,
        ),
        CommandDefinition(
            "project.metadata",
            ProjectMetadataInput,
            CommandEffect.READ_ONLY,
            ExecutionClass.BOUNDED_SCAN_OR_EXPORT,
        ),
        CommandDefinition(
            "binary.export",
            BinaryExportInput,
            CommandEffect.READ_ONLY,
            ExecutionClass.BOUNDED_SCAN_OR_EXPORT,
        ),
        CommandDefinition(
            "events.read",
            EventsReadInput,
            CommandEffect.READ_ONLY,
            ExecutionClass.BOUNDED_SCAN_OR_EXPORT,
        ),
        CommandDefinition(
            "batch.execute",
            BatchExecuteInput,
            CommandEffect.READ_ONLY,
            ExecutionClass.BOUNDED_SCAN_OR_EXPORT,
        ),
    )
)


CommandInput = (
    OperatorInput
    | ChildrenInput
    | ConnectionsInput
    | InspectOperatorInput
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
    | ImportToxInput
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
