from __future__ import annotations

import hashlib
import json
import re
import time
import zipfile
from importlib.metadata import version
from pathlib import Path

RELEASE_VERSION = version("touchdesigner-cli")
LOCKED_TOUCHDESIGNER_VERSION = "2025.32050"
PRE_1_RELEASE_VERSION = re.compile(
    r"^0\.(?:[1-9]\d*)\.(?:0|[1-9]\d*)(?:-(?:alpha|beta|rc)\.(?:0|[1-9]\d*))?$"
)


def is_approved_release_version(value: str) -> bool:
    return PRE_1_RELEASE_VERSION.fullmatch(value) is not None


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


def package_release(
    executables: Path,
    agent_stage: Path,
    output: Path,
    *,
    source_epoch: int,
    expected_commit: str | None = None,
) -> list[Path]:
    validate_agent_stage(agent_stage, expected_commit=expected_commit)
    output.mkdir(parents=True, exist_ok=True)
    layouts = {
        f"td-v{RELEASE_VERSION}-windows-x86_64.zip": {"td.exe": executables / "td.exe"},
        f"td-daemon-v{RELEASE_VERSION}-windows-x86_64.zip": {
            "td-daemon.exe": executables / "td-daemon.exe"
        },
        f"td-agent-cli-v{RELEASE_VERSION}-windows-x86_64.zip": {
            "td-agent.exe": executables / "td-agent.exe"
        },
        f"td-agent-component-v{RELEASE_VERSION}-td{LOCKED_TOUCHDESIGNER_VERSION}.zip": {
            name: agent_stage / name
            for name in ("td-agent.tox", "manifest.json", "verification.json")
        },
    }
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
