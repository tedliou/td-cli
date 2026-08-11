"""Canonical Agent Component extension source loaded by TouchDesigner."""

import base64
import builtins
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import ClassVar

if not hasattr(builtins, "_td_cli_runtime_session_id"):
    builtins._td_cli_runtime_session_id = str(uuid.uuid4())


class AgentCommandError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class OperatorCatalog:
    """Agent-side view of the embedded locked-build create catalog."""

    def __init__(self, manifest):
        self.touchdesigner_build = str(manifest["touchdesigner_build"])
        self.entries = {str(entry["op_type"]): entry for entry in manifest["operators"]}

    def require_creatable(self, op_type, allow_conditional=False):
        entry = self.entries.get(op_type)
        status = None if entry is None else entry.get("status")
        if status == "supported":
            return status
        if status == "conditional":
            if allow_conditional:
                return status
            raise AgentCommandError("operator_type_conditional")
        raise AgentCommandError("operator_type_unsupported")


class OperatorControl:
    """Execute typed Commands against one TouchDesigner Operator graph."""

    HANDLERS: ClassVar[dict] = {
        "ops.children": "_children",
        "ops.connect": "_connect_operators",
        "ops.connections": "_connections",
        "ops.copy": "_copy_operator",
        "ops.create": "_create_operator",
        "ops.disconnect": "_disconnect_operators",
        "ops.destroy": "_destroy_requested_operator",
        "ops.get": "_get_operator",
        "ops.move": "_move_operator",
        "ops.rename": "_rename_operator",
        "ops.state.get": "_get_operator_state",
        "ops.state.set": "_set_operator_state",
        "dat.text.get": "_get_text_dat",
        "dat.text.set": "_set_text_dat",
        "dat.table.get": "_get_table_dat",
        "dat.table.replace": "_replace_table_dat",
        "dat.table.patch": "_patch_table_dat",
        "parameters.get": "_get_parameter",
        "parameters.list": "_list_parameters",
        "parameters.pulse": "_pulse_parameter",
        "parameters.set": "_set_parameter",
        "parameters.sequence.get": "_get_parameter_sequence",
        "parameters.sequence.replace": "_replace_parameter_sequence",
    }
    STATE_FIELDS: ClassVar[tuple] = (
        ("node_x", "nodeX", "integer"),
        ("node_y", "nodeY", "integer"),
        ("node_width", "nodeWidth", "integer"),
        ("node_height", "nodeHeight", "integer"),
        ("color", "color", "color"),
        ("comment", "comment", "string"),
        ("bypass", "bypass", "boolean"),
        ("viewer", "viewer", "boolean"),
        ("expose", "expose", "boolean"),
        ("lock", "lock", "boolean"),
    )
    MAX_DAT_CONTENT_BYTES = 32_768
    MAX_TABLE_ROWS = 256
    MAX_TABLE_COLUMNS = 256
    MAX_TABLE_CELLS = 4096
    MAX_TABLE_CELL_BYTES = 16_384
    MAX_MULTI_OP_PATHS = 256
    MAX_SEQUENCE_BLOCKS = 128
    MAX_SEQUENCE_PARAMETERS = 256

    def __init__(self, operator_lookup, operator_catalog, protected_path=None):
        self.operator_lookup = operator_lookup
        self.operator_catalog = operator_catalog
        self.protected_path = str(protected_path) if protected_path is not None else None

    def execute(self, command):
        name = command["name"]
        payload = command["input"]
        handler_name = self.HANDLERS.get(name)
        if handler_name is None:
            raise AgentCommandError("command_unsupported")
        return getattr(self, handler_name)(payload)

    def _operator(self, payload):
        operator = self.operator_lookup(payload["operator_path"])
        if operator is None or str(operator.path) != payload["operator_path"]:
            raise AgentCommandError("operator_not_found")
        return operator

    def _get_operator(self, payload):
        return self._operator_result(self._operator(payload))

    def _get_text_dat(self, payload):
        operator = self._require_dat(payload, "textDAT")
        try:
            text = str(operator.text)
            byte_count = len(text.encode("utf-8"))
        except Exception as error:
            raise AgentCommandError("dat_content_unavailable") from error
        if byte_count > payload["max_bytes"]:
            raise AgentCommandError("result_too_large")
        return {
            "operator_path": str(operator.path),
            "dat_kind": "text",
            "text": text,
            "utf8_bytes": byte_count,
        }

    def _set_text_dat(self, payload):
        operator = self._require_dat(payload, "textDAT")
        path = str(operator.path)
        self._require_dat_mutable(operator)
        before = self._text_dat_snapshot(operator)

        def apply():
            operator.text = payload["text"]

        def read():
            return self._text_dat_snapshot(operator)

        text = self._mutate_dat(
            path,
            before,
            payload["text"],
            apply,
            read,
            lambda snapshot: setattr(operator, "text", snapshot),
            "text_dat",
        )
        return {
            "operator_path": path,
            "dat_kind": "text",
            "text": text,
            "utf8_bytes": len(text.encode("utf-8")),
        }

    def _get_table_dat(self, payload):
        operator = self._require_dat(payload, "tableDAT")
        try:
            total_rows = int(operator.numRows)
            total_columns = int(operator.numCols)
            row_stop = min(total_rows, payload["row_offset"] + payload["row_count"])
            column_stop = min(total_columns, payload["column_offset"] + payload["column_count"])
            rows = self._table_rows(
                operator,
                payload["row_offset"],
                row_stop,
                payload["column_offset"],
                column_stop,
            )
            byte_count = self._table_byte_count(rows)
            if any(
                len(cell.encode("utf-8")) > self.MAX_TABLE_CELL_BYTES
                for row in rows
                for cell in row
            ):
                raise AgentCommandError("result_too_large")
        except Exception as error:
            if isinstance(error, AgentCommandError):
                raise
            raise AgentCommandError("dat_content_unavailable") from error
        if byte_count > payload["max_bytes"]:
            raise AgentCommandError("result_too_large")
        return {
            "operator_path": str(operator.path),
            "dat_kind": "table",
            "total_rows": total_rows,
            "total_columns": total_columns,
            "row_offset": payload["row_offset"],
            "column_offset": payload["column_offset"],
            "rows": rows,
            "utf8_bytes": byte_count,
        }

    def _replace_table_dat(self, payload):
        operator = self._require_dat(payload, "tableDAT")
        return self._mutate_table_dat(operator, payload["rows"], "table_dat")

    def _patch_table_dat(self, payload):
        operator = self._require_dat(payload, "tableDAT")
        rows = payload["rows"]
        row_offset = payload["row_offset"]
        column_offset = payload["column_offset"]
        try:
            total_rows = int(operator.numRows)
            total_columns = int(operator.numCols)
        except Exception as error:
            raise AgentCommandError("dat_content_unavailable") from error
        if row_offset + len(rows) > total_rows or column_offset + len(rows[0]) > total_columns:
            raise AgentCommandError("table_dat_patch_out_of_bounds")
        path = str(operator.path)
        self._require_dat_mutable(operator)
        before = self._table_dat_snapshot(operator)
        expected = [list(row) for row in before]
        for row_index, row in enumerate(rows, start=row_offset):
            for column_index, value in enumerate(row, start=column_offset):
                expected[row_index][column_index] = value

        def apply():
            for row_index, row in enumerate(rows, start=row_offset):
                for column_index, value in enumerate(row, start=column_offset):
                    operator[row_index, column_index].val = value

        after = self._mutate_dat(
            path,
            before,
            expected,
            apply,
            lambda: self._table_dat_snapshot(operator),
            lambda snapshot: self._write_table_rows(operator, snapshot),
            "table_dat",
        )
        return self._complete_table_result(path, after)

    def _mutate_table_dat(self, operator, rows, error_prefix):
        path = str(operator.path)
        self._require_dat_mutable(operator)
        before = self._table_dat_snapshot(operator)
        after = self._mutate_dat(
            path,
            before,
            rows,
            lambda: self._write_table_rows(operator, rows),
            lambda: self._table_dat_snapshot(operator),
            lambda snapshot: self._write_table_rows(operator, snapshot),
            error_prefix,
        )
        return self._complete_table_result(path, after)

    def _mutate_dat(self, path, before, expected, apply, read, restore, error_prefix):
        try:
            apply()
            after = read()
            if after != expected:
                raise RuntimeError("DAT readback mismatch")
            return after
        except Exception as error:
            if self.operator_lookup(path) is None:
                raise AgentCommandError(f"{error_prefix}_outcome_unknown") from error
            try:
                restore(before)
                if read() != before:
                    raise RuntimeError("DAT rollback verification failed")
            except Exception as rollback_error:
                if self.operator_lookup(path) is None:
                    raise AgentCommandError(f"{error_prefix}_outcome_unknown") from rollback_error
                raise AgentCommandError(f"{error_prefix}_rollback_failed") from rollback_error
            raise AgentCommandError(f"{error_prefix}_write_failed") from error

    def _require_dat(self, payload, op_type):
        operator = self._operator(payload)
        if str(operator.OPType) != op_type:
            raise AgentCommandError("dat_type_mismatch")
        return operator

    def _require_dat_mutable(self, operator):
        self._require_mutable_path(str(operator.path))
        try:
            file_parameter = getattr(operator.par, "file", None)
            sync_parameter = getattr(operator.par, "syncfile", None)
            file_path = "" if file_parameter is None else str(file_parameter.eval() or "")
            sync_file = False if sync_parameter is None else bool(sync_parameter.eval())
            locked = bool(operator.lock)
            replicated = getattr(operator, "replicator", None) is not None
            cloned = self._has_clone_ancestor(operator)
        except Exception as error:
            raise AgentCommandError("dat_content_unavailable") from error
        if file_path or sync_file or locked or replicated or cloned:
            raise AgentCommandError("dat_content_not_writable")

    @staticmethod
    def _has_clone_ancestor(operator):
        parent_method = getattr(operator, "parent", None)
        ancestor = parent_method() if callable(parent_method) else None
        while ancestor is not None:
            clone_parameter = getattr(getattr(ancestor, "par", None), "clone", None)
            if clone_parameter is not None and str(clone_parameter.eval() or ""):
                return True
            parent_method = getattr(ancestor, "parent", None)
            ancestor = parent_method() if callable(parent_method) else None
        return False

    @classmethod
    def _text_dat_snapshot(cls, operator):
        try:
            text = str(operator.text)
        except Exception as error:
            raise AgentCommandError("dat_content_unavailable") from error
        if len(text.encode("utf-8")) > cls.MAX_DAT_CONTENT_BYTES:
            raise AgentCommandError("dat_content_too_large")
        return text

    @classmethod
    def _table_dat_snapshot(cls, operator):
        try:
            row_count = int(operator.numRows)
            column_count = int(operator.numCols)
            if (
                row_count > cls.MAX_TABLE_ROWS
                or column_count > cls.MAX_TABLE_COLUMNS
                or row_count * column_count > cls.MAX_TABLE_CELLS
            ):
                raise AgentCommandError("dat_content_too_large")
            rows = cls._table_rows(operator, 0, row_count, 0, column_count)
        except Exception as error:
            if isinstance(error, AgentCommandError):
                raise
            raise AgentCommandError("dat_content_unavailable") from error
        if cls._table_byte_count(rows) > cls.MAX_DAT_CONTENT_BYTES or any(
            len(cell.encode("utf-8")) > cls.MAX_TABLE_CELL_BYTES for row in rows for cell in row
        ):
            raise AgentCommandError("dat_content_too_large")
        return rows

    @staticmethod
    def _table_rows(operator, row_start, row_stop, column_start, column_stop):
        return [
            [str(operator[row, column].val) for column in range(column_start, column_stop)]
            for row in range(row_start, row_stop)
        ]

    @staticmethod
    def _write_table_rows(operator, rows):
        operator.clear()
        if not rows:
            return
        operator.setSize(len(rows), len(rows[0]))
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                operator[row_index, column_index].val = value

    @staticmethod
    def _table_byte_count(rows):
        return sum(len(cell.encode("utf-8")) for row in rows for cell in row)

    @classmethod
    def _complete_table_result(cls, path, rows):
        return {
            "operator_path": path,
            "dat_kind": "table",
            "total_rows": len(rows),
            "total_columns": len(rows[0]) if rows else 0,
            "rows": rows,
            "utf8_bytes": cls._table_byte_count(rows),
        }

    def _get_operator_state(self, payload):
        operator = self._operator(payload)
        try:
            state = self._operator_state(operator)
        except Exception as error:
            raise AgentCommandError("operator_state_unavailable") from error
        return {"operator_path": str(operator.path), "state": state}

    def _set_operator_state(self, payload):
        operator = self._operator(payload)
        path = str(operator.path)
        self._require_mutable_path(path)
        try:
            before = self._operator_state(operator)
        except Exception as error:
            raise AgentCommandError("operator_state_unavailable") from error
        applied_fields = []
        try:
            for field, _, _ in self.STATE_FIELDS:
                value = payload.get(field)
                if value is None:
                    continue
                applied_fields.append(field)
                self._apply_operator_state_field(operator, field, value)
            state = self._operator_state(operator)
            if not self._state_matches_patch(state, payload):
                raise AgentCommandError("operator_state_failed")
        except Exception as error:
            if self.operator_lookup(path) is None:
                raise AgentCommandError("operator_state_outcome_unknown") from error
            if not self._restore_operator_state(operator, before):
                raise AgentCommandError("operator_state_rollback_failed") from error
            if isinstance(error, AgentCommandError):
                raise
            raise AgentCommandError("operator_state_failed") from error
        return {
            "operator_path": path,
            "applied_fields": applied_fields,
            "state": state,
        }

    def _apply_operator_state_field(self, operator, field, value):
        descriptor = next(item for item in self.STATE_FIELDS if item[0] == field)
        _, attribute, kind = descriptor
        if kind == "color":
            value = (value["red"], value["green"], value["blue"])
        setattr(operator, attribute, value)

    def _restore_operator_state(self, operator, state):
        try:
            operator.lock = False
            for field, _, _ in self.STATE_FIELDS:
                if field != "lock":
                    self._apply_operator_state_field(operator, field, state[field])
            operator.lock = state["lock"]
            return self._state_matches_patch(self._operator_state(operator), state)
        except Exception:  # noqa: BLE001 - locked runtime can raise td-specific errors
            return False

    @staticmethod
    def _state_matches_patch(state, payload):
        for field, expected in payload.items():
            if field == "operator_path" or expected is None:
                continue
            actual = state[field]
            if field == "color":
                if any(
                    abs(actual[channel] - expected[channel]) > 0.000001
                    for channel in ("red", "green", "blue")
                ):
                    return False
            elif actual != expected:
                return False
        return True

    @classmethod
    def _operator_state(cls, operator):
        state = {}
        converters = {"integer": int, "string": str, "boolean": bool}
        for field, attribute, kind in cls.STATE_FIELDS:
            value = getattr(operator, attribute)
            if kind == "color":
                value = {
                    "red": float(value[0]),
                    "green": float(value[1]),
                    "blue": float(value[2]),
                }
            else:
                value = converters[kind](value)
            state[field] = value
        return state

    def _children(self, payload):
        operator = self._operator(payload)
        children = [self._operator_result(child) for child in operator.children]
        op_type = payload.get("op_type")
        if op_type is not None:
            children = [child for child in children if child["op_type"] == op_type]
        return sorted(children, key=lambda child: child["path"])

    def _connections(self, payload):
        operator = self._operator(payload)
        maximum = payload["max_connections"]
        count = len(self._subtree_connections([operator]))
        if count > maximum:
            raise AgentCommandError("result_too_large")
        inputs = []
        for connector in operator.inputConnectors:
            connection = connector.connections[0] if connector.connections else None
            inputs.append(
                {
                    "input_index": int(connector.index),
                    "description": str(getattr(connector, "description", "") or ""),
                    "connection": (
                        {
                            "source_path": str(connection.owner.path),
                            "output_index": int(connection.index),
                        }
                        if connection is not None
                        else None
                    ),
                }
            )
        outputs = []
        for connector in operator.outputConnectors:
            outputs.append(
                {
                    "output_index": int(connector.index),
                    "description": str(getattr(connector, "description", "") or ""),
                    "connections": [
                        {
                            "target_path": str(connection.owner.path),
                            "input_index": int(connection.index),
                        }
                        for connection in connector.connections
                    ],
                }
            )
        return {
            "operator_path": str(operator.path),
            "inputs": inputs,
            "outputs": outputs,
            "connection_count": count,
        }

    def _rename_operator(self, payload):
        operator = self._operator(payload)
        old_path = str(operator.path)
        if old_path == "/":
            raise AgentCommandError("operator_rename_forbidden")
        old_name = str(operator.name)
        new_name = payload["new_name"]
        new_path = old_path.rsplit("/", 1)[0] + "/" + new_name
        collision = self.operator_lookup(new_path)
        if collision is not None and collision is not operator:
            raise AgentCommandError("operator_already_exists")
        try:
            operator.name = new_name
            if (
                str(operator.name) != new_name
                or str(operator.path) != new_path
                or self.operator_lookup(new_path) is not operator
            ):
                raise AgentCommandError("operator_rename_failed")
        except Exception as error:
            try:
                operator.name = old_name
                if str(operator.name) != old_name or str(operator.path) != old_path:
                    raise RuntimeError("rename rollback verification failed")
            except Exception:  # noqa: BLE001 - preserve the uncertain mutation outcome
                raise AgentCommandError("operator_rename_rollback_failed") from error
            if isinstance(error, AgentCommandError):
                raise
            raise AgentCommandError("operator_rename_failed") from error
        return {
            "old_path": old_path,
            "path": new_path,
            "old_name": old_name,
            "name": new_name,
            "renamed": True,
        }

    def _destroy_requested_operator(self, payload):
        operator = self._operator(payload)
        path = str(operator.path)
        self._require_mutable_path(path)
        subtree = self._bounded_subtree(operator, payload["max_operators"])
        if len(subtree) > 1 and not payload["recursive"]:
            raise AgentCommandError("operator_not_empty")
        connections = self._subtree_connections(subtree)
        if connections and not payload["allow_connected"]:
            raise AgentCommandError("operator_connected")
        try:
            operator.destroy()
        except Exception as error:
            if self.operator_lookup(path) is None:
                raise AgentCommandError("operator_destroy_outcome_unknown") from error
            raise AgentCommandError("operator_destroy_failed") from error
        if self.operator_lookup(path) is not None:
            raise AgentCommandError("operator_destroy_failed")
        return {
            "operator_path": path,
            "operator_count": len(subtree),
            "detached_connections": connections,
            "destroyed": True,
        }

    def _copy_operator(self, payload):
        source = self.operator_lookup(payload["source_path"])
        target_parent = self.operator_lookup(payload["target_parent_path"])
        if source is None or target_parent is None:
            raise AgentCommandError("operator_not_found")
        self._require_mutable_path(str(source.path))
        self._require_mutable_destination(str(target_parent.path))
        if str(target_parent.family) != "COMP":
            raise AgentCommandError("operator_parent_invalid")
        expected_path = str(target_parent.path).rstrip("/") + "/" + payload["new_name"]
        if self.operator_lookup(expected_path) is not None:
            raise AgentCommandError("operator_already_exists")
        source_subtree = self._bounded_subtree(source, payload["max_operators"])
        if source.docked and not payload["include_docked"]:
            raise AgentCommandError("operator_docked")
        unreplicated = self._boundary_connections(source_subtree)
        before = {str(child.path) for child in target_parent.children}
        created = None
        created_roots = []
        try:
            created = target_parent.copy(
                source,
                name=payload["new_name"],
                includeDocked=payload["include_docked"],
            )
            created_roots = [
                child for child in target_parent.children if str(child.path) not in before
            ]
            if (
                str(created.path) != expected_path
                or str(created.name) != payload["new_name"]
                or str(created.OPType) != str(source.OPType)
                or str(created.family) != str(source.family)
            ):
                raise AgentCommandError("operator_copy_failed")
            affected = self._bounded_forest(created_roots, payload["max_operators"])
            if created not in affected:
                raise AgentCommandError("operator_copy_failed")
            if payload.get("node_x") is not None:
                created.nodeX = payload["node_x"]
                if int(created.nodeX) != payload["node_x"]:
                    raise AgentCommandError("operator_copy_failed")
            if payload.get("node_y") is not None:
                created.nodeY = payload["node_y"]
                if int(created.nodeY) != payload["node_y"]:
                    raise AgentCommandError("operator_copy_failed")
        except Exception as error:
            created_roots = [
                child for child in target_parent.children if str(child.path) not in before
            ]
            if created is not None and created not in created_roots:
                created_roots.append(created)
            if not self._rollback_created(created_roots):
                raise AgentCommandError("operator_copy_rollback_failed") from error
            if isinstance(error, AgentCommandError):
                raise
            raise AgentCommandError("operator_copy_failed") from error
        return {
            "source_path": str(source.path),
            **self._operator_result(created),
            "operator_count": len(affected),
            "include_docked": payload["include_docked"],
            "unreplicated_connections": unreplicated,
        }

    def _move_operator(self, payload):
        source = self.operator_lookup(payload["source_path"])
        if source is None:
            raise AgentCommandError("operator_not_found")
        old_path = str(source.path)
        target_parent_path = payload["target_parent_path"]
        if target_parent_path.startswith(old_path.rstrip("/") + "/"):
            raise AgentCommandError("operator_parent_invalid")
        source_subtree = self._bounded_subtree(source, payload["max_operators"])
        detached = self._boundary_connections(source_subtree)
        if detached and not payload["allow_connected"]:
            raise AgentCommandError("operator_connected")
        copied = self._copy_operator(
            {
                "source_path": old_path,
                "target_parent_path": target_parent_path,
                "new_name": payload["new_name"],
                "node_x": payload.get("node_x"),
                "node_y": payload.get("node_y"),
                "include_docked": False,
                "max_operators": payload["max_operators"],
            }
        )
        created = self.operator_lookup(copied["path"])
        try:
            source.destroy()
        except Exception as error:
            if self.operator_lookup(old_path) is not None:
                if created is None or not self._rollback_created([created]):
                    raise AgentCommandError("operator_move_rollback_failed") from error
                raise AgentCommandError("operator_move_failed") from error
        if self.operator_lookup(old_path) is not None:
            if created is None or not self._rollback_created([created]):
                raise AgentCommandError("operator_move_rollback_failed")
            raise AgentCommandError("operator_move_failed")
        if self.operator_lookup(copied["path"]) is None:
            raise AgentCommandError("operator_move_outcome_unknown")
        return {
            "old_path": old_path,
            "path": copied["path"],
            "name": copied["name"],
            "op_type": copied["op_type"],
            "family": copied["family"],
            "operator_count": copied["operator_count"],
            "detached_connections": detached,
            "identity_preserved": False,
            "moved": True,
        }

    def _require_mutable_path(self, path):
        if path == "/":
            raise AgentCommandError("operator_mutation_forbidden")
        protected = self.protected_path
        if protected is not None and (
            protected == path
            or protected.startswith(path + "/")
            or path.startswith(protected + "/")
        ):
            raise AgentCommandError("operator_mutation_forbidden")

    def _require_mutable_destination(self, path):
        protected = self.protected_path
        if protected is not None and (path == protected or path.startswith(protected + "/")):
            raise AgentCommandError("operator_mutation_forbidden")

    @staticmethod
    def _bounded_subtree(root, maximum):
        rows = []
        queue = [root]
        while queue:
            operator = queue.pop(0)
            if len(rows) >= maximum:
                raise AgentCommandError("result_too_large")
            rows.append(operator)
            queue.extend(list(getattr(operator, "children", [])))
        return rows

    @classmethod
    def _bounded_forest(cls, roots, maximum):
        rows = []
        for root in roots:
            remaining = maximum - len(rows)
            if remaining <= 0:
                raise AgentCommandError("result_too_large")
            rows.extend(cls._bounded_subtree(root, remaining))
        return rows

    @staticmethod
    def _subtree_connections(subtree):
        edges = set()
        for operator in subtree:
            for connector in operator.inputConnectors:
                for connection in connector.connections:
                    edges.add(
                        (
                            str(connection.owner.path),
                            int(connection.index),
                            str(operator.path),
                            int(connector.index),
                        )
                    )
            for connector in operator.outputConnectors:
                for connection in connector.connections:
                    edges.add(
                        (
                            str(operator.path),
                            int(connector.index),
                            str(connection.owner.path),
                            int(connection.index),
                        )
                    )
        return [
            {
                "source_path": source_path,
                "output_index": output_index,
                "target_path": target_path,
                "input_index": input_index,
            }
            for source_path, output_index, target_path, input_index in sorted(edges)
        ]

    @classmethod
    def _boundary_connections(cls, subtree):
        paths = {str(operator.path) for operator in subtree}
        return [
            edge
            for edge in cls._subtree_connections(subtree)
            if (edge["source_path"] in paths) != (edge["target_path"] in paths)
        ]

    def _rollback_created(self, roots):
        paths = [str(root.path) for root in roots]
        for root in reversed(roots):
            self._destroy_operator(root)
        return all(self.operator_lookup(path) is None for path in paths)

    def _parameter_for_payload(self, payload):
        operator = self._operator(payload)
        parameter = self._parameter(operator, payload["parameter"])
        return operator, parameter

    def _get_parameter(self, payload):
        operator, parameter = self._parameter_for_payload(payload)
        self._preflight_parameter("parameters.get", operator, payload, parameter)
        return self._parameter_result(operator, payload["parameter"], parameter)

    def _pulse_parameter(self, payload):
        operator, parameter = self._parameter_for_payload(payload)
        self._preflight_parameter("parameters.pulse", operator, payload, parameter)
        parameter.pulse()
        return {
            "operator_path": str(operator.path),
            "parameter": payload["parameter"],
            "pulsed": True,
        }

    def _list_parameters(self, payload):
        operator = self._operator(payload)
        builtin = list(getattr(operator, "builtinPars", []))
        custom = list(getattr(operator, "customPars", []))
        builtin_ids = {id(parameter) for parameter in builtin}
        custom_ids = {id(parameter) for parameter in custom}
        parameters = [
            self._parameter_metadata(
                parameter,
                builtin=id(parameter) in builtin_ids,
                custom=id(parameter) in custom_ids,
            )
            for parameter in builtin + custom
        ]
        return {
            "operator_path": str(operator.path),
            "parameters": sorted(parameters, key=lambda item: item["name"]),
        }

    @classmethod
    def _parameter_metadata(cls, parameter, *, builtin, custom):
        value_kind = cls._parameter_value_kind(parameter)
        read_only = bool(getattr(parameter, "readOnly", False))
        enabled = bool(getattr(parameter, "enable", True))
        hidden = bool(getattr(parameter, "hidden", False))
        writable = not read_only and enabled and not hidden
        constant_supported = writable and value_kind in {
            "boolean",
            "integer",
            "number",
            "string",
            "menu",
            "operator",
        }
        expression_supported = constant_supported
        pulse_supported = writable and value_kind == "pulse"
        page = getattr(parameter, "page", None)
        menu = value_kind == "menu"
        expression_source = getattr(parameter, "expr", None)
        return {
            "name": str(parameter.name),
            "label": str(parameter.label),
            "page": str(page.name) if page is not None else None,
            "style": str(parameter.style),
            "builtin": bool(builtin),
            "custom": bool(custom),
            "hidden": hidden,
            "enabled": enabled,
            "read_only": read_only,
            "mode": cls._parameter_mode(parameter),
            "value_kind": value_kind,
            "constant_supported": constant_supported,
            "expression_supported": expression_supported,
            "pulse_supported": pulse_supported,
            "export_supported": writable and value_kind in {"boolean", "integer", "number"},
            "bind_supported": writable and value_kind not in {"pulse", "python", "sequence", "unknown"},
            "unsupported_reason": cls._parameter_unsupported_reason(
                value_kind, read_only=read_only, enabled=enabled, hidden=hidden
            ),
            "sequence": cls._parameter_sequence_identity(parameter),
            "source": cls._parameter_source(parameter, cls._parameter_mode(parameter)),
            "bounds": cls._parameter_bounds(parameter, value_kind),
            "max_operator_paths": cls.MAX_MULTI_OP_PATHS
            if value_kind == "multi_operator"
            else None,
            "expression": {
                "supported": expression_supported,
                "source": str(expression_source)
                if expression_supported and expression_source not in (None, "")
                else None,
            },
            "menu_names": [str(value) for value in (getattr(parameter, "menuNames", None) or [])]
            if menu
            else None,
            "menu_labels": [str(value) for value in (getattr(parameter, "menuLabels", None) or [])]
            if menu
            else None,
        }

    @staticmethod
    def _parameter_bounds(parameter, value_kind):
        if value_kind not in {"integer", "number"}:
            return None
        return {
            "minimum": float(getattr(parameter, "min", 0)),
            "maximum": float(getattr(parameter, "max", 1)),
            "clamp_min": bool(getattr(parameter, "clampMin", False)),
            "clamp_max": bool(getattr(parameter, "clampMax", False)),
            "normal_minimum": float(getattr(parameter, "normMin", 0)),
            "normal_maximum": float(getattr(parameter, "normMax", 1)),
        }

    @staticmethod
    def _parameter_unsupported_reason(value_kind, *, read_only, enabled, hidden):
        if hidden:
            return "obsolete"
        if read_only:
            return "read_only"
        if not enabled:
            return "disabled"
        if value_kind in {"python", "sequence", "unknown"}:
            return "opaque_or_structural"
        return None

    @staticmethod
    def _parameter_sequence_identity(parameter):
        sequence = getattr(parameter, "sequence", None)
        block = getattr(parameter, "sequenceBlock", None)
        if sequence is None:
            return None
        return {
            "name": str(sequence.name),
            "block_index": int(block.index) if block is not None else None,
        }

    def _get_parameter_sequence(self, payload):
        operator = self._operator(payload)
        sequence = self._sequence(operator, payload["sequence"])
        return self._sequence_result(operator, sequence)

    def _replace_parameter_sequence(self, payload):
        operator = self._operator(payload)
        sequence = self._sequence(operator, payload["sequence"])
        before = self._sequence_result(operator, sequence)
        try:
            self._apply_sequence_blocks(sequence, payload["blocks"])
            result = self._sequence_result(operator, sequence)
            if result["blocks"] != payload["blocks"]:
                raise RuntimeError("sequence readback mismatch")
            return result
        except Exception as error:
            try:
                current = self._sequence(operator, payload["sequence"])
                self._apply_sequence_blocks(current, before["blocks"])
                if self._sequence_result(operator, current)["blocks"] != before["blocks"]:
                    raise RuntimeError("sequence rollback mismatch")
            except AgentCommandError as rollback_error:
                if rollback_error.code in {"operator_not_found", "parameter_sequence_not_found"}:
                    raise AgentCommandError("parameter_sequence_outcome_unknown") from error
                raise AgentCommandError("parameter_sequence_rollback_failed") from rollback_error
            except Exception as rollback_error:
                raise AgentCommandError("parameter_sequence_rollback_failed") from rollback_error
            raise AgentCommandError("parameter_sequence_write_failed") from error

    @staticmethod
    def _sequence(operator, name):
        collection = getattr(operator, "seq", None)
        try:
            sequence = collection[name] if collection is not None else None
        except Exception:  # noqa: BLE001 - TD collections may raise tdError subclasses
            sequence = getattr(collection, name, None)
        if sequence is None:
            raise AgentCommandError("parameter_sequence_not_found")
        return sequence

    @classmethod
    def _sequence_result(cls, operator, sequence):
        blocks = []
        for block in list(sequence.blocks):
            parameters = []
            for group in list(block):
                for parameter in list(group):
                    if bool(getattr(parameter, "isSequence", False)):
                        continue
                    if getattr(block, "namePar", None) is not None and parameter.isSamePar(
                        block.namePar
                    ):
                        continue
                    item = cls._parameter_result(operator, str(parameter.name), parameter)
                    prefix = f"{sequence.name}{int(block.index)}"
                    local_name = str(parameter.name).removeprefix(prefix)
                    descriptor = {
                        "parameter": local_name,
                        "mode": item["mode"],
                        "value": item["value"]
                        if item["mode"] in {"constant", "expression"}
                        else None,
                    }
                    if item["source"] is not None:
                        descriptor["source"] = item["source"]
                    parameters.append(descriptor)
            blocks.append(
                {
                    "name": str(block.name) if getattr(block, "namePar", None) is not None else None,
                    "parameters": parameters,
                }
            )
        return {
            "operator_path": str(operator.path),
            "sequence": str(sequence.name),
            "block_size": int(sequence.blockSize),
            "max_blocks": int(sequence.maxBlocks) if sequence.maxBlocks is not None else None,
            "blocks": blocks,
        }

    def _apply_sequence_blocks(self, sequence, blocks):
        if len(blocks) > self.MAX_SEQUENCE_BLOCKS:
            raise AgentCommandError("parameter_sequence_too_large")
        sequence.numBlocks = len(blocks)
        if int(sequence.numBlocks) != len(blocks):
            raise RuntimeError("sequence block count rejected")
        for index, block_input in enumerate(blocks):
            block = sequence.blocks[index]
            if block_input.get("name") is not None:
                block.name = block_input["name"]
            for item in block_input["parameters"]:
                try:
                    parameter = block.par[item["parameter"]]
                except Exception as error:
                    raise AgentCommandError("parameter_not_found") from error
                payload = {
                    "operator_path": str(sequence.owner.path),
                    "parameter": str(parameter.name),
                    "mode": item["mode"],
                    "value": item.get("value"),
                }
                if item.get("source") is not None:
                    payload["source"] = item["source"]
                self._set_parameter(payload)

    @staticmethod
    def _parameter_mode(parameter):
        mode = str(parameter.mode).lower()
        for known in ("expression", "export", "bind"):
            if known in mode:
                return known
        return "constant"

    @staticmethod
    def _parameter_value_kind(parameter):
        if bool(getattr(parameter, "isSequence", False)):
            return "sequence"
        if bool(getattr(parameter, "isPython", False)):
            return "python"
        if bool(getattr(parameter, "isOP", False)):
            style = str(getattr(parameter, "style", "")).lower()
            return "multi_operator" if "multi" in style else "operator"
        checks = (
            ("isPulse", "pulse"),
            ("isMenu", "menu"),
            ("isToggle", "boolean"),
            ("isInt", "integer"),
            ("isFloat", "number"),
            ("isNumber", "number"),
            ("isString", "string"),
        )
        return next(
            (kind for attribute, kind in checks if bool(getattr(parameter, attribute, False))),
            "unknown",
        )

    def preflight(self, command):
        name = command["name"]
        payload = command["input"]
        operator = self.operator_lookup(payload["operator_path"])
        if operator is None:
            raise AgentCommandError("operator_not_found")
        if name.startswith("parameters.") and "parameter" in payload:
            parameter = self._parameter(operator, payload["parameter"])
            self._preflight_parameter(name, operator, payload, parameter)

    def _create_operator(self, payload):
        catalog_status = self.operator_catalog.require_creatable(
            payload["op_type"], payload.get("allow_conditional", False)
        )
        parent = self.operator_lookup(payload["parent_path"])
        if parent is None:
            raise AgentCommandError("operator_not_found")
        if str(parent.family) != "COMP":
            raise AgentCommandError("operator_parent_invalid")
        expected_path = str(parent.path).rstrip("/") + "/" + payload["name"]
        if self.operator_lookup(expected_path) is not None:
            raise AgentCommandError("operator_already_exists")
        created = None
        try:
            created = parent.create(payload["op_type"], payload["name"])
            if str(created.path) != expected_path or str(created.name) != payload["name"]:
                raise AgentCommandError("operator_create_failed")
            created.nodeX = payload["node_x"]
            created.nodeY = payload["node_y"]
        except AgentCommandError:
            self._destroy_operator(created)
            raise
        except Exception:  # noqa: BLE001 - TouchDesigner raises tdError subclasses
            self._destroy_operator(created)
            raise AgentCommandError("operator_create_failed")
        return {**self._operator_result(created), "catalog_status": catalog_status}

    @staticmethod
    def _destroy_operator(operator):
        if operator is None:
            return
        try:
            operator.destroy()
        except Exception:  # noqa: BLE001, S110 - rollback is best effort
            pass

    def _connect_operators(self, payload):
        source, target, output_index, input_index = self._connector_endpoints(payload)
        source_connector = source.outputConnectors[output_index]
        target_connector = target.inputConnectors[input_index]
        replace = payload.get("replace", False)
        previous = target_connector.connections[0] if target_connector.connections else None
        previous_connection = (
            {"source_path": str(previous.owner.path), "output_index": int(previous.index)}
            if previous is not None
            else None
        )
        if previous is not None and not replace:
            raise AgentCommandError("connector_occupied")
        if previous is not None and self._connection_matches(previous, source, output_index):
            return {
                "source_path": str(source.path),
                "target_path": str(target.path),
                "output_index": output_index,
                "input_index": input_index,
                "connected": True,
                "replaced": False,
                "previous_connection": previous_connection,
            }
        try:
            if previous is not None:
                target_connector.connect(source_connector)
            else:
                source_connector.connect(target_connector)
            if not any(
                self._connection_matches(connection, source, output_index)
                for connection in target_connector.connections
            ):
                raise RuntimeError("connection verification failed")
        except Exception as error:
            self._disconnect_connector(target_connector)
            if previous is not None:
                try:
                    previous_source = previous.owner.outputConnectors[int(previous.index)]
                    target_connector.connect(previous_source)
                    if not any(
                        self._connection_matches(connection, previous.owner, int(previous.index))
                        for connection in target_connector.connections
                    ):
                        raise RuntimeError("replace rollback verification failed")
                except Exception:  # noqa: BLE001 - uncertain graph mutation outcome
                    raise AgentCommandError("connector_replace_rollback_failed") from error
                raise AgentCommandError("connector_replace_failed") from error
            raise AgentCommandError("connector_connect_failed") from error
        return {
            "source_path": str(source.path),
            "target_path": str(target.path),
            "output_index": output_index,
            "input_index": input_index,
            "connected": True,
            "replaced": previous is not None,
            "previous_connection": previous_connection,
        }

    def _disconnect_operators(self, payload):
        source, target, output_index, input_index = self._connector_endpoints(payload)
        target_connector = target.inputConnectors[input_index]
        if not any(
            self._connection_matches(connection, source, output_index)
            for connection in target_connector.connections
        ):
            raise AgentCommandError("connection_not_found")
        try:
            target_connector.disconnect()
        except Exception as error:
            raise AgentCommandError("connector_disconnect_failed") from error
        if any(
            self._connection_matches(connection, source, output_index)
            for connection in target_connector.connections
        ):
            raise AgentCommandError("connector_disconnect_failed")
        return {
            "source_path": str(source.path),
            "target_path": str(target.path),
            "output_index": output_index,
            "input_index": input_index,
            "disconnected": True,
        }

    def _connector_endpoints(self, payload):
        source = self.operator_lookup(payload["source_path"])
        target = self.operator_lookup(payload["target_path"])
        if source is None or target is None:
            raise AgentCommandError("operator_not_found")
        if str(source.family) != str(target.family):
            raise AgentCommandError("operator_family_mismatch")
        output_index = payload["output_index"]
        input_index = payload["input_index"]
        if output_index >= len(source.outputConnectors) or input_index >= len(
            target.inputConnectors
        ):
            raise AgentCommandError("connector_not_found")
        return source, target, output_index, input_index

    @staticmethod
    def _connection_matches(connection, source, output_index):
        return (
            str(connection.owner.path) == str(source.path)
            and int(connection.index) == output_index
            and bool(connection.isOutput)
        )

    @staticmethod
    def _disconnect_connector(connector):
        try:
            connector.disconnect()
        except Exception:  # noqa: BLE001, S110 - rollback is best effort
            pass

    def _preflight_parameter(self, name, operator, payload, parameter):
        if name in {"parameters.get", "parameters.set"}:
            self._parameter_result(operator, payload["parameter"], parameter)
        if name == "parameters.set":
            if getattr(parameter, "readOnly", False):
                raise AgentCommandError("parameter_read_only")
            if not bool(getattr(parameter, "enable", True)):
                raise AgentCommandError("parameter_disabled")
            if bool(getattr(parameter, "hidden", False)):
                raise AgentCommandError("parameter_obsolete")
            self._validate_parameter_write(parameter, payload)
            if payload["mode"] == "expression":
                try:
                    compile(payload["value"], "<parameter-expression>", "eval")
                except SyntaxError:
                    raise AgentCommandError("expression_invalid")
        if name == "parameters.pulse" and not getattr(parameter, "isPulse", False):
            raise AgentCommandError("parameter_not_pulseable")
        if name == "parameters.pulse":
            if getattr(parameter, "readOnly", False):
                raise AgentCommandError("parameter_read_only")
            if not bool(getattr(parameter, "enable", True)):
                raise AgentCommandError("parameter_disabled")
            if bool(getattr(parameter, "hidden", False)):
                raise AgentCommandError("parameter_obsolete")

    @classmethod
    def _validate_parameter_write(cls, parameter, payload):
        kind = cls._parameter_value_kind(parameter)
        mode = payload["mode"]
        if kind in {"pulse", "python", "sequence", "unknown"}:
            raise AgentCommandError("parameter_type_unsupported")
        if mode == "export" and kind not in {"boolean", "integer", "number"}:
            raise AgentCommandError("parameter_type_unsupported")
        value = payload.get("value")
        if mode != "constant":
            return
        expected = {
            "boolean": bool,
            "integer": int,
            "number": (int, float),
            "string": str,
            "menu": str,
        }.get(kind)
        if expected is not None and (not isinstance(value, expected) or kind == "integer" and type(value) is bool):
            raise AgentCommandError("parameter_value_invalid")
        if kind == "menu" and value not in list(getattr(parameter, "menuNames", []) or []):
            raise AgentCommandError("parameter_value_invalid")

    def _set_parameter(self, payload):
        operator, parameter = self._parameter_for_payload(payload)
        self._preflight_parameter("parameters.set", operator, payload, parameter)
        before = self._parameter_snapshot(parameter)
        try:
            if payload["mode"] == "expression":
                parameter.expr = payload["value"]
            elif payload["mode"] == "bind":
                source = payload["source"]
                source_operator = self.operator_lookup(source["operator_path"])
                if source_operator is None or self._parameter(
                    source_operator, source["parameter"]
                ) is None:
                    raise AgentCommandError("parameter_source_not_found")
                parameter.bindExpr = "op({!r}).par.{}".format(
                    source["operator_path"], source["parameter"]
                )
            elif payload["mode"] == "export":
                self._activate_parameter_export(parameter, payload["source"])
            else:
                parameter.val = self._parameter_constant_for_write(parameter, payload["value"])
        except AgentCommandError:
            raise
        except Exception as error:
            code = (
                "expression_invalid"
                if payload["mode"] == "expression"
                else "parameter_write_rejected"
            )
            self._rollback_parameter(operator, payload["parameter"], before, error)
            raise AgentCommandError(code) from error
        try:
            result = self._parameter_result(operator, payload["parameter"], parameter)
            expected = (
                self._canonical_parameter_source(payload.get("source"))
                if payload["mode"] in {"export", "bind"}
                else payload["value"]
            )
            actual = result.get("source") if payload["mode"] in {"export", "bind"} else result["value"]
            if result["mode"] != payload["mode"] or actual != expected:
                raise RuntimeError("parameter readback mismatch")
        except Exception as error:
            self._rollback_parameter(operator, payload["parameter"], before, error)
            raise AgentCommandError("parameter_write_rejected") from error
        return result

    @staticmethod
    def _canonical_parameter_source(source):
        if source is None:
            return None
        return {
            "kind": source["kind"],
            "operator_path": source["operator_path"],
            "channel": source.get("channel"),
            "parameter": source.get("parameter"),
        }

    def _parameter_constant_for_write(self, parameter, value):
        kind = self._parameter_value_kind(parameter)
        if kind not in {"operator", "multi_operator"}:
            return value
        paths = value if isinstance(value, list) else ([] if value is None else [value])
        if kind == "operator" and len(paths) > 1:
            raise AgentCommandError("parameter_value_invalid")
        operators = []
        for path in paths:
            target = self.operator_lookup(path)
            if target is None or str(target.path) != path:
                raise AgentCommandError("parameter_source_not_found")
            operators.append(target)
        if kind == "multi_operator":
            return operators
        return operators[0] if operators else None

    def _activate_parameter_export(self, parameter, source):
        source_operator = self.operator_lookup(source["operator_path"])
        if source_operator is None:
            raise AgentCommandError("parameter_source_not_found")
        try:
            channel = source_operator[source["channel"]]
        except Exception as error:
            raise AgentCommandError("parameter_source_not_found") from error
        if channel is None:
            raise AgentCommandError("parameter_source_not_found")
        current = getattr(parameter, "exportSource", None)
        current_owner = getattr(current, "owner", None)
        if (
            current is None
            or current_owner is None
            or str(current_owner.path) != source["operator_path"]
            or str(current.name) != source["channel"]
        ):
            raise AgentCommandError("parameter_export_source_unavailable")
        parameter.mode = getattr(getattr(builtins, "ParMode", None), "EXPORT", "export")

    @classmethod
    def _parameter_snapshot(cls, parameter):
        return {
            "mode": cls._parameter_mode(parameter),
            "raw_mode": getattr(parameter, "mode", None),
            "val": getattr(parameter, "val", None),
            "expr": getattr(parameter, "expr", ""),
            "bind_expr": getattr(parameter, "bindExpr", ""),
        }

    def _rollback_parameter(self, operator, name, before, cause):
        try:
            parameter = self._parameter(operator, name)
            mode = before["mode"]
            if mode == "expression":
                parameter.expr = before["expr"]
            elif mode == "bind":
                parameter.bindExpr = before["bind_expr"]
            elif mode == "export":
                parameter.mode = before["raw_mode"]
            else:
                parameter.val = before["val"]
            restored = self._parameter_snapshot(parameter)
            if restored["mode"] != mode:
                raise RuntimeError("rollback mode mismatch")
        except AgentCommandError as error:
            if error.code == "parameter_not_found":
                raise AgentCommandError("parameter_outcome_unknown") from cause
            raise
        except Exception as error:
            raise AgentCommandError("parameter_rollback_failed") from error

    @staticmethod
    def _operator_result(operator):
        return {
            "path": str(operator.path),
            "name": str(operator.name),
            "op_type": str(operator.OPType),
            "family": str(operator.family),
        }

    @staticmethod
    def _parameter(operator, name):
        try:
            parameter = getattr(operator.par, name)
        except Exception:  # noqa: BLE001 - missing TD parameters raise tdAttributeError
            parameter = None
        if parameter is None:
            raise AgentCommandError("parameter_not_found")
        return parameter

    @classmethod
    def _parameter_result(cls, operator, name, parameter):
        mode = cls._parameter_mode(parameter)
        kind = cls._parameter_value_kind(parameter)
        source = cls._parameter_source(parameter, mode)
        if kind in {"python", "sequence", "unknown"}:
            value = None
        elif mode == "expression":
            value = parameter.expr
        elif kind in {"operator", "multi_operator"}:
            try:
                operators = list(parameter.evalOPs())
            except Exception as error:
                raise AgentCommandError("parameter_type_unsupported") from error
            paths = [str(item.path) for item in operators]
            value = paths if kind == "multi_operator" else (paths[0] if paths else None)
        else:
            value = parameter.eval()
        if kind in {"python", "sequence", "unknown"} and value is None:
            value_type = kind
        elif kind == "operator" and (value is None or type(value) is str):
            value_type = "operator"
        elif kind == "multi_operator" and isinstance(value, list):
            value_type = "multi_operator"
        elif type(value) is bool:
            value_type = "boolean"
        elif type(value) is int:
            value_type = "integer"
        elif type(value) is float:
            value_type = "number"
        elif type(value) is str:
            value_type = "string"
        else:
            raise AgentCommandError("parameter_type_unsupported")
        return {
            "operator_path": str(operator.path),
            "parameter": name,
            "mode": mode,
            "value": value,
            "value_type": value_type,
            "source": source,
            "unsupported_reason": "opaque_or_structural"
            if kind in {"python", "sequence", "unknown"}
            else None,
        }

    @staticmethod
    def _parameter_source(parameter, mode):
        if mode == "export":
            source = getattr(parameter, "exportSource", None)
            owner = getattr(source, "owner", None)
            if source is None or owner is None:
                return None
            return {
                "kind": "export_channel",
                "operator_path": str(owner.path),
                "channel": str(source.name),
                "parameter": None,
            }
        if mode == "bind":
            master = getattr(parameter, "bindMaster", None)
            owner = getattr(master, "owner", None)
            if master is None or owner is None:
                return None
            return {
                "kind": "bind_parameter",
                "operator_path": str(owner.path),
                "channel": None,
                "parameter": str(master.name),
            }
        return None


class AgentExt:
    MAX_UNCONFIRMED_RESULTS = 256
    MAX_RESULT_BYTES = 256 * 1024

    CAPABILITIES = tuple(OperatorControl.HANDLERS) + (
        "batch.execute",
        "binary.export",
        "events.read",
        "project.metadata",
        "project.snapshot",
    )

    def __init__(self, owner_comp, operator_lookup=None, project_info=None, app_info=None):
        self.owner_comp = owner_comp
        self.operator_lookup = operator_lookup or (lambda path: op(path))
        self.project_info = project_info or getattr(builtins, "project", None)
        self.app_info = app_info or getattr(builtins, "app", None)
        if self.app_info is None or not hasattr(self.app_info, "build"):
            raise RuntimeError("TouchDesigner app build is required")
        manifest_dat = owner_comp.op("agent_manifest")
        catalog_dat = owner_comp.op("operator_catalog")
        if manifest_dat is None or catalog_dat is None:
            raise RuntimeError("Agent manifest and Operator catalog DATs are required")
        try:
            manifest = json.loads(manifest_dat.text)
            self.agent_version = str(manifest["agent_version"])
            self.protocol_versions = [int(version) for version in manifest["protocol_versions"]]
            operator_catalog = OperatorCatalog(json.loads(catalog_dat.text))
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("Operator catalog DAT is invalid") from error
        if operator_catalog.touchdesigner_build != str(self.app_info.build):
            raise RuntimeError("Operator catalog TouchDesigner build does not match runtime")
        self.operator_control = OperatorControl(
            self.operator_lookup, operator_catalog, protected_path=owner_comp.path
        )
        runtime_session_id = builtins._td_cli_runtime_session_id
        state = getattr(builtins, "_td_cli_agent_state", None)
        if state is None or state["runtime_session_id"] != runtime_session_id:
            state = {
                "runtime_session_id": runtime_session_id,
                "instance_id": str(uuid.uuid4()),
                "pending_results": {},
                "seen_commands": {},
                "events": [],
                "next_event_id": 1,
            }
            builtins._td_cli_agent_state = state
        self.instance_id = state["instance_id"]
        self.pending_results = state["pending_results"]
        self.seen_commands = state["seen_commands"]
        self.events = state.setdefault("events", [])
        state.setdefault("next_event_id", 1)
        self._state = state
        self.connection_id = None
        self.draining = False
        self.last_heartbeat_at = 0.0

    def registration_payload(self):
        return {
            "instance_id": self.instance_id,
            "agent_version": self.agent_version,
            "td_build": str(self.app_info.build),
            "protocol_versions": self.protocol_versions,
            "capabilities": list(self.CAPABILITIES),
            "status": "draining" if self.draining else "online",
        }

    def heartbeat_payload(self):
        return {
            "instance_id": self.instance_id,
            "connection_id": self.connection_id,
            "status": "draining" if self.draining else "online",
        }

    def accept(self, request):
        request_id = request["request_id"]
        canonical = json.dumps(request["command"], separators=(",", ":"), sort_keys=True)
        if request_id in self.seen_commands:
            if self.seen_commands[request_id] != canonical:
                return "request_rejected", {"request_id": request_id, "code": "request_id_conflict"}
            if request_id in self.pending_results:
                return "request_result", self.pending_results[request_id]
        if self.draining:
            return "request_rejected", {"request_id": request_id, "code": "instance_draining"}
        if len(self.pending_results) >= self.MAX_UNCONFIRMED_RESULTS:
            return "request_rejected", {"request_id": request_id, "code": "result_buffer_full"}
        self.seen_commands[request_id] = canonical
        try:
            command = request["command"]
            command_result = self.execute_command(command)
        except AgentCommandError as error:
            self._record_event("command.failed", request_id, error.code)
            return "request_rejected", {"request_id": request_id, "code": error.code}
        except Exception:  # noqa: BLE001 - convert TD runtime failures to a wire error
            self._record_event("command.failed", request_id, "internal_error")
            return "request_rejected", {"request_id": request_id, "code": "internal_error"}
        result = {
            "request_id": request_id,
            "instance_id": self.instance_id,
            "connection_id": self.connection_id,
            "result": self._wire_value(command_result),
        }
        if len(json.dumps(result, separators=(",", ":")).encode("utf-8")) > self.MAX_RESULT_BYTES:
            self._record_event("command.failed", request_id, "result_too_large")
            return "request_rejected", {"request_id": request_id, "code": "result_too_large"}
        self.pending_results[request_id] = result
        self._record_event("command.succeeded", request_id)
        return "request_result", result

    @classmethod
    def _wire_value(cls, value):
        """Remove optional nulls that locked SocketIO DAT cannot serialize."""
        if isinstance(value, dict):
            return {key: cls._wire_value(item) for key, item in value.items() if item is not None}
        if isinstance(value, list):
            return [
                {"__td_cli_null__": True} if item is None else cls._wire_value(item)
                for item in value
            ]
        return value

    def execute_command(self, command):
        name = command["name"]
        payload = command["input"]
        if name == "project.metadata":
            return self._project_metadata()
        if name == "events.read":
            return self._events(payload)
        if name == "batch.execute":
            commands = payload["commands"]
            for item in commands:
                self.operator_control.preflight(item)
            return {"results": [self.execute_command(item) for item in commands]}
        if name in OperatorControl.HANDLERS:
            return self.operator_control.execute(command)
        operator = self.operator_lookup(payload["operator_path"])
        if operator is None:
            raise AgentCommandError("operator_not_found")
        if name == "project.snapshot":
            return self._snapshot(operator, payload)
        if name == "binary.export":
            return self._binary_export(operator, payload)
        raise AgentCommandError("command_unsupported")

    def _snapshot(self, root, payload):
        maximum = payload["max_operators"]
        rows = []
        queue = [(root, 0)]
        while queue:
            operator, depth = queue.pop(0)
            if len(rows) >= maximum:
                raise AgentCommandError("result_too_large")
            rows.append({**self._operator_result(operator), "depth": depth})
            if depth < payload["max_depth"]:
                queue.extend(
                    (child, depth + 1)
                    for child in sorted(operator.children, key=lambda child: str(child.path))
                )
        return {"root_path": str(root.path), "operators": rows}

    @staticmethod
    def _binary_export(operator, payload):
        format_name = payload["format"]
        family = str(operator.family)
        if format_name == "tox" and family != "COMP":
            raise AgentCommandError("command_unsupported")
        if format_name == "png" and family != "TOP":
            raise AgentCommandError("command_unsupported")
        try:
            raw = bytes(
                operator.saveByteArray() if format_name == "tox" else operator.saveByteArray(".png")
            )
        except Exception:  # noqa: BLE001 - TouchDesigner raises tdError subclasses
            raise AgentCommandError("internal_error")
        if len(raw) > payload["max_bytes"]:
            raise AgentCommandError("result_too_large")
        return {
            "operator_path": str(operator.path),
            "format": format_name,
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "data_base64": base64.b64encode(raw).decode("ascii"),
        }

    def _project_metadata(self):
        if self.project_info is None:
            raise AgentCommandError("internal_error")
        return {
            "name": str(self.project_info.name),
            "folder": str(self.project_info.folder),
            "saved_with": {
                "version": str(self.project_info.saveVersion),
                "build": str(self.project_info.saveBuild),
                "time": str(self.project_info.saveTime),
                "os_name": str(self.project_info.saveOSName),
                "os_version": str(self.project_info.saveOSVersion),
            },
        }

    def _events(self, payload):
        after = payload["after"]
        events = [item for item in self.events if item["id"] > after][: payload["limit"]]
        if payload["include_errors"]:
            root = self.operator_lookup("/")
            errors = [str(value) for value in (root.errors(recurse=True) if root else [])]
        else:
            errors = []
        return {
            "events": events,
            "errors": errors[:100],
            "next_after": events[-1]["id"] if events else after,
        }

    def _record_event(self, kind, request_id, code=None):
        event = {"id": self._state["next_event_id"], "kind": kind, "request_id": request_id}
        if code is not None:
            event["code"] = code
        self._state["next_event_id"] += 1
        self.events.append(event)
        del self.events[:-1000]

    @staticmethod
    def _operator_result(operator):
        return {
            "path": str(operator.path),
            "name": str(operator.name),
            "op_type": str(operator.OPType),
            "family": str(operator.family),
        }

    def acknowledge_result(self, request_id):
        self.pending_results.pop(request_id, None)

    def begin_draining(self):
        self.draining = True

    def end_draining(self):
        self.draining = False

    def refresh_auth(self, table):
        token_path = Path(os.environ["LOCALAPPDATA"]) / "touchdesigner-cli" / "state" / "auth.token"
        token = token_path.read_text(encoding="ascii").strip()
        if len(token) != 64:
            raise RuntimeError("auth.token is malformed")
        table.clear()
        table.appendRow(["token", token])
