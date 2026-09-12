from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path
from typing import Annotated, Self

import httpx
import typer
import uvicorn

from td_cli import __version__
from td_cli.cli_support import print_version
from td_cli.daemon.runtime_files import (
    configure_logging,
    data_root,
    load_or_create_token,
    load_token,
    secure_layout,
)
from td_cli.daemon.transport import create_transport_app
from td_cli.processes import LaunchError, launch_detached
from td_cli.protocol import PROTOCOL_VERSION

app = typer.Typer(no_args_is_help=True)
ENDPOINT = "http://127.0.0.1:9982"


class WindowsControlEvent(IntEnum):
    CLOSE = 2
    LOGOFF = 5
    SHUTDOWN = 6


WINDOWS_SHUTDOWN_EVENTS = frozenset(event.value for event in WindowsControlEvent)


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=print_version, is_eager=True),
    ] = None,
) -> None:
    """Manage the per-user td-cli Daemon."""


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


def _probe(root: Path, *, timeout: float = 0.5) -> dict[str, object] | None:
    try:
        token = load_token(root)
        if token is None:
            return None
        response = httpx.get(
            f"{ENDPOINT}/v3/health",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        payload = response.json() if response.status_code == 200 else None
        return payload
    except (OSError, RuntimeError, httpx.HTTPError):
        return None


def _status_payload(root: Path) -> dict[str, object]:
    health = _probe(root)
    run_path = root / "run" / "daemon.json"
    run = json.loads(run_path.read_text(encoding="utf-8")) if run_path.exists() else {}
    return {
        "status": (
            "running"
            if health and health.get("ready") is True
            else ("starting/unhealthy" if health or _pid_alive(run.get("pid")) else "stopped")
        ),
        "pid": run.get("pid"),
        "endpoint": run.get("endpoint"),
        "release_version": health.get("release_version") if health else run.get("release_version"),
        "protocol_versions": health.get("protocol_versions", [])
        if health
        else run.get("protocol_versions", []),
        "started_at": run.get("started_at"),
    }


def _pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0 or os.name != "nt":
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_uint]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if handle:
        kernel.CloseHandle(handle)
        return True
    return ctypes.get_last_error() == 5  # Access denied is not proof of process death.


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
                create_transport_app(
                    root,
                    token=token,
                    shutdown=begin_shutdown,
                    runtime_health=lambda: all(
                        getattr(handler, "healthy", True) for handler in logger.handlers
                    ),
                ),
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
            "protocol_versions": [PROTOCOL_VERSION],
        }
        run_path = root / "run" / "daemon.json"
        temporary = run_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(snapshot, separators=(",", ":")), encoding="utf-8")
        temporary.replace(run_path)
        logger.info("daemon_started")
        control_handler = _install_windows_shutdown_handler(server, token)
        try:
            server.run()
        finally:
            if control_handler is not None:
                ctypes.windll.kernel32.SetConsoleCtrlHandler(control_handler, False)
            logger.info("daemon_stopped")
            run_path.unlink(missing_ok=True)


def ensure_running(*, timeout: float) -> None:
    """Start once, wait boundedly, and never replace an authenticated unhealthy runtime."""
    deadline = time.monotonic() + timeout
    root = data_root()

    def probe_within_deadline() -> dict[str, object] | None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LaunchError("Daemon startup deadline expired")
        health = _probe(root, timeout=min(0.5, remaining))
        if time.monotonic() >= deadline:
            raise LaunchError("Daemon startup deadline expired")
        return health

    health = probe_within_deadline()
    versions = health.get("protocol_versions", []) if health else []
    if health and (not isinstance(versions, list) or PROTOCOL_VERSION not in versions):
        raise LaunchError("Daemon protocol is incompatible; update explicitly")
    if health and health.get("ready") is True:
        return
    if health is not None:
        raise LaunchError("Daemon is starting/unhealthy")
    frozen = bool(getattr(sys, "frozen", False))
    command = (
        [str(Path(sys.executable).with_name("td-daemon.exe")), "serve"]
        if frozen
        else [sys.executable, "-m", "td_cli.daemon.cli", "serve"]
    )
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LaunchError("Daemon startup deadline expired")
    launch_detached(command, cwd=Path.cwd(), hidden=True, timeout=remaining)
    while time.monotonic() < deadline:
        health = probe_within_deadline()
        if health and health.get("ready") is True:
            return
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))
    raise LaunchError(f"Daemon startup timed out; inspect {root / 'logs' / 'daemon.log'}")


@app.command()
def start(
    timeout: Annotated[float, typer.Option("--timeout", min=0.1, max=3600)] = 30.0,
) -> None:
    """Start the per-user background Daemon without a console window."""
    try:
        ensure_running(timeout=timeout)
    except (LaunchError, OSError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(3) from error


@app.command()
def stop() -> None:
    root = data_root()
    health = _probe(root)
    if health is None:
        typer.echo("Daemon is stopped")
        return
    token = load_token(root)
    if token is None:
        typer.echo("Daemon is stopped")
        return
    try:
        httpx.post(
            f"{ENDPOINT}/v3/shutdown", headers={"Authorization": f"Bearer {token}"}, timeout=6
        )
    except httpx.HTTPError:
        raise typer.Exit(3) from None
    deadline = time.monotonic() + 1
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


def _install_windows_shutdown_handler(server: uvicorn.Server, token: str) -> object | None:
    if os.name != "nt":
        return None
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

    @callback_type
    def handler(control_type: int) -> bool:
        if control_type not in WINDOWS_SHUTDOWN_EVENTS:
            return False
        threading.Thread(
            target=_request_orderly_shutdown,
            args=(server, token),
            daemon=True,
            name="td-cli-windows-shutdown",
        ).start()
        return True

    if not ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True):
        raise RuntimeError("cannot install Windows shutdown handler")
    return handler


def _request_orderly_shutdown(server: uvicorn.Server, token: str) -> None:
    try:
        httpx.post(
            f"{ENDPOINT}/v3/shutdown",
            headers={"Authorization": f"Bearer {token}"},
            timeout=6,
        )
    except httpx.HTTPError:
        server.should_exit = True


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
