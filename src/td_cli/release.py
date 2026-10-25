from __future__ import annotations

import hashlib
import json
import re
import time
import zipfile
from importlib.metadata import version
from pathlib import Path

from td_cli.licensing import license_files

RELEASE_VERSION = version("touchdesigner-cli")
LOCKED_TOUCHDESIGNER_VERSION = "2025.32050"
# Canonical SemVer from 0.1.0 on, with an optional alpha, beta, or rc pre-release; 0.0.x stays
# unreleasable and build metadata is never accepted.
RELEASE_VERSION_PATTERN = re.compile(
    r"^(?:0\.[1-9]\d*|[1-9]\d*\.(?:0|[1-9]\d*))\.(?:0|[1-9]\d*)"
    r"(?:-(?:alpha|beta|rc)\.(?:0|[1-9]\d*))?$"
)


def is_approved_release_version(value: str) -> bool:
    return RELEASE_VERSION_PATTERN.fullmatch(value) is not None


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_agent_stage(stage: Path, *, expected_commit: str | None = None) -> None:
    required = ("td-agent.tox", "manifest.json", "verification.json")
    missing = [name for name in required if not (stage / name).is_file()]
    if missing:
        raise ValueError(f"Agent Component stage is missing: {', '.join(missing)}")
    manifest = _load_json(stage / "manifest.json")
    verification = _load_json(stage / "verification.json")
    if manifest.get("agent_version") != RELEASE_VERSION:
        raise ValueError("Agent Component Release Version does not match pyproject.toml")
    if manifest.get("locked_touchdesigner_version") != LOCKED_TOUCHDESIGNER_VERSION:
        raise ValueError("Agent Component TouchDesigner version is not locked")
    if verification.get("validated") is not True:
        raise ValueError("Agent Component verification did not pass")
    if expected_commit is not None and (
        manifest.get("source_commit") != expected_commit
        or verification.get("source_commit") != expected_commit
    ):
        raise ValueError("Agent Component source commit does not match")
    digest = hashlib.sha256((stage / "td-agent.tox").read_bytes()).hexdigest()
    if manifest.get("artifact_sha256") != digest or verification.get("artifact_sha256") != digest:
        raise ValueError("Agent Component artifact digest does not match")


def _write_zip(path: Path, files: dict[str, Path], source_epoch: int) -> None:
    timestamp = time.gmtime(max(source_epoch, 315532800))[:6]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, timestamp)
            info.create_system = 0
            info.external_attr = 0
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[name].read_bytes(), compresslevel=9)


def _native_files(bundle: Path) -> dict[str, Path]:
    """The native td is one exe; a leftover PyInstaller runtime beside it is not shipped."""
    executable = bundle / f"{bundle.name}.exe"
    if not executable.is_file():
        raise ValueError(f"Release input is missing: {executable}")
    return {executable.name: executable}


def _onedir_files(bundle: Path) -> dict[str, Path]:
    """Map every file of one onedir bundle to archive names.

    The exe sits beside its PyInstaller contents_directory, named "<bundle>-runtime" in packaging.
    """
    files = {
        path.relative_to(bundle).as_posix(): path
        for path in sorted(bundle.rglob("*"))
        if path.is_file()
    }
    runtime = f"{bundle.name}-runtime"
    if not any(name.startswith(f"{runtime}/") for name in files):
        raise ValueError(f"Release input is missing: {bundle / runtime}")
    files.setdefault(f"{bundle.name}.exe", bundle / f"{bundle.name}.exe")
    return files


def package_release(
    executables: Path,
    agent_stage: Path,
    output: Path,
    *,
    source_epoch: int,
    expected_commit: str | None = None,
) -> list[Path]:
    validate_agent_stage(agent_stage, expected_commit=expected_commit)
    notices = license_files(Path("."))
    output.mkdir(parents=True, exist_ok=True)
    layouts = {
        f"td-v{RELEASE_VERSION}-windows-x86_64.zip": _native_files(executables / "td"),
        f"td-daemon-v{RELEASE_VERSION}-windows-x86_64.zip": _onedir_files(
            executables / "td-daemon"
        ),
        f"td-agent-cli-v{RELEASE_VERSION}-windows-x86_64.zip": _onedir_files(
            executables / "td-agent"
        ),
        f"td-agent-component-v{RELEASE_VERSION}-td{LOCKED_TOUCHDESIGNER_VERSION}.zip": {
            name: agent_stage / name
            for name in ("td-agent.tox", "manifest.json", "verification.json")
        },
    }
    for files in layouts.values():
        files.update(notices)
    missing = [
        str(path) for files in layouts.values() for path in files.values() if not path.is_file()
    ]
    if missing:
        raise ValueError(f"Release input is missing: {', '.join(missing)}")
    artifacts = []
    for name in sorted(layouts):
        path = output / name
        _write_zip(path, layouts[name], source_epoch)
        artifacts.append(path)
    checksum_lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}" for path in artifacts
    ]
    (output / "SHA256SUMS").write_text(
        "\n".join(checksum_lines) + "\n", encoding="utf-8", newline="\n"
    )
    install_template = Path("packaging/install.ps1").read_text(encoding="utf-8")
    (output / "install.ps1").write_text(
        install_template.replace("__VERSION__", RELEASE_VERSION), encoding="utf-8", newline="\n"
    )
    (output / "uninstall.ps1").write_text(
        Path("packaging/uninstall.ps1").read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
    )
    return artifacts
