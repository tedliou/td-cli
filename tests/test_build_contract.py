import subprocess
import sys
from pathlib import Path

import pytest


def test_build_graph_has_one_spec_per_python_executable() -> None:
    for name in ("td-daemon", "td-agent"):
        text = Path(f"packaging/{name}.spec").read_text(encoding="utf-8")
        assert f'name="{name}"' in text
        assert "EXE(" in text
        # onedir, each with its own runtime directory: the two share one install directory.
        assert "COLLECT(" in text
        assert f'contents_directory="{name}-runtime"' in text
    # td is the native Rust client; no PyInstaller spec builds it any more.
    assert not Path("packaging/td.spec").exists()


def test_build_graph_pins_reproducibility_inputs() -> None:
    text = Path("scripts/build_release.py").read_text(encoding="utf-8")
    assert '"PYTHONHASHSEED": "0"' in text
    assert '"SOURCE_DATE_EPOCH": str(args.source_epoch)' in text


def test_native_td_builds_locked_without_machine_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.path.insert(0, "scripts")
    try:
        import build_release
    finally:
        sys.path.remove("scripts")
    calls = []

    def cargo(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(build_release.subprocess, "run", cargo)
    monkeypatch.setattr(build_release, "NATIVE_TD", tmp_path)
    (tmp_path / "target/release").mkdir(parents=True)
    (tmp_path / "target/release/td.exe").write_bytes(b"native")
    monkeypatch.setenv("RUSTFLAGS", "-C debuginfo=2")

    build_release.build_native_td(tmp_path / "dist", 1_700_000_000)

    [(command, kwargs)] = calls
    assert command == ["cargo", "build", "--release", "--locked"]
    assert kwargs["cwd"] == tmp_path and kwargs["timeout"] > 0
    env = kwargs["env"]
    assert env["SOURCE_DATE_EPOCH"] == "1700000000" and "RUSTFLAGS" not in env
    flags = env["CARGO_ENCODED_RUSTFLAGS"].split(chr(0x1F))
    assert f"--remap-path-prefix={Path.cwd().resolve()}=td-cli" in flags
    assert sorted(path.name for path in (tmp_path / "dist/td").iterdir()) == ["td.exe"]
