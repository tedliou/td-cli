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
        try:
            response = httpx.request(
                method,
                f"{self.endpoint}{path}",
                headers=self._headers(),
                json=json,
                timeout=self.timeout,
            )
        except (OSError, RuntimeError, httpx.HTTPError) as error:
            raise ClientError("daemon_unavailable") from error
        if response.status_code >= 400:
            detail = response.json().get("detail", "transport_error")
            if isinstance(detail, list):
                detail = "invalid_arguments"
            raise ClientError(str(detail))
        return response.json()

    def instances(self) -> list[dict[str, Any]]:
        return self.request("GET", "/v1/instances")

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
        return self.request(
            "POST",
            "/v1/requests",
            json={"request_id": request_id, "instance_id": instance_id, "command": command},
        )

    def get_request(self, request_id: str) -> dict[str, Any]:
        return self.request("GET", f"/v1/requests/{request_id}")

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
