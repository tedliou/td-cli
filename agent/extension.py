"""Canonical Agent Component extension source loaded by TouchDesigner."""

import json
import os
import uuid
from pathlib import Path


class AgentExt:
    MAX_UNCONFIRMED_RESULTS = 256

    def __init__(self, owner_comp):
        self.owner_comp = owner_comp
        self.instance_id = str(uuid.uuid4())
        self.connection_id = None
        self.pending_results = {}
        self.seen_commands = {}
        self.draining = False
        self.last_heartbeat_at = 0.0

    def registration_payload(self):
        return {
            "instance_id": self.instance_id,
            "agent_version": "0.1.0.dev0",
            "protocol_versions": [1],
            "capabilities": ["diagnostic.ping"],
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
        result = {
            "request_id": request_id,
            "instance_id": self.instance_id,
            "connection_id": self.connection_id,
            "result": {"message": request["command"]["input"]["message"]},
        }
        self.pending_results[request_id] = result
        return "request_result", result

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
