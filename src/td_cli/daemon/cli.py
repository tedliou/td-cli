from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from td_cli.daemon.transport import create_transport_app

app = typer.Typer(no_args_is_help=True)


def data_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("LOCALAPPDATA is unavailable")
    return Path(local) / "touchdesigner-cli"


def load_or_create_token(root: Path) -> str:
    token_path = root / "state" / "auth.token"
    if token_path.exists():
        token = token_path.read_text(encoding="ascii").strip()
        if len(token) != 64:
            raise RuntimeError("auth.token is malformed")
        return token
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    temporary = token_path.with_suffix(".tmp")
    temporary.write_text(token, encoding="ascii")
    temporary.replace(token_path)
    return token


@app.command()
def serve(
    host: Annotated[str, typer.Option(hidden=True)] = "127.0.0.1",
    port: Annotated[int, typer.Option(hidden=True)] = 9982,
) -> None:
    """Run the per-user Daemon in the foreground."""
    root = data_root()
    token = load_or_create_token(root)
    uvicorn.run(create_transport_app(root, token=token), host=host, port=port)


@app.command()
def status(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Observe Daemon state without starting it."""
    payload = {
        "status": "stopped",
        "pid": None,
        "endpoint": None,
        "release_version": None,
        "protocol_versions": [],
        "started_at": None,
    }
    if as_json:
        typer.echo(json.dumps(payload, separators=(",", ":")))
    else:
        typer.echo("Daemon is stopped")
    raise typer.Exit(3)


if __name__ == "__main__":
    app()
