import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from td_cli.agent_tool import app, source_revision


def test_source_revision_is_stable_across_text_checkout_line_endings(
    tmp_path: Path,
) -> None:
    lf_source = tmp_path / "lf"
    crlf_source = tmp_path / "crlf"
    lf_source.mkdir()
    crlf_source.mkdir()
    for source, newline in ((lf_source, "\n"), (crlf_source, "\r\n")):
        (source / "manifest.json").write_text(
            '{"required_files":["extension.py"]}' + newline,
            encoding="utf-8",
            newline="",
        )
        (source / "extension.py").write_text(
            f"def value():{newline}    return 1{newline}",
            encoding="utf-8",
            newline="",
        )

    assert source_revision(lf_source, ["extension.py"]) == source_revision(
        crlf_source, ["extension.py"]
    )


def test_canonical_agent_sources_pass_structural_inspection() -> None:
    result = CliRunner().invoke(app, ["inspect-source", "agent"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report == {
        "agent_version": "0.2.0",
        "locked_touchdesigner_version": "2025.32050",
        "protocol_versions": [1],
        "required_files": [
            "extension.py",
            "socket_callbacks.py",
            "heartbeat_execute.py",
            "build_td.py",
            "touchdesigner-2025.32050-operators.json",
        ],
        "valid": True,
    }


def test_inspection_rejects_source_that_does_not_match_manifest(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "agent_version": "0.1.0",
                "locked_touchdesigner_version": "2025.32050",
                "protocol_versions": [1],
                "required_files": ["missing.py"],
            }
        ),
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["inspect-source", str(tmp_path)])
    assert result.exit_code == 1
    assert "missing.py" in result.stderr


def test_artifact_inspection_ties_tox_to_current_source_revision(tmp_path: Path) -> None:
    artifact = tmp_path / "td-agent.tox"
    artifact.write_bytes(b"derived")
    manifest = json.loads(Path("agent/manifest.json").read_text(encoding="utf-8"))
    evidence = {
        "source_revision": source_revision(Path("agent"), manifest["required_files"]),
        "touchdesigner_version": "2025.32050",
        "operators": manifest["required_operators"],
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
    }
    artifact.with_suffix(".tox.manifest.json").write_text(json.dumps(evidence), encoding="utf-8")

    valid = CliRunner().invoke(app, ["inspect-artifact", str(artifact), "--source", "agent"])
    assert valid.exit_code == 0, valid.output
    assert json.loads(valid.stdout)["valid"] is True

    evidence["source_revision"] = "stale"
    artifact.with_suffix(".tox.manifest.json").write_text(json.dumps(evidence), encoding="utf-8")
    stale = CliRunner().invoke(app, ["inspect-artifact", str(artifact), "--source", "agent"])
    assert stale.exit_code == 1
    assert "stale" in stale.stderr

    evidence["source_revision"] = source_revision(Path("agent"), manifest["required_files"])
    artifact.with_suffix(".tox.manifest.json").write_text(json.dumps(evidence), encoding="utf-8")
    artifact.write_bytes(b"replaced")
    damaged = CliRunner().invoke(app, ["inspect-artifact", str(artifact), "--source", "agent"])
    assert damaged.exit_code == 1
    assert "digest" in damaged.stderr


def test_build_instructions_pin_current_revision_and_output(tmp_path: Path) -> None:
    output = tmp_path / "td-agent.tox"
    result = CliRunner().invoke(
        app, ["build-instructions", "--output", str(output), "--source", "agent"]
    )
    manifest = json.loads(Path("agent/manifest.json").read_text(encoding="utf-8"))
    revision = source_revision(Path("agent"), manifest["required_files"])
    assert result.exit_code == 0, result.output
    assert "2025.32050" in result.stdout
    assert revision in result.stdout
    assert str(output.resolve()) in result.stdout
