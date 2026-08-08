import json
from pathlib import Path

from typer.testing import CliRunner

from td_cli.agent_tool import app


def test_canonical_agent_sources_pass_structural_inspection() -> None:
    result = CliRunner().invoke(app, ["inspect-source", "agent"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report == {
        "agent_version": "0.1.0.dev0",
        "locked_touchdesigner_version": "2025.32050",
        "protocol_versions": [1],
        "required_files": ["extension.py", "socket_callbacks.py"],
        "valid": True,
    }


def test_inspection_rejects_source_that_does_not_match_manifest(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "agent_version": "0.1.0.dev0",
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
