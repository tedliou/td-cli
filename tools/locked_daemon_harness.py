"""Shared runner for locked acceptance through this checkout's real Daemon transport.

Runs ``create_transport_app`` on an isolated port with a scratch data root and launches a
disposable TouchDesigner project whose Execute DAT is ``locked_menu_limit_probe.py`` (it loads
the Agent artifact named by ``TDCLI_MENU_LIMIT_ARTIFACT`` and redirects it to this Daemon). Every
Command goes through the public DaemonClient. The user's Daemon, data root, and projects are never
touched; the TouchDesigner process started here is the only one stopped.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import uvicorn

from td_cli.client import ClientError, DaemonClient, new_request_id
from td_cli.daemon.runtime_files import load_or_create_token, secure_layout
from td_cli.daemon.transport import create_transport_app
from td_cli.protocol import Command

PORT = 19982
TOUCHDESIGNER = Path(r"C:\Program Files\Derivative\TouchDesigner\bin\TouchDesigner.exe")


def wait_for(predicate, seconds: float, what: str) -> Any:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.5)
    raise TimeoutError(f"timed out waiting for {what}")


class Harness:
    def __init__(self, client: DaemonClient, instance_id: str) -> None:
        self.client = client
        self.instance_id = instance_id
        self.requests: list[dict[str, Any]] = []

    def submit(self, label: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        command = Command.model_validate({"name": name, "input": payload}).model_dump(mode="json")
        request_id = new_request_id()
        started = time.monotonic()
        try:
            self.client.submit(request_id, self.instance_id, command)
        except ClientError as error:
            row = {
                "step": label,
                "command": name,
                "request_id": request_id,
                "admission_error": error.code,
            }
            self.requests.append(row)
            return row
        snapshot = self.client.wait(request_id)
        self.requests.append(
            {
                "step": label,
                "command": name,
                "request_id": request_id,
                "status": snapshot["status"],
                "error": snapshot.get("error"),
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
                "result_bytes": len(json.dumps(snapshot.get("result"), separators=(",", ":"))),
            }
        )
        return snapshot

    def run(self, label: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.submit(label, name, payload)
        if snapshot.get("status") != "succeeded":
            raise RuntimeError(f"{label}: {snapshot}")
        return snapshot["result"]

    def error(self, label: str, name: str, payload: dict[str, Any]) -> str | None:
        """Submit a Command expected to fail and return its error code."""
        snapshot = self.submit(label, name, payload)
        error = snapshot.get("error")
        return snapshot.get("admission_error") or (error or {}).get("code")


def run_locked(
    *,
    work: Path,
    project: Path,
    artifact: Path,
    evidence_path: Path,
    steps: Callable[[Harness, Path], dict[str, Any]],
) -> None:
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    for name in ("ready.json", "done", "td-final.json", "td-error.json"):
        (work / name).unlink(missing_ok=True)
    localappdata = work / "localappdata"
    root = localappdata / "touchdesigner-cli"
    if root.exists():
        shutil.rmtree(root)  # scratch data root created by a previous run of this harness
    localappdata.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {
        "artifact": str(artifact),
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "project": str(project),
        "port": PORT,
    }
    env = {
        **os.environ,
        "LOCALAPPDATA": str(localappdata),
        "TDCLI_MENU_LIMIT_WORK": str(work),
        "TDCLI_MENU_LIMIT_ARTIFACT": str(artifact),
        "TDCLI_MENU_LIMIT_URL": f"http://127.0.0.1:{PORT}",
    }
    process = subprocess.Popen([str(TOUCHDESIGNER), str(project)], env=env)
    evidence["touchdesigner_pid"] = process.pid
    server: uvicorn.Server | None = None
    thread: threading.Thread | None = None
    harness: Harness | None = None
    try:
        wait_for(
            lambda: (
                (work / "ready.json").exists()
                or (work / "td-error.json").exists()
                or process.poll() is not None
            ),
            120,
            "TouchDesigner readiness",
        )
        if not (work / "ready.json").exists():
            raise RuntimeError("TouchDesigner probe did not become ready")
        evidence["touchdesigner_ready"] = json.loads((work / "ready.json").read_text("utf-8"))
        secure_layout(root)
        token = load_or_create_token(root)
        server = uvicorn.Server(
            uvicorn.Config(
                create_transport_app(root, token=token, runtime_health=lambda: True),
                host="127.0.0.1",
                port=PORT,
                log_config=None,
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        client = DaemonClient(timeout=60, root=root, endpoint=f"http://127.0.0.1:{PORT}")
        wait_for(lambda: server is not None and server.started, 30, "acceptance Daemon")
        instance = wait_for(
            lambda: next((i for i in client.instances() if i["status"] == "online"), None),
            60,
            "online Instance",
        )
        evidence["instance"] = instance
        harness = Harness(client, instance["instance_id"])
        evidence.update(steps(harness, work))
    except Exception as error:
        evidence["harness_error"] = repr(error)
        raise
    finally:
        if harness is not None:
            evidence["requests"] = harness.requests
        (work / "done").write_text("done", encoding="utf-8")
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()  # the disposable TouchDesigner process started above
            evidence["touchdesigner_killed"] = True
        evidence["touchdesigner_exit_code"] = process.returncode
        for name in ("td-final.json", "td-error.json"):
            if (work / name).exists():
                evidence[name.removesuffix(".json")] = json.loads((work / name).read_text("utf-8"))
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=15)
            evidence["daemon_thread_stopped"] = not thread.is_alive()
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
