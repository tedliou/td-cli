"""Locked-build offline replacement of a known canonical Agent component."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

BUILD = "2025.32050"
LIMIT = 64 * 1024 * 1024
SCRIPT_NAMES = (
    "agent_extension",
    "agent_manifest",
    "socket_callbacks",
    "heartbeat_execute",
    "operator_catalog",
)
LEGACY_HASHES = dict(
    zip(
        SCRIPT_NAMES,
        (
            "6b2dc47cfdc142fbbaf2d52b40ab09d64457651de275834498ade7ba86f36de1",
            "985f4f8ab4701485a139671ad4df6282c60c7601b0d67a5b56a589f3150f2369",
            "a9092602153be233d9ea23b4a907371a18b967a61854a590fd698b17ccbb59fe",
            "9df9b2ae5bf526ccd088e949fcbaa0be6af5e8723440d006fc6dbc8b0749c7a0",
            "565725f8064aeac1320379325c3ab24e2e184068ea14b0e1f136f38736720fef",
        ),
        strict=True,
    )
)


class UpgradeError(ValueError):
    pass


def digest(path: Path) -> str:
    if not path.is_file() or path.stat().st_size > LIMIT:
        raise UpgradeError(f"missing or oversized file: {path.name}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_file(path: Path, suffix: str) -> Path:
    path = path.absolute()
    if path.suffix.lower() != suffix or str(path).startswith("\\\\"):
        raise UpgradeError(f"expected local {suffix} file")
    for entry in (path, *path.parents):
        if entry.is_symlink() or (
            entry.exists()
            and getattr(entry.stat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise UpgradeError("reparse paths are unsupported")
    if ":" in str(path)[2:]:
        raise UpgradeError("alternate streams are unsupported")
    digest(path)
    return path.resolve()


class VendorTools:
    def __init__(self, directory: Path, deadline: float):
        self.directory = directory.resolve()
        self.deadline = deadline

    def run(self, name: str, path: Path) -> subprocess.CompletedProcess[str]:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise UpgradeError("upgrade deadline exceeded")
        return subprocess.run(
            [str(self.directory / name), path.name],
            cwd=path.parent,
            capture_output=True,
            text=True,
            timeout=min(remaining, 30),
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def expand(self, path: Path) -> Path:
        result = self.run("toeexpand.exe", path)
        expanded = path.with_name(path.name + ".dir")
        # The locked vendor utility returns 1 on successful expansion.
        if result.returncode != 1 or not expanded.is_dir():
            raise UpgradeError("vendor expansion failed")
        build = (expanded / ".build").read_text(encoding="utf-8")
        if f"build {BUILD}\n" not in build:
            raise UpgradeError("project or artifact uses an unsupported TouchDesigner build")
        return expanded

    def collapse(self, path: Path) -> None:
        if self.run("toecollapse.exe", path).returncode != 0:
            raise UpgradeError("vendor collapse failed")
        digest(path)


def snapshot(root: Path) -> dict[str, str]:
    entries = list(root.rglob("*"))
    if len(entries) > 10000:
        raise UpgradeError("expanded project exceeds entry limit")
    return {p.relative_to(root).as_posix(): digest(p) for p in entries if p.is_file()}


def manifest(component: Path) -> dict[str, Any]:
    raw = (component / "agent_manifest.text").read_bytes()
    try:
        result = json.loads(raw[raw.index(b"{") :].decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise UpgradeError("invalid embedded Agent manifest") from exc
    if not isinstance(result, dict) or result.get("locked_touchdesigner_version") != BUILD:
        raise UpgradeError("unsupported embedded Agent build")
    return result


def script_hashes(component: Path) -> dict[str, str]:
    return {name: digest(component / (name + ".text")) for name in SCRIPT_NAMES}


def identify(root: Path, target: Path) -> tuple[Path, bool]:
    candidates = list(root.rglob("agent_manifest.text"))
    if len(candidates) != 1:
        raise UpgradeError("project must contain exactly one known Agent")
    component = candidates[0].parent
    info = manifest(component)
    hashes = script_hashes(component)
    same = hashes == script_hashes(target)
    if not same and (info.get("agent_version") != "0.3.1" or hashes != LEGACY_HASHES):
        raise UpgradeError("embedded Agent is modified or has an unsupported version")
    if {p.name for p in component.iterdir()} != {p.name for p in target.iterdir()}:
        raise UpgradeError("embedded Agent has unknown or missing children")
    # Parameters define extension initialization and relative callback references.
    for candidate in component.glob("*.parm"):
        actual = candidate.read_bytes()
        wanted = (target / candidate.name).read_bytes()
        if candidate.name == "socketio1.parm":
            actual = actual.replace(b"active 0 off\n", b"").replace(b"active 0 on\n", b"")
            wanted = wanted.replace(b"active 0 off\n", b"").replace(b"active 0 on\n", b"")
        if actual != wanted:
            raise UpgradeError("embedded Agent has modified runtime parameters")
    root_parm = component.with_suffix(".parm")
    root_parameters = root_parm.read_bytes()
    if b"enableexternaltox 0 off\n" in root_parameters:
        root_parameters = b"\n".join(
            line for line in root_parameters.split(b"\n") if not line.startswith(b"externaltox 0 ")
        )
    if root_parameters != target.with_suffix(".parm").read_bytes():
        raise UpgradeError("embedded Agent has modified root parameters or external linkage")
    if not component.with_suffix(".n").read_bytes().startswith(b"COMP:base\n"):
        raise UpgradeError("embedded Agent root is not a base COMP")
    return component, same


def assert_project_closed(project: Path, deadline: float) -> None:
    if os.name != "nt":
        raise UpgradeError("offline upgrade currently requires Windows")
    script = (
        "$ErrorActionPreference='Stop'; "
        "@(Get-CimInstance Win32_Process -Filter \"Name='TouchDesigner.exe'\" | "
        "Select-Object ProcessId,CommandLine) | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=max(0.1, min(10, deadline - time.monotonic())),
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )
    if result.returncode:
        raise UpgradeError("cannot establish TouchDesigner process ownership")
    rows = json.loads(result.stdout or "[]")
    if isinstance(rows, dict):
        rows = [rows]
    if rows:
        raise UpgradeError("close all TouchDesigner processes before offline upgrade")


def upgrade_project(
    project: Path,
    artifact: Path,
    evidence: Path,
    tools_dir: Path,
    expected_sha256: str,
    timeout: float = 90,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    project = local_file(project, ".toe")
    artifact = local_file(artifact, ".tox")
    original = digest(project)
    if original != expected_sha256:
        raise UpgradeError("project digest changed")
    target_evidence = json.loads(evidence.read_text(encoding="utf-8"))
    if (
        target_evidence.get("artifact_sha256") != digest(artifact)
        or target_evidence.get("touchdesigner_version") != BUILD
    ):
        raise UpgradeError("artifact does not match trusted locked-build evidence")
    assert_project_closed(project, deadline)
    vendor = VendorTools(tools_dir, deadline)
    with tempfile.TemporaryDirectory(prefix=".td-agent-upgrade-", dir=project.parent) as temporary:
        scratch = Path(temporary)
        target_file = scratch / "target.tox"
        shutil.copyfile(artifact, target_file)
        if digest(target_file) != target_evidence["artifact_sha256"]:
            raise UpgradeError("artifact changed during staging")
        target_root = vendor.expand(target_file)
        targets = list(target_root.glob("*/agent_manifest.text"))
        if len(targets) != 1:
            raise UpgradeError("target artifact structure is invalid")
        target = targets[0].parent
        if manifest(target).get("agent_version") != "0.4.0":
            raise UpgradeError("unsupported target Agent version")
        staged = scratch / "project.toe"
        shutil.copyfile(project, staged)
        expanded = vendor.expand(staged)
        before = snapshot(expanded)
        component, same = identify(expanded, target)
        operator_path = "/" + component.relative_to(expanded).as_posix()
        if same:
            return {
                "status": "unchanged",
                "path": str(project),
                "sha256": original,
                "agent_path": operator_path,
                "agent_version": "0.4.0",
            }
        # First establish a byte-preserving vendor round-trip baseline.
        vendor.collapse(staged)
        baseline = scratch / "baseline.toe"
        shutil.copyfile(staged, baseline)
        if snapshot(vendor.expand(baseline)) != before:
            raise UpgradeError("vendor round trip changed expanded project content")
        # Keep the existing component root descriptor, location, and parent layout.
        for old in component.iterdir():
            if not old.is_file():
                raise UpgradeError("nested Agent content is unsupported")
            old.unlink()
        for replacement in target.iterdir():
            if not replacement.is_file():
                raise UpgradeError("nested target Agent content is unsupported")
            shutil.copyfile(replacement, component / replacement.name)
        expected = snapshot(expanded)
        vendor.collapse(staged)
        verify = scratch / "verify.toe"
        shutil.copyfile(staged, verify)
        verified = vendor.expand(verify)
        if snapshot(verified) != expected:
            raise UpgradeError("collapsed upgrade does not match intended project content")
        identify(verified, target)
        payload = staged.read_bytes()
    # Cleanup completes before the only mutation of the original project.
    assert_project_closed(project, deadline)
    if time.monotonic() > deadline or digest(project) != original:
        raise UpgradeError("project changed or upgrade deadline exceeded")
    candidate = project.with_name(project.name + ".agent-candidate-" + uuid.uuid4().hex)
    try:
        with candidate.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        upgraded = digest(candidate)
        backup = project.with_name(project.name + ".agent-backup-" + uuid.uuid4().hex)
        with backup.open("xb") as stream:
            stream.write(project.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        if digest(backup) != original or digest(project) != original:
            raise UpgradeError("backup verification failed or project changed")
        os.replace(candidate, project)
        return {
            "status": "upgraded",
            "path": str(project),
            "sha256": upgraded,
            "previous_sha256": original,
            "backup": str(backup),
            "agent_path": operator_path,
            "agent_version": "0.4.0",
        }
    finally:
        if candidate.exists():
            candidate.unlink()
