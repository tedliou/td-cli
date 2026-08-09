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
            except (OSError, RuntimeError, httpx.HTTPError) as error:
                last_error = error
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
        if any(item.get("status") not in {"online", "offline", "draining"} for item in items):
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
