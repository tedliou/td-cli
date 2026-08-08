from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Self

import httpx
import typer
import uvicorn

from td_cli import __version__
from td_cli.daemon.runtime_files import (
    configure_logging,
    data_root,
    load_or_create_token,
    secure_layout,
)
from td_cli.daemon.transport import create_transport_app

app = typer.Typer(no_args_is_help=True)
ENDPOINT = "http://127.0.0.1:9982"


class DaemonMutex:
    def __init__(self) -> None:
        self.handle: int | None = None

    def __enter__(self) -> Self:
        if os.name != "nt":
            return self
        kernel32 = ctypes.windll.kernel32
        username = os.environ.get("USERNAME", "unknown").replace("\\", "-")
        self.handle = kernel32.CreateMutexW(
            None, False, f"Local\\touchdesigner-cli-daemon-{username}"
        )
        if not self.handle or kernel32.GetLastError() == 183:
            raise RuntimeError("another Daemon owns the per-user mutex")
        return self

    def __exit__(self, *_: object) -> None:
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(self.handle)


def _probe(root: Path) -> dict[str, object] | None:
    try:
        token = load_or_create_token(root)
        response = httpx.get(
            f"{ENDPOINT}/v1/health",
            headers={"Authorization": f"Bearer {token}"},
            timeout=0.5,
        )
        payload = response.json() if response.status_code == 200 else None
        return payload if payload and 1 in payload.get("protocol_versions", []) else None
    except (OSError, RuntimeError, httpx.HTTPError):
        return None


def _status_payload(root: Path) -> dict[str, object]:
    health = _probe(root)
    run_path = root / "run" / "daemon.json"
    run = json.loads(run_path.read_text(encoding="utf-8")) if run_path.exists() else {}
    return {
        "status": "running" if health else ("starting/unhealthy" if run else "stopped"),
        "pid": run.get("pid"),
        "endpoint": run.get("endpoint"),
        "release_version": health.get("release_version") if health else run.get("release_version"),
        "protocol_versions": health.get("protocol_versions", [])
        if health
        else run.get("protocol_versions", []),
        "started_at": run.get("started_at"),
    }


@app.command()
def serve() -> None:
    """Run the per-user Daemon in the foreground."""
    root = data_root()
    secure_layout(root)
    with DaemonMutex():
        token = load_or_create_token(root)
        logger = configure_logging(root)
        server: uvicorn.Server

        def begin_shutdown() -> None:
            server.should_exit = True

        server = uvicorn.Server(
            uvicorn.Config(
                create_transport_app(root, token=token, shutdown=begin_shutdown),
                host="127.0.0.1",
                port=9982,
                log_config=None,
            )
        )
        snapshot = {
            "pid": os.getpid(),
            "started_at": datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "endpoint": "127.0.0.1:9982",
            "release_version": __version__,
            "protocol_versions": [1],
        }
        run_path = root / "run" / "daemon.json"
        temporary = run_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(snapshot, separators=(",", ":")), encoding="utf-8")
        temporary.replace(run_path)
        logger.info("daemon_started")
        try:
            server.run()
        finally:
            logger.info("daemon_stopped")
            run_path.unlink(missing_ok=True)


@app.command()
def start() -> None:
    root = data_root()
    secure_layout(root)
    if _probe(root):
        typer.echo("Daemon is running")
        return
    flags = 0
    startupinfo = None
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    subprocess.Popen(
        [sys.executable, "-m", "td_cli.daemon.cli", "serve"],
        creationflags=flags,
        startupinfo=startupinfo,
        close_fds=True,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if _probe(root):
            typer.echo("Daemon is running")
            return
        time.sleep(0.05)
    raise typer.Exit(3)


@app.command()
def stop() -> None:
    root = data_root()
    health = _probe(root)
    if health is None:
        typer.echo("Daemon is stopped")
        return
    token = load_or_create_token(root)
    try:
        httpx.post(
            f"{ENDPOINT}/v1/shutdown", headers={"Authorization": f"Bearer {token}"}, timeout=1
        )
    except httpx.HTTPError:
        raise typer.Exit(3) from None
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if _probe(root) is None:
            typer.echo("Daemon is stopped")
            return
        time.sleep(0.05)
    payload = _status_payload(root)
    typer.echo(
        f"Stop timed out; PID {payload['pid']}; logs: {root / 'logs' / 'daemon.log'}; "
        f"manual recovery: Stop-Process -Id {payload['pid']}",
        err=True,
    )
    raise typer.Exit(3)


@app.command()
def status(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    payload = _status_payload(data_root())
    typer.echo(
        json.dumps(payload, separators=(",", ":")) if as_json else f"Daemon is {payload['status']}"
    )
    if payload["status"] != "running":
        raise typer.Exit(3)


if __name__ == "__main__":
    app()
