"""Canonical Agent Component extension source loaded by TouchDesigner."""

import builtins
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
        "ops.get",
        "parameters.get",
        "parameters.pulse",
        "parameters.set",
    )

    def __init__(self, owner_comp, operator_lookup=None):
        self.owner_comp = owner_comp
        self.operator_lookup = operator_lookup or (lambda path: op(path))
        runtime_session_id = builtins._td_cli_runtime_session_id
        state = getattr(builtins, "_td_cli_agent_state", None)
        if state is None or state["runtime_session_id"] != runtime_session_id:
            state = {
                "runtime_session_id": runtime_session_id,
                "instance_id": str(uuid.uuid4()),
                "pending_results": {},
                "seen_commands": {},
            }
            builtins._td_cli_agent_state = state
        self.instance_id = state["instance_id"]
        self.pending_results = state["pending_results"]
        self.seen_commands = state["seen_commands"]
        self.connection_id = None
        self.draining = False
        self.last_heartbeat_at = 0.0

    def registration_payload(self):
        return {
            "instance_id": self.instance_id,
            "agent_version": "0.1.0.dev0",
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
            if (
                len(json.dumps(command_result, separators=(",", ":")).encode("utf-8"))
                > self.MAX_RESULT_BYTES
            ):
                raise AgentCommandError("result_too_large")
        except AgentCommandError as error:
            return "request_rejected", {"request_id": request_id, "code": error.code}
        except Exception:  # noqa: BLE001 - convert TD runtime failures to a wire error
            return "request_rejected", {"request_id": request_id, "code": "internal_error"}
        result = {
            "request_id": request_id,
            "instance_id": self.instance_id,
            "connection_id": self.connection_id,
            "result": command_result,
        }
        self.pending_results[request_id] = result
        return "request_result", result

    def execute_command(self, command):
        name = command["name"]
        payload = command["input"]
        operator = self.operator_lookup(payload["operator_path"])
        if operator is None:
            raise AgentCommandError("operator_not_found")
        if name == "ops.get":
            return self._operator_result(operator)
        if name == "ops.children":
            children = [self._operator_result(child) for child in operator.children]
            op_type = payload.get("op_type")
            if op_type is not None:
                children = [child for child in children if child["op_type"] == op_type]
            return sorted(children, key=lambda child: child["path"])
        parameter = self._parameter(operator, payload["parameter"])
        if name == "parameters.get":
            return self._parameter_result(operator, payload["parameter"], parameter)
        if name == "parameters.set":
            if getattr(parameter, "readOnly", False):
                raise AgentCommandError("parameter_read_only")
            try:
                if payload["mode"] == "expression":
                    compile(payload["value"], "<parameter-expression>", "eval")
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
            if not getattr(parameter, "isPulse", False):
                raise AgentCommandError("parameter_not_pulseable")
            parameter.pulse()
            return {
                "operator_path": str(operator.path),
                "parameter": payload["parameter"],
                "pulsed": True,
            }
        raise AgentCommandError("command_unsupported")

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

    def refresh_auth(self, table):
        token_path = Path(os.environ["LOCALAPPDATA"]) / "touchdesigner-cli" / "state" / "auth.token"
        token = token_path.read_text(encoding="ascii").strip()
        if len(token) != 64:
            raise RuntimeError("auth.token is malformed")
        table.clear()
        table.appendRow(["token", token])
