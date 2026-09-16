from __future__ import annotations

import functools
import http.server
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
            command, capture_output=True, text=True, encoding="utf-8", timeout=60, check=False
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
