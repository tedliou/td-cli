from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(no_args_is_help=True)


def source_revision(source: Path, required_files: list[str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(["manifest.json", *required_files]):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update((source / name).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


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


@app.command("inspect-artifact")
def inspect_artifact(
    artifact: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    source: Annotated[Path, typer.Option(exists=True, file_okay=False)] = Path("agent"),
) -> None:
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    evidence_path = artifact.with_suffix(artifact.suffix + ".manifest.json")
    if not evidence_path.exists():
        typer.echo(f"artifact evidence is missing: {evidence_path}", err=True)
        raise typer.Exit(1)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    expected_revision = source_revision(source, manifest["required_files"])
    problems = []
    if evidence.get("source_revision") != expected_revision:
        problems.append("artifact is stale relative to canonical source")
    if evidence.get("touchdesigner_version") != manifest["locked_touchdesigner_version"]:
        problems.append("artifact was built with an unlocked TouchDesigner version")
    if sorted(evidence.get("operators", [])) != sorted(manifest["required_operators"]):
        problems.append("artifact operator structure does not match manifest")
    if problems:
        typer.echo("; ".join(problems), err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps({**evidence, "valid": True}, separators=(",", ":"), sort_keys=True))


@app.command("build-instructions")
def build_instructions(
    output: Annotated[Path, typer.Option()],
    source: Annotated[Path, typer.Option(exists=True, file_okay=False)] = Path("agent"),
) -> None:
    """Print the exact locked-runtime Textport build command."""
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    revision = source_revision(source, manifest["required_files"])
    script = (source / "build_td.py").resolve()
    command = (
        "op('/project1').create(textDAT, 'td_agent_builder').par.file = "
        f"r'{script}'; op('/project1/td_agent_builder').par.syncfile = True; "
        f"op('/project1/td_agent_builder').run(r'{source.resolve()}', r'{output.resolve()}', "
        f"'{revision}')"
    )
    typer.echo(f"TouchDesigner {manifest['locked_touchdesigner_version']} Textport command:")
    typer.echo(command)
