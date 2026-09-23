from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from td_cli.daemon.cli import ENDPOINT, ensure_running
from td_cli.daemon.runtime_files import data_root, load_token
from td_cli.error_catalog import ERROR_CATALOG
from td_cli.processes import LaunchError
from td_cli.protocol import PROTOCOL_VERSION, RequestStatus

_REQUEST_STATUSES = frozenset(RequestStatus)


class ClientError(Exception):
    def __init__(self, code: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.details = details or {}


class DaemonClient:
    def __init__(
        self,
        *,
        timeout: float,
        root: Path | None = None,
        endpoint: str = ENDPOINT,
        autostart: bool = False,
    ) -> None:
        self.timeout = timeout
        self.root = root or data_root()
        self.endpoint = endpoint
        self._autostart = autostart
        self._deadline: float | None = None

    def _headers(self) -> dict[str, str]:
        token = load_token(self.root)
        if token is None:
            raise ClientError("daemon_unavailable")
        return {"Authorization": f"Bearer {token}"}

    def request(self, method: str, path: str, *, json: object = None) -> Any:
        if self._autostart:
            self._autostart = False
            self._deadline = time.monotonic() + self.timeout
            try:
                ensure_running(timeout=self.timeout)
            except (LaunchError, OSError) as error:
                raise ClientError("daemon_unavailable", details={"reason": str(error)}) from error
        deadline = self._deadline or (time.monotonic() + self.timeout)
        if time.monotonic() >= deadline:
            raise ClientError("daemon_unavailable", details={"reason": "command deadline expired"})
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
        payload = self.request("GET", "/v3/health")
        if PROTOCOL_VERSION not in payload.get("protocol_versions", []):
            raise ClientError("protocol_incompatible")
        return payload

    def instances(self) -> list[dict[str, Any]]:
        items = self.request("GET", "/v3/instances")
        if any(
            item.get("status") not in {"online", "offline", "draining", "synchronizing"}
            or item.get("protocol_version") != PROTOCOL_VERSION
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
            raise ClientError(
                {
                    "offline": "instance_offline",
                    "draining": "instance_draining",
                    "synchronizing": "instance_synchronizing",
                }[instance["status"]]
            )
        return instance

    def submit(self, request_id: str, instance_id: str, command: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.request(
                "POST",
                "/v3/requests",
                json={"request_id": request_id, "instance_id": instance_id, "command": command},
            )
        except ClientError as error:
            error.details.setdefault("request_id", request_id)
            raise

    def get_request(self, request_id: str) -> dict[str, Any]:
        snapshot = self.request("GET", f"/v3/requests/{request_id}")
        if snapshot.get("status") not in _REQUEST_STATUSES:
            raise _incompatible(snapshot)
        error = snapshot.get("error")
        if isinstance(error, dict) and error.get("code") not in ERROR_CATALOG:
            raise _incompatible(snapshot)
        command = snapshot.get("command")
        result = snapshot.get("result")
        if (
            isinstance(command, dict)
            and command.get("name") == "parameters.get"
            and isinstance(result, dict)
            and (
                result.get("mode") not in {"constant", "expression", "export", "bind"}
                or result.get("value_type")
                not in {
                    "boolean",
                    "integer",
                    "number",
                    "string",
                    "operator",
                    "multi_operator",
                    "python",
                    "sequence",
                    "unknown",
                }
            )
        ):
            raise _incompatible(snapshot)
        if (
            isinstance(command, dict)
            and command.get("name") == "parameters.set"
            and isinstance(result, dict)
            and (
                result.get("mode") not in {"constant", "expression", "export", "bind"}
                or result.get("value_type")
                not in {"boolean", "integer", "number", "string", "operator", "multi_operator"}
            )
        ):
            raise _incompatible(snapshot)
        if (
            isinstance(command, dict)
            and command.get("name") == "parameters.list"
            and isinstance(result, dict)
        ):
            parameters = result.get("parameters")
            if not isinstance(parameters, list) or any(
                not isinstance(parameter, dict)
                or parameter.get("mode") not in {"constant", "expression", "export", "bind"}
                or parameter.get("value_kind")
                not in {
                    "boolean",
                    "integer",
                    "number",
                    "string",
                    "menu",
                    "operator",
                    "multi_operator",
                    "pulse",
                    "python",
                    "sequence",
                    "unknown",
                }
                for parameter in parameters
            ):
                raise _incompatible(snapshot)
        if (
            isinstance(command, dict)
            and command.get("name") in {"parameters.sequence.get", "parameters.sequence.replace"}
            and isinstance(result, dict)
            and not isinstance(result.get("blocks"), list)
        ):
            raise _incompatible(snapshot)
        return snapshot

    def wait(self, request_id: str) -> dict[str, Any]:
        deadline = self._deadline or (time.monotonic() + self.timeout)
        snapshot = None
        while True:
            if time.monotonic() >= deadline:
                raise ClientError(
                    "wait_timeout", details={"request_id": request_id, "request": snapshot}
                )
            try:
                snapshot = self.get_request(request_id)
            except ClientError as error:
                error.details.setdefault("request_id", request_id)
                raise
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


def _incompatible(snapshot: dict[str, Any]) -> ClientError:
    """Reject an uninterpretable Request without discarding its identity or status."""
    return ClientError("protocol_incompatible", details={"request": snapshot})
