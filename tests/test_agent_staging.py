import hashlib
import json
import subprocess
from pathlib import Path


def test_prepared_agent_stage_carries_release_and_source_identity(tmp_path: Path) -> None:
    artifact = tmp_path / "td-agent.tox"
    artifact.write_bytes(b"tox")
    source_manifest = json.loads(Path("agent/manifest.json").read_text(encoding="utf-8"))
    from td_cli.agent_tool import source_revision

    artifact.with_suffix(".tox.manifest.json").write_text(
        json.dumps(
            {
                "artifact_sha256": hashlib.sha256(b"tox").hexdigest(),
                "operators": source_manifest["required_operators"],
                "source_revision": source_revision(
                    Path("agent"), source_manifest["required_files"]
                ),
                "touchdesigner_version": "2025.32050",
            }
        ),
        encoding="utf-8",
    )
    verification = tmp_path / "locked-validation.json"
    verification.write_text(
        json.dumps(
            {
                "validated": True,
                "checks": ["online"],
                "artifact_sha256": hashlib.sha256(b"tox").hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "stage"

    subprocess.run(
        [
            "uv",
            "run",
            "python",
            "scripts/prepare_agent_stage.py",
            "--artifact",
            str(artifact),
            "--source-commit",
            "a" * 40,
            "--verification",
            str(verification),
            "--output",
            str(output),
        ],
        check=True,
    )

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    evidence = json.loads((output / "verification.json").read_text(encoding="utf-8"))
    assert manifest["agent_version"] == "0.3.1"
    assert manifest["source_commit"] == "a" * 40
    assert evidence == {
        "artifact_sha256": hashlib.sha256(b"tox").hexdigest(),
        "checks": ["online"],
        "source_commit": "a" * 40,
        "validated": True,
    }


def test_prepared_agent_stage_rejects_verification_for_another_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "td-agent.tox"
    artifact.write_bytes(b"tox")
    source_manifest = json.loads(Path("agent/manifest.json").read_text(encoding="utf-8"))
    from td_cli.agent_tool import source_revision

    artifact.with_suffix(".tox.manifest.json").write_text(
        json.dumps(
            {
                "artifact_sha256": hashlib.sha256(b"tox").hexdigest(),
                "operators": source_manifest["required_operators"],
                "source_revision": source_revision(
                    Path("agent"), source_manifest["required_files"]
                ),
                "touchdesigner_version": "2025.32050",
            }
        ),
        encoding="utf-8",
    )
    verification = tmp_path / "locked-validation.json"
    verification.write_text(
        json.dumps({"validated": True, "artifact_sha256": hashlib.sha256(b"old").hexdigest()}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "scripts/prepare_agent_stage.py",
            "--artifact",
            str(artifact),
            "--source-commit",
            "a" * 40,
            "--verification",
            str(verification),
            "--output",
            str(tmp_path / "stage"),
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode != 0
    assert "does not match the Agent artifact" in result.stderr
