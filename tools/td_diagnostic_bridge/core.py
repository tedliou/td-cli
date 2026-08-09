"""Pure protocol core for the session-only TouchDesigner diagnostic bridge."""

import hmac
import json
import secrets
import time
import traceback as traceback_module
from collections import deque
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO


@dataclass(frozen=True)
class BridgeResponse:
    status_code: int
    payload: dict[str, object]


class BridgeApplication:
    MAX_BODY_BYTES = 65_536
    MAX_QUEUE_DEPTH = 16

    def __init__(self, token: str, touchdesigner_build: str) -> None:
        self._token = token
        self._touchdesigner_build = touchdesigner_build
        self._queue: deque[str] = deque()
        self._jobs: dict[str, dict[str, object]] = {}
        self._busy = False
        self._shutdown_requested = False

    def handle_request(
        self,
        method: str,
        uri: str,
        headers: dict[str, str],
        data: bytes,
    ) -> BridgeResponse:
        authorization = next(
            (value for key, value in headers.items() if key.lower() == "authorization"),
            "",
        )
        if not hmac.compare_digest(authorization, f"Bearer {self._token}"):
            return BridgeResponse(401, {"error": "unauthorized"})
        if method == "GET" and uri == "/health":
            return BridgeResponse(
                200,
                {
                    "bridge_version": 1,
                    "touchdesigner_build": self._touchdesigner_build,
                    "queue_depth": len(self._queue),
                    "busy": self._busy,
                },
            )
        if method == "POST" and uri == "/jobs":
            return self._submit(data)
        if method == "POST" and uri == "/shutdown":
            self._shutdown_requested = True
            return BridgeResponse(202, {"status": "stopping"})
        if method == "GET" and uri.startswith("/jobs/"):
            job = self._jobs.get(uri.removeprefix("/jobs/"))
            if job is None:
                return BridgeResponse(404, {"error": "unknown_job"})
            return BridgeResponse(200, dict(job))
        return BridgeResponse(404, {"error": "not_found"})

    def consume_shutdown_request(self) -> bool:
        requested = self._shutdown_requested
        self._shutdown_requested = False
        return requested

    def _submit(self, data: bytes) -> BridgeResponse:
        if len(data) > self.MAX_BODY_BYTES:
            return BridgeResponse(413, {"error": "body_too_large"})
        if len(self._queue) >= self.MAX_QUEUE_DEPTH:
            return BridgeResponse(429, {"error": "queue_full"})
        try:
            request = json.loads(data)
            code = request["code"]
            mode = request.get("mode", "exec")
            if not isinstance(code, str) or mode not in {"exec", "eval"}:
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return BridgeResponse(400, {"error": "invalid_job"})
        job_id = secrets.token_urlsafe(18)
        self._jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "code": code,
            "mode": mode,
            "value": "",
            "stdout": "",
            "stderr": "",
            "traceback": "",
            "started_at": None,
            "finished_at": None,
        }
        self._queue.append(job_id)
        return BridgeResponse(202, {"job_id": job_id, "status": "queued"})

    def execute_next(self, base_globals: dict[str, object]) -> bool:
        if not self._queue or self._busy:
            return False
        job_id = self._queue.popleft()
        job = self._jobs[job_id]
        self._busy = True
        job["status"] = "running"
        job["started_at"] = time.time()
        stdout = StringIO()
        stderr = StringIO()
        namespace = dict(base_globals)
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                if job["mode"] == "eval":
                    value = eval(str(job["code"]), namespace)
                else:
                    exec(str(job["code"]), namespace)
                    value = namespace.get("result")
            job["value"] = repr(value)
            job["status"] = "succeeded"
        except BaseException:
            job["status"] = "failed"
            job["traceback"] = traceback_module.format_exc()
        finally:
            job["stdout"] = stdout.getvalue()
            job["stderr"] = stderr.getvalue()
            job["finished_at"] = time.time()
            self._busy = False
        return True
