from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(no_args_is_help=True)


@app.command("inspect-source")
def inspect_source(source: Annotated[Path, typer.Argument(exists=True, file_okay=False)]) -> None:
    manifest_path = source / "manifest.json"
    if not manifest_path.is_file():
        typer.echo("manifest.json is missing", err=True)
        raise typer.Exit(1)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    missing = [name for name in manifest["required_files"] if not (source / name).is_file()]
    if missing:
        typer.echo(f"missing required source: {', '.join(missing)}", err=True)
        raise typer.Exit(1)
    report = {
        "agent_version": manifest["agent_version"],
        "locked_touchdesigner_version": manifest["locked_touchdesigner_version"],
        "protocol_versions": manifest["protocol_versions"],
        "required_files": manifest["required_files"],
        "valid": True,
    }
    typer.echo(json.dumps(report, separators=(",", ":"), sort_keys=True))


@app.command()
def build(
    touchdesigner: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option()],
    source: Annotated[Path, typer.Option(exists=True, file_okay=False)] = Path("agent"),
) -> None:
    """Delegate .tox creation to the locked TouchDesigner runtime."""
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    locked = manifest["locked_touchdesigner_version"]
    if locked not in str(touchdesigner):
        typer.echo(f"TouchDesigner path must identify locked version {locked}", err=True)
        raise typer.Exit(1)
    completed = subprocess.run(
        [str(touchdesigner), str(source / "build_td.py"), str(source), str(output)], check=False
    )
    if completed.returncode:
        raise typer.Exit(completed.returncode)
