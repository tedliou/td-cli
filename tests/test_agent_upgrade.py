import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from td_cli import agent_upgrade as upgrade
from td_cli.agent_tool import app


def packed(root: Path, path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for entry in root.rglob("*"):
            if entry.is_file():
                archive.write(entry, entry.relative_to(root).as_posix())
    return path


class ArchiveVendor:
    """Test double for the external vendor executables only."""

    def __init__(self, directory, deadline):
        pass

    def expand(self, path):
        root = path.with_name(path.name + ".dir")
        with zipfile.ZipFile(path) as archive:
            archive.extractall(root)
        return root

    def collapse(self, path):
        packed(path.with_name(path.name + ".dir"), path)


@pytest.fixture
def migration(tmp_path, monkeypatch):
    def component(root, version):
        root.mkdir(parents=True)
        info = {"agent_version": version, "locked_touchdesigner_version": upgrade.BUILD}
        for name in upgrade.SCRIPT_NAMES:
            value = json.dumps(info) if name == "agent_manifest" else version + name
            (root / (name + ".text")).write_text(value)
        (root / "socketio1.parm").write_text("?\nactive 0 off\n?\n")
        root.with_suffix(".n").write_bytes(b"COMP:base\ntile 1 2 3 4\nend\n")
        root.with_suffix(".parm").write_text("?\nenableexternaltox 0 off\n?\n")
        return root

    source_root = tmp_path / "source"
    source = component(source_root / "project1" / "my_agent", "0.3.1")
    (source_root / "media.parm").write_bytes(b"relative media path\x00unchanged")
    target_root = tmp_path / "target"
    component(target_root / "td_agent", "0.4.0")
    project = packed(source_root, tmp_path / "work.toe")
    artifact = packed(target_root, tmp_path / "agent.tox")
    evidence = tmp_path / "manifest.json"
    evidence.write_text(
        json.dumps(
            {
                "artifact_sha256": upgrade.digest(artifact),
                "touchdesigner_version": upgrade.BUILD,
            }
        )
    )
    monkeypatch.setattr(upgrade, "LEGACY_HASHES", upgrade.script_hashes(source))
    monkeypatch.setattr(upgrade, "VendorTools", ArchiveVendor)
    monkeypatch.setattr(upgrade, "assert_project_closed", lambda *args: None)
    return project, artifact, evidence, tmp_path


def invoke(migration):
    project, artifact, evidence, tools = migration
    return CliRunner().invoke(
        app,
        [
            "upgrade-project",
            str(project),
            "--artifact",
            str(artifact),
            "--manifest",
            str(evidence),
            "--tools-dir",
            str(tools),
            "--expected-sha256",
            upgrade.digest(project),
        ],
    )


def test_public_upgrade_preserves_graph_path_and_backup_then_is_noop(migration):
    project, _, _, _ = migration
    original = project.read_bytes()
    result = invoke(migration)
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["agent_path"] == "/project1/my_agent"
    assert Path(report["backup"]).read_bytes() == original
    with zipfile.ZipFile(project) as archive:
        assert archive.read("media.parm") == b"relative media path\x00unchanged"
        assert b'"0.4.0"' in archive.read("project1/my_agent/agent_manifest.text")
    upgraded = project.read_bytes()
    second = invoke(migration)
    assert second.exit_code == 0, second.output
    assert json.loads(second.stdout)["status"] == "unchanged"
    assert project.read_bytes() == upgraded
    assert len(list(project.parent.glob("*.agent-backup-*"))) == 1


def test_vendor_roundtrip_corruption_preserves_original(migration, monkeypatch):
    project, _, _, _ = migration
    original = project.read_bytes()

    class CorruptVendor(ArchiveVendor):
        def collapse(self, path):
            root = path.with_name(path.name + ".dir")
            (root / "media.parm").write_bytes(b"lost artwork")
            super().collapse(path)

    monkeypatch.setattr(upgrade, "VendorTools", CorruptVendor)
    result = invoke(migration)
    assert result.exit_code == 1
    assert "round trip changed" in result.output
    assert project.read_bytes() == original
    assert not list(project.parent.glob("*.agent-backup-*"))


def test_changed_artifact_is_rejected_before_vendor_execution(migration):
    project, artifact, _, _ = migration
    original = project.read_bytes()
    artifact.write_bytes(b"substituted artifact")
    result = invoke(migration)
    assert result.exit_code == 1
    assert "trusted locked-build evidence" in result.output
    assert project.read_bytes() == original


def test_vendor_timeout_has_explicit_failure_and_preserves_original(migration, monkeypatch):
    import subprocess

    project, _, _, _ = migration
    original = project.read_bytes()

    class TimeoutVendor(ArchiveVendor):
        def expand(self, path):
            raise subprocess.TimeoutExpired("toeexpand.exe", 30)

    monkeypatch.setattr(upgrade, "VendorTools", TimeoutVendor)
    result = invoke(migration)
    assert result.exit_code == 1
    assert "upgrade failed:" in result.output
    assert project.read_bytes() == original


def test_running_touchdesigner_is_rejected_even_with_unrelated_launch_path(monkeypatch, tmp_path):
    from types import SimpleNamespace

    monkeypatch.setattr(
        upgrade.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=0, stdout='[{"ProcessId":1,"CommandLine":"TouchDesigner.exe other.toe"}]'
        ),
    )
    with pytest.raises(upgrade.UpgradeError, match="close all TouchDesigner"):
        upgrade.assert_project_closed(tmp_path / "work.toe", 1000000000)


def test_unknown_agent_children_are_rejected(migration):
    project, _, _, _ = migration
    with zipfile.ZipFile(project, "a") as archive:
        archive.writestr("project1/my_agent/custom.dat", "keep me")
    original = project.read_bytes()
    result = invoke(migration)
    assert result.exit_code == 1
    assert "unknown or missing children" in result.output
    assert project.read_bytes() == original


def test_stale_project_precondition_preserves_original(migration):
    project, artifact, evidence, temporary = migration
    original = project.read_bytes()
    with pytest.raises(upgrade.UpgradeError, match="digest changed"):
        upgrade.upgrade_project(project, artifact, evidence, temporary, "0" * 64)
    assert hashlib.sha256(project.read_bytes()).digest() == hashlib.sha256(original).digest()


def test_changed_event_subscriptions_are_not_reported_as_canonical(migration):
    project, artifact, evidence, _ = migration
    for path, prefix in ((project, "project1/my_agent"), (artifact, "td_agent")):
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr(
                prefix + "/events_table.table", "modified" if path == project else "events"
            )
    evidence.write_text(
        json.dumps(
            {
                "artifact_sha256": upgrade.digest(artifact),
                "touchdesigner_version": upgrade.BUILD,
            }
        )
    )
    original = project.read_bytes()
    result = invoke(migration)
    assert result.exit_code == 1
    assert "modified runtime structure" in result.output
    assert project.read_bytes() == original
