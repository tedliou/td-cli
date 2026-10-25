from __future__ import annotations

import http.client
import json as jsonlib
import secrets
import select
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from td_cli.daemon.runtime_files import ENDPOINT, data_root, load_token
from td_cli.error_catalog import ERROR_CATALOG
from td_cli.processes import LaunchError
from td_cli.protocol import (
    MAX_REQUEST_WAIT_MS,
    PROTOCOL_VERSION,
    TERMINAL_REQUEST_STATUSES,
    RequestStatus,
)

_REQUEST_STATUSES = frozenset(RequestStatus)
# The Daemon caps one long poll at 25 s; leave room for its answer within the HTTP budget.
WAIT_RESPONSE_MARGIN_SECONDS = 0.1


HEALTH_PROBE_TIMEOUT = 0.5


def new_request_id() -> str:
    """A UUIDv7 Request ID, like the ones the native td generates."""
    milliseconds = int(time.time() * 1000)
    value = (milliseconds << 80) | (0x7 << 76) | (secrets.randbits(12) << 64)
    value |= (0b10 << 62) | secrets.randbits(62)
    return str(uuid.UUID(int=value))


def ensure_running(*, timeout: float) -> None:
    """Start the Daemon; its server stack is imported only when a start is needed."""
    from td_cli.daemon.cli import ensure_running as start

    start(timeout=timeout)


class ClientError(Exception):
    def __init__(self, code: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.details = details or {}


class DaemonConnectError(OSError):
    """The connection to the Daemon could not be opened, so nothing was sent."""


class DaemonConnection:
    """One keep-alive HTTP/1.1 connection to the loopback Daemon.

    It uses the standard library because importing httpx costs every td start ~130 ms.
    An idle connection the Daemon already closed is replaced before sending, so only a
    failure to open a connection is reported as ``DaemonConnectError``.
    """

    def __init__(self, endpoint: str) -> None:
        parts = urlsplit(endpoint)
        self._host = parts.hostname or "127.0.0.1"
        self._port = parts.port or 80
        self._http: http.client.HTTPConnection | None = None

    def exchange(
        self, method: str, path: str, *, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> tuple[int, bytes]:
        connection = self._reusable()
        if connection is None:
            connection = http.client.HTTPConnection(self._host, self._port, timeout=timeout)
            try:
                connection.connect()
            except OSError as error:
                raise DaemonConnectError(str(error)) from error
        try:
            connection.sock.settimeout(timeout)
            if body is not None:
                headers = {**headers, "Content-Type": "application/json"}
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            content = response.read()
        except BaseException:
            connection.close()
            raise
        if response.will_close:
            connection.close()
        else:
            self._http = connection
        return response.status, content

    def _reusable(self) -> http.client.HTTPConnection | None:
        connection, self._http = self._http, None
        if connection is None:
            return None
        readable, _, _ = select.select([connection.sock], [], [], 0)
        if readable:  # an idle connection only becomes readable when the Daemon closed it
            connection.close()
            return None
        return connection

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None


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
        # One keep-alive connection serves every request of a Command.
        self._connection = DaemonConnection(endpoint)

    def close(self) -> None:
        self._connection.close()

    def _headers(self) -> dict[str, str]:
        token = load_token(self.root)
        if token is None:
            raise ClientError("daemon_unavailable")
        return {"Authorization": f"Bearer {token}"}

    def _daemon_is_healthy(self) -> bool:
        """Probe health on the shared connection; any doubt is left to the start routine."""
        try:
            status, content = self._connection.exchange(
                "GET",
                "/v3/health",
                headers=self._headers(),
                body=None,
                timeout=min(HEALTH_PROBE_TIMEOUT, self.timeout),
            )
            payload = jsonlib.loads(content) if status == 200 else None
        except (ClientError, OSError, ValueError, http.client.HTTPException):
            return False
        if not isinstance(payload, dict):
            return False
        versions = payload.get("protocol_versions")
        return (
            payload.get("ready") is True
            and isinstance(versions, list)
            and PROTOCOL_VERSION in versions
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        json: object = None,
        content: bytes | None = None,
        long_poll: bool = False,
    ) -> Any:
        """Send one call within the Command deadline.

        With ``long_poll`` each attempt asks the Daemon to wait for the rest of the deadline,
        less a margin for its answer, so a retried GET never outwaits its own timeout.
        """
        if self._autostart:
            self._autostart = False
            self._deadline = time.monotonic() + self.timeout
            if not self._daemon_is_healthy():
                remaining = self._deadline - time.monotonic()
                if remaining <= 0:
                    raise ClientError(
                        "daemon_unavailable", details={"reason": "command deadline expired"}
                    )
                try:
                    ensure_running(timeout=remaining)
                except (LaunchError, OSError) as error:
                    raise ClientError(
                        "daemon_unavailable", details={"reason": str(error)}
                    ) from error
        deadline = self._deadline or (time.monotonic() + self.timeout)
        if time.monotonic() >= deadline:
            raise ClientError("daemon_unavailable", details={"reason": "command deadline expired"})
        backoffs = (0.0, 0.1, 0.3) if method == "GET" else (0.0,)
        body = content
        if json is not None:
            body = jsonlib.dumps(json, separators=(",", ":")).encode()
        answer = None
        last_error: Exception | None = None
        for backoff in backoffs:
            if backoff:
                remaining = deadline - time.monotonic()
                if remaining <= backoff:
                    break
                time.sleep(backoff)
            target = path
            wait_ms = min(
                MAX_REQUEST_WAIT_MS,
                int((deadline - time.monotonic() - WAIT_RESPONSE_MARGIN_SECONDS) * 1000),
            )
            if long_poll and wait_ms > 0:
                target = f"{path}?wait_ms={wait_ms}"
            try:
                answer = self._connection.exchange(
                    method,
                    target,
                    headers=self._headers(),
                    body=body,
                    timeout=max(0.001, deadline - time.monotonic()),
                )
                break
            except DaemonConnectError as error:
                last_error = error
            except (OSError, http.client.HTTPException) as error:
                raise ClientError("daemon_unavailable") from error
        if answer is None:
            raise ClientError("daemon_unavailable") from last_error
        status, content = answer
        try:
            payload = jsonlib.loads(content)
        except ValueError as error:
            raise ClientError("daemon_unavailable") from error
        if status >= 400:
            detail = payload.get("detail", "transport_error")
            if isinstance(detail, list):
                detail = "invalid_arguments"
            if detail == "Not Found":
                detail = "daemon_unavailable"
            details = payload.get("details")
            raise ClientError(str(detail), details=details if isinstance(details, dict) else None)
        return payload

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
        """Let the Daemon apply the selector rule; without a selector, the only online one."""
        query = {"online_only": "true" if online_only else "false"}
        if selector is not None:
            query["selector"] = selector
        return self.request("GET", f"/v3/instances/resolve?{urlencode(query)}")

    def validate_plan(self, document: bytes) -> list[dict[str, Any]]:
        """Have the Daemon check a whole ``commands execute`` plan; return its Commands."""
        return self.request("POST", "/v3/command-plans/validate", content=document)["commands"]

    def submit(
        self,
        request_id: str,
        instance_id: str | None,
        command: dict[str, Any],
        *,
        instance_selector: str | None = None,
    ) -> dict[str, Any]:
        """Submit by Instance ID, or let the Daemon validate and resolve the selector first."""
        body: dict[str, Any] = {"request_id": request_id, "command": command}
        if instance_id is not None:
            body["instance_id"] = instance_id
        elif instance_selector is not None:
            body["instance_selector"] = instance_selector
        try:
            return self.request("POST", "/v3/requests", json=body)
        except ClientError as error:
            error.details.setdefault("request_id", request_id)
            raise

    def get_request(self, request_id: str, *, long_poll: bool = False) -> dict[str, Any]:
        """Read a Request; with ``long_poll`` the Daemon answers once it settles."""
        snapshot = self.request("GET", f"/v3/requests/{request_id}", long_poll=long_poll)
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
                snapshot = self.get_request(request_id, long_poll=True)
            except ClientError as error:
                error.details.setdefault("request_id", request_id)
                raise
            if snapshot["status"] in TERMINAL_REQUEST_STATUSES:
                return snapshot
            if time.monotonic() >= deadline:
                raise ClientError("wait_timeout", details={"request": snapshot})
            # A long poll ends unsettled only when its wait ran out or the Daemon is stopping;
            # space the next query so neither case becomes a tight loop.
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))


def _incompatible(snapshot: dict[str, Any]) -> ClientError:
    """Reject an uninterpretable Request without discarding its identity or status."""
    return ClientError("protocol_incompatible", details={"request": snapshot})
