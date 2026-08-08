"""Canonical Agent Component extension source loaded by TouchDesigner."""

import json
import uuid


class AgentExt:
    def __init__(self, owner_comp):
        self.owner_comp = owner_comp
        self.instance_id = str(uuid.uuid4())
        self.connection_id = None
        self.pending_results = {}

    def registration_payload(self):
        return {
            "instance_id": self.instance_id,
            "agent_version": "0.1.0.dev0",
            "protocol_versions": [1],
            "capabilities": ["diagnostic.ping"],
        }

    def diagnostic_result(self, request):
        result = {"message": request["command"]["input"]["message"]}
        self.pending_results[request["request_id"]] = result
        return json.dumps(result, separators=(",", ":"))
