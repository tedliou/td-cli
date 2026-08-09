from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from td_cli.daemon.cli import ENDPOINT
from td_cli.daemon.runtime_files import data_root, load_token


class ClientError(Exception):
    def __init__(self, code: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.details = details or {}


class DaemonClient:
    def __init__(
        self, *, timeout: float, root: Path | None = None, endpoint: str = ENDPOINT
    ) -> None:
        self.timeout = timeout
        self.root = root or data_root()
        self.endpoint = endpoint

    def _headers(self) -> dict[str, str]:
        token = load_token(self.root)
        if token is None:
            raise ClientError("daemon_unavailable")
        return {"Authorization": f"Bearer {token}"}

    def request(self, method: str, path: str, *, json: object = None) -> Any:
        deadline = time.monotonic() + self.timeout
        backoffs = (0.0, 0.1, 0.3) if method == "GET" else (0.0,)
        response = None
        last_error: Exception | None = None
        for backoff in backoffs:
            if backoff:
                remaining = deadline - time.monotonic()
                if remaining <= backoff:
                    break
                time.sleep(backoff)
            try:
                response = httpx.request(
                    method,
                    f"{self.endpoint}{path}",
                    headers=self._headers(),
                    json=json,
                    timeout=max(0.001, deadline - time.monotonic()),
                )
                break
            except httpx.ConnectError as error:
                last_error = error
            except (OSError, RuntimeError, httpx.HTTPError) as error:
                raise ClientError("daemon_unavailable") from error
        if response is None:
            raise ClientError("daemon_unavailable") from last_error
        if response.status_code >= 400:
            detail = response.json().get("detail", "transport_error")
            if isinstance(detail, list):
                detail = "invalid_arguments"
            if detail == "Not Found":
                detail = "daemon_unavailable"
            raise ClientError(str(detail))
        return response.json()

    def health(self) -> dict[str, Any]:
        payload = self.request("GET", "/v1/health")
        if 1 not in payload.get("protocol_versions", []):
            raise ClientError("protocol_incompatible")
        return payload

    def instances(self) -> list[dict[str, Any]]:
        items = self.request("GET", "/v1/instances")
        if any(
            item.get("status") not in {"online", "offline", "draining"}
            or item.get("protocol_version") != 1
            for item in items
        ):
            raise ClientError("protocol_incompatible")
        return items

    def select_instance(self, selector: str | None, *, online_only: bool = True) -> dict[str, Any]:
        instances = self.instances()
        if selector is None:
            matches = [item for item in instances if item["status"] == "online"]
            if len(matches) != 1:
                raise ClientError(
                    "instance_not_found" if not matches else "instance_selector_ambiguous"
                )
            return matches[0]
        matches = [
            item
            for item in instances
            if item["selector"].startswith(selector) or item["instance_id"].startswith(selector)
        ]
        if not matches:
            raise ClientError("instance_not_found")
        if len(matches) != 1:
            raise ClientError("instance_selector_ambiguous")
        instance = matches[0]
        if online_only and instance["status"] != "online":
            raise ClientError(f"instance_{instance['status']}")
        return instance

    def submit(self, request_id: str, instance_id: str, command: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.request(
                "POST",
                "/v1/requests",
                json={"request_id": request_id, "instance_id": instance_id, "command": command},
            )
        except ClientError as error:
            error.details.setdefault("request_id", request_id)
            raise

    def get_request(self, request_id: str) -> dict[str, Any]:
        snapshot = self.request("GET", f"/v1/requests/{request_id}")
        if snapshot.get("status") not in {
            "queued",
            "dispatched",
            "running",
            "succeeded",
            "failed",
            "unknown",
            "instance_offline",
            "daemon_shutdown",
        }:
            raise ClientError("protocol_incompatible")
        error = snapshot.get("error")
        if isinstance(error, dict) and error.get("code") not in {
            "invalid_arguments",
            "daemon_unavailable",
            "transport_error",
            "protocol_incompatible",
            "instance_not_found",
            "instance_selector_ambiguous",
            "instance_offline",
            "instance_draining",
            "instance_busy",
            "command_unsupported",
            "request_not_found",
            "request_id_conflict",
            "request_rejected",
            "request_outcome_unknown",
            "result_buffer_full",
            "operator_not_found",
            "result_too_large",
            "parameter_not_found",
            "parameter_read_only",
            "parameter_not_pulseable",
            "parameter_type_unsupported",
            "parameter_write_rejected",
            "expression_invalid",
            "wait_timeout",
            "daemon_shutdown",
            "internal_error",
        }:
            raise ClientError("protocol_incompatible")
        command = snapshot.get("command")
        result = snapshot.get("result")
        if (
            isinstance(command, dict)
            and command.get("name") in {"parameters.get", "parameters.set"}
            and isinstance(result, dict)
            and (
                result.get("mode") not in {"constant", "expression"}
                or result.get("value_type") not in {"boolean", "integer", "number", "string"}
            )
        ):
            raise ClientError("protocol_incompatible")
        return snapshot

    def wait(self, request_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        while True:
            snapshot = self.get_request(request_id)
            if snapshot["status"] in {
                "succeeded",
                "failed",
                "unknown",
                "instance_offline",
                "daemon_shutdown",
            }:
                return snapshot
            if time.monotonic() >= deadline:
                raise ClientError("wait_timeout", details={"request": snapshot})
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
