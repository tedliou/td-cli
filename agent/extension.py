"""Canonical Agent Component extension source loaded by TouchDesigner."""

import base64
import builtins
import hashlib
import json
import os
import uuid
from pathlib import Path

if not hasattr(builtins, "_td_cli_runtime_session_id"):
    builtins._td_cli_runtime_session_id = str(uuid.uuid4())


class AgentCommandError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class AgentExt:
    MAX_UNCONFIRMED_RESULTS = 256
    MAX_RESULT_BYTES = 256 * 1024

    CAPABILITIES = (
        "ops.children",
        "ops.connect",
        "ops.create",
        "ops.get",
        "parameters.get",
        "parameters.pulse",
        "parameters.set",
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
            "agent_version": "0.1.0",
            "td_build": str(getattr(self.app_info, "build", "2025.32050")),
            "protocol_versions": [1],
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
            "result": command_result,
        }
        if len(json.dumps(result, separators=(",", ":")).encode("utf-8")) > self.MAX_RESULT_BYTES:
            self._record_event("command.failed", request_id, "result_too_large")
            return "request_rejected", {"request_id": request_id, "code": "result_too_large"}
        self.pending_results[request_id] = result
        self._record_event("command.succeeded", request_id)
        return "request_result", result

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
                self._preflight(item)
            return {"results": [self.execute_command(item) for item in commands]}
        if name == "ops.create":
            return self._create_operator(payload)
        if name == "ops.connect":
            return self._connect_operators(payload)
        operator = self.operator_lookup(payload["operator_path"])
        if operator is None:
            raise AgentCommandError("operator_not_found")
        if name == "project.snapshot":
            return self._snapshot(operator, payload)
        if name == "binary.export":
            return self._binary_export(operator, payload)
        if name == "ops.get":
            return self._operator_result(operator)
        if name == "ops.children":
            children = [self._operator_result(child) for child in operator.children]
            op_type = payload.get("op_type")
            if op_type is not None:
                children = [child for child in children if child["op_type"] == op_type]
            return sorted(children, key=lambda child: child["path"])
        parameter = self._parameter(operator, payload["parameter"])
        self._preflight_parameter(name, operator, payload, parameter)
        if name == "parameters.get":
            return self._parameter_result(operator, payload["parameter"], parameter)
        if name == "parameters.set":
            try:
                if payload["mode"] == "expression":
                    parameter.expr = payload["value"]
                else:
                    parameter.val = payload["value"]
            except Exception:  # noqa: BLE001 - TouchDesigner raises tdError subclasses
                code = (
                    "expression_invalid"
                    if payload["mode"] == "expression"
                    else "parameter_write_rejected"
                )
                raise AgentCommandError(code)
            result = self._parameter_result(operator, payload["parameter"], parameter)
            if result["mode"] != payload["mode"] or result["value"] != payload["value"]:
                raise AgentCommandError("parameter_write_rejected")
            return result
        if name == "parameters.pulse":
            parameter.pulse()
            return {
                "operator_path": str(operator.path),
                "parameter": payload["parameter"],
                "pulsed": True,
            }
        raise AgentCommandError("command_unsupported")

    def _create_operator(self, payload):
        parent = self.operator_lookup(payload["parent_path"])
        if parent is None:
            raise AgentCommandError("operator_not_found")
        if str(parent.family) != "COMP":
            raise AgentCommandError("operator_parent_invalid")
        expected_path = str(parent.path).rstrip("/") + "/" + payload["name"]
        if self.operator_lookup(expected_path) is not None:
            raise AgentCommandError("operator_already_exists")
        try:
            created = parent.create(payload["op_type"], payload["name"])
            if str(created.path) != expected_path or str(created.name) != payload["name"]:
                created.destroy()
                raise AgentCommandError("operator_create_failed")
            created.nodeX = payload["node_x"]
            created.nodeY = payload["node_y"]
        except AgentCommandError:
            raise
        except Exception:  # noqa: BLE001 - TouchDesigner raises tdError subclasses
            raise AgentCommandError("operator_create_failed")
        return self._operator_result(created)

    def _connect_operators(self, payload):
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
        source_connector = source.outputConnectors[output_index]
        target_connector = target.inputConnectors[input_index]
        if target_connector.connections:
            raise AgentCommandError("connector_occupied")
        try:
            source_connector.connect(target_connector)
        except Exception:  # noqa: BLE001 - TouchDesigner raises tdError subclasses
            raise AgentCommandError("connector_connect_failed")
        if not any(
            str(connection.owner.path) == str(source.path)
            and int(connection.index) == output_index
            and bool(connection.isOutput)
            for connection in target_connector.connections
        ):
            raise AgentCommandError("connector_connect_failed")
        return {
            "source_path": str(source.path),
            "target_path": str(target.path),
            "output_index": output_index,
            "input_index": input_index,
            "connected": True,
        }

    def _preflight(self, command):
        name = command["name"]
        payload = command["input"]
        operator = self.operator_lookup(payload["operator_path"])
        if operator is None:
            raise AgentCommandError("operator_not_found")
        if name.startswith("parameters."):
            parameter = self._parameter(operator, payload["parameter"])
            self._preflight_parameter(name, operator, payload, parameter)

    def _preflight_parameter(self, name, operator, payload, parameter):
        if name in {"parameters.get", "parameters.set"}:
            self._parameter_result(operator, payload["parameter"], parameter)
        if name == "parameters.set":
            if getattr(parameter, "readOnly", False):
                raise AgentCommandError("parameter_read_only")
            if payload["mode"] == "expression":
                try:
                    compile(payload["value"], "<parameter-expression>", "eval")
                except SyntaxError:
                    raise AgentCommandError("expression_invalid")
        if name == "parameters.pulse" and not getattr(parameter, "isPulse", False):
            raise AgentCommandError("parameter_not_pulseable")

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

    @staticmethod
    def _parameter(operator, name):
        try:
            parameter = getattr(operator.par, name)
        except Exception:  # noqa: BLE001 - missing TD parameters raise tdAttributeError
            parameter = None
        if parameter is None:
            raise AgentCommandError("parameter_not_found")
        return parameter

    @staticmethod
    def _parameter_result(operator, name, parameter):
        mode_text = str(parameter.mode).lower()
        mode = "expression" if "expression" in mode_text else "constant"
        value = parameter.expr if mode == "expression" else parameter.eval()
        if type(value) is bool:
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
