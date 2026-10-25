from __future__ import annotations

import functools
import http.server
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from test_release_packaging import _write_agent_stage, _write_executables

from td_cli.release import package_release


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is required")
def test_installer_verifies_nested_license_files_on_same_version(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_executables(source)
    _write_agent_stage(source)
    output = tmp_path / "release"
    package_release(source, source, output, source_epoch=1_700_000_000)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(output))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    install = tmp_path / "installed"
    command = [
        "pwsh",
        "-NoProfile",
        "-File",
        str(output / "install.ps1"),
        "-AssetBaseUri",
        f"http://127.0.0.1:{server.server_port}",
        "-InstallRoot",
        str(install),
        "-NonInteractive",
        "-NoPathUpdate",
    ]

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )

    try:
        result = run()
        assert result.returncode == 0, result.stderr
        notice = install / "current/LICENSES/runtime__CPython__LICENSE.txt"
        original = Path("LICENSES/runtime__CPython__LICENSE.txt").read_bytes()
        assert notice.read_bytes() == original
        result = run()
        assert result.returncode == 0, result.stderr
        assert "already installed and verified" in result.stdout
        notice.write_text("tampered", encoding="utf-8")
        result = run()
        assert result.returncode != 0
        assert "Current installation failed verification" in result.stderr
        notice.write_bytes(original)
        notice.unlink()
        result = run()
        assert result.returncode != 0
        assert "Current installation failed verification" in result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _shell_environment(shell: str) -> dict[str, str]:
    environment = dict(os.environ)
    if shell == "powershell":
        # A PowerShell 7 parent's PSModulePath hides the 5.1 built-in modules (Get-FileHash).
        environment["PSModulePath"] = str(
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32/WindowsPowerShell/v1.0/Modules"
        )
    return environment


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_installer_upgrade_replaces_current_junction_and_keeps_old_version(
    tmp_path: Path, shell: str
) -> None:
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} is required")
    source = tmp_path / "source"
    source.mkdir()
    _write_executables(source)
    _write_agent_stage(source)
    output = tmp_path / "release"
    package_release(source, source, output, source_epoch=1_700_000_000)
    install = tmp_path / "installed"
    old_version = install / "versions" / "0.0.1"
    old_version.mkdir(parents=True)
    (old_version / "sentinel.txt").write_text("old", encoding="utf-8")
    # A PyInstaller td from before 0.13.0 kept its runtime beside td.exe.
    (old_version / "td-runtime").mkdir()
    (old_version / "td-runtime" / "python311.dll").write_bytes(b"old runtime")
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(install / "current"), str(old_version)],
        capture_output=True,
        check=True,
        timeout=30,
    )
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(output))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [
                shell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(output / "install.ps1"),
                "-AssetBaseUri",
                f"http://127.0.0.1:{server.server_port}",
                "-InstallRoot",
                str(install),
                "-NonInteractive",
                "-NoPathUpdate",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
            env=_shell_environment(shell),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert result.returncode == 0, result.stderr
    assert "Installed td-cli" in result.stdout
    assert (install / "current" / "td.exe").is_file()
    assert not (install / "current" / "td-runtime").exists()
    assert not (install / "current.previous").exists()
    assert not (install / "current.next").exists()
    assert (old_version / "sentinel.txt").read_text(encoding="utf-8") == "old"


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is required")
def test_uninstall_removes_a_legacy_td_runtime(tmp_path: Path) -> None:
    install = tmp_path / "installed"
    legacy = install / "versions" / "0.12.0" / "td-runtime"
    legacy.mkdir(parents=True)
    (legacy / "python311.dll").write_bytes(b"old runtime")
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(install / "current"), str(legacy.parent)],
        capture_output=True,
        check=True,
        timeout=30,
    )
    result = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-File",
            "packaging/uninstall.ps1",
            "-InstallRoot",
            str(install),
            "-NonInteractive",
            "-NoPathUpdate",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not install.exists()
