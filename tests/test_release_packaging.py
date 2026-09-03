from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from td_cli.release import package_release, validate_agent_stage


def _write_executables(root: Path) -> None:
    for name in ("td.exe", "td-daemon.exe", "td-agent.exe"):
        (root / name).write_bytes(name.encode())


def _write_agent_stage(root: Path, *, source_commit: str = "a" * 40) -> None:
    tox = root / "td-agent.tox"
    tox.write_bytes(b"tox")
    manifest = {
        "agent_version": "0.3.1",
        "locked_touchdesigner_version": "2025.32050",
        "source_commit": source_commit,
        "artifact_sha256": hashlib.sha256(b"tox").hexdigest(),
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "verification.json").write_text(
        json.dumps(
            {
                "validated": True,
                "source_commit": source_commit,
                "artifact_sha256": hashlib.sha256(b"tox").hexdigest(),
            }
        ),
        encoding="utf-8",
    )


def test_release_packaging_creates_the_four_root_layouts_and_sorted_checksums(
    tmp_path: Path,
) -> None:
    executables = tmp_path / "bin"
    agent = tmp_path / "agent"
    output = tmp_path / "dist"
    executables.mkdir()
    agent.mkdir()
    _write_executables(executables)
    _write_agent_stage(agent)

    artifacts = package_release(executables, agent, output, source_epoch=1_700_000_000)
    first_digests = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in artifacts}
    repeated = package_release(executables, agent, output, source_epoch=1_700_000_000)
    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in repeated
    } == first_digests

    assert [path.name for path in artifacts] == [
        "td-agent-cli-v0.3.1-windows-x86_64.zip",
        "td-agent-component-v0.3.1-td2025.32050.zip",
        "td-daemon-v0.3.1-windows-x86_64.zip",
        "td-v0.3.1-windows-x86_64.zip",
    ]
    expected_entries = {
        artifacts[0].name: ["td-agent.exe"],
        artifacts[1].name: ["manifest.json", "td-agent.tox", "verification.json"],
        artifacts[2].name: ["td-daemon.exe"],
        artifacts[3].name: ["td.exe"],
    }
    for archive in artifacts:
        with zipfile.ZipFile(archive) as opened:
            assert opened.namelist() == expected_entries[archive.name]
            assert {item.date_time for item in opened.infolist()} == {(2023, 11, 14, 22, 13, 20)}
            assert {item.create_system for item in opened.infolist()} == {0}

    checksum_lines = (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert [line.split("  ")[1] for line in checksum_lines] == sorted(expected_entries)
    assert all(len(line.split("  ")[0]) == 64 for line in checksum_lines)
    assert "__VERSION__" not in (output / "install.ps1").read_text(encoding="utf-8")
    assert "0.3.1" in (output / "install.ps1").read_text(encoding="utf-8")
    assert (output / "uninstall.ps1").is_file()


def test_agent_stage_rejects_wrong_commit_or_touchdesigner_build(tmp_path: Path) -> None:
    _write_agent_stage(tmp_path, source_commit="b" * 40)

    with pytest.raises(ValueError, match="source commit"):
        validate_agent_stage(tmp_path, expected_commit="a" * 40)

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    manifest["source_commit"] = "a" * 40
    manifest["locked_touchdesigner_version"] = "2025.99999"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="TouchDesigner"):
        validate_agent_stage(tmp_path, expected_commit="a" * 40)


def test_agent_stage_rejects_verification_for_another_artifact(tmp_path: Path) -> None:
    _write_agent_stage(tmp_path)
    verification = json.loads((tmp_path / "verification.json").read_text(encoding="utf-8"))
    verification["artifact_sha256"] = hashlib.sha256(b"old").hexdigest()
    (tmp_path / "verification.json").write_text(json.dumps(verification), encoding="utf-8")

    with pytest.raises(ValueError, match="artifact digest"):
        validate_agent_stage(tmp_path, expected_commit="a" * 40)
