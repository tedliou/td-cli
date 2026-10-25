from __future__ import annotations

import hashlib
import json
import shutil
import ssl
import zipfile
from pathlib import Path

import pytest

from td_cli.licensing import license_files
from td_cli.release import package_release, validate_agent_stage


def test_release_build_refuses_unreviewed_native_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssl, "OPENSSL_VERSION", "OpenSSL unreviewed")
    with pytest.raises(ValueError, match="Python runtime changed"):
        license_files(Path("."), check_runtime=True)


# td is one native exe; the Python executables are PyInstaller onedir bundles, so no
# invocation pays a onefile extraction.
RUNTIME_FILES_BY_BUNDLE = {
    "td": (),
    **{
        name: (f"{name}-runtime/base_library.zip", f"{name}-runtime/python311.dll")
        for name in ("td-daemon", "td-agent")
    },
}


def _write_executables(root: Path) -> None:
    for bundle, runtime in RUNTIME_FILES_BY_BUNDLE.items():
        for name in (f"{bundle}.exe", *runtime):
            path = root / bundle / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())


def _write_agent_stage(root: Path, *, source_commit: str = "a" * 40) -> None:
    tox = root / "td-agent.tox"
    tox.write_bytes(b"tox")
    manifest = {
        "agent_version": "1.0.0",
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
        "td-agent-cli-v1.0.0-windows-x86_64.zip",
        "td-agent-component-v1.0.0-td2025.32050.zip",
        "td-daemon-v1.0.0-windows-x86_64.zip",
        "td-v1.0.0-windows-x86_64.zip",
    ]
    expected_entries = {
        artifacts[0].name: ["td-agent.exe", *RUNTIME_FILES_BY_BUNDLE["td-agent"]],
        artifacts[1].name: ["manifest.json", "td-agent.tox", "verification.json"],
        artifacts[2].name: ["td-daemon.exe", *RUNTIME_FILES_BY_BUNDLE["td-daemon"]],
        artifacts[3].name: ["td.exe", *RUNTIME_FILES_BY_BUNDLE["td"]],
    }
    for archive in artifacts:
        with zipfile.ZipFile(archive) as opened:
            notices = license_files(Path("."))
            assert opened.namelist() == sorted(expected_entries[archive.name] + list(notices))
            for name, source in notices.items():
                assert opened.read(name) == source.read_bytes()
            assert {item.date_time for item in opened.infolist()} == {(2023, 11, 14, 22, 13, 20)}
            assert {item.create_system for item in opened.infolist()} == {0}

    checksum_lines = (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert [line.split("  ")[1] for line in checksum_lines] == sorted(expected_entries)
    assert all(len(line.split("  ")[0]) == 64 for line in checksum_lines)
    assert "__VERSION__" not in (output / "install.ps1").read_text(encoding="utf-8")
    assert "1.0.0" in (output / "install.ps1").read_text(encoding="utf-8")
    assert (output / "uninstall.ps1").is_file()


@pytest.mark.parametrize(
    "damage", ["missing", "changed", "stale-lock", "stale-cargo-lock", "stale-toolchain"]
)
def test_release_refuses_incomplete_or_stale_licenses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    (tmp_path / "native/td").mkdir(parents=True)
    for name in (
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "uv.lock",
        "native/td/Cargo.lock",
        "native/td/rust-toolchain.toml",
    ):
        shutil.copyfile(name, tmp_path / name)
    shutil.copytree("LICENSES", tmp_path / "LICENSES")
    _write_executables(tmp_path)
    _write_agent_stage(tmp_path)
    damaged = tmp_path / "LICENSES/runtime__CPython__LICENSE.txt"
    if damage == "missing":
        damaged.unlink()
    elif damage == "changed":
        damaged.write_text("removed upstream notice", encoding="utf-8")
    elif damage == "stale-lock":
        lock = tmp_path / "uv.lock"
        lock.write_text(
            lock.read_text(encoding="utf-8").replace('name = "bidict"', 'name = "new-dependency"'),
            encoding="utf-8",
        )
    elif damage == "stale-cargo-lock":
        lock = tmp_path / "native/td/Cargo.lock"
        lock.write_text(
            lock.read_text(encoding="utf-8").replace('name = "itoa"', 'name = "new-crate"'),
            encoding="utf-8",
        )
    else:
        toolchain = tmp_path / "native/td/rust-toolchain.toml"
        toolchain.write_text(
            toolchain.read_text(encoding="utf-8").replace("1.99.1", "1.100.0"), encoding="utf-8"
        )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="license"):
        package_release(tmp_path, tmp_path, tmp_path / "output", source_epoch=1_700_000_000)
    assert not (tmp_path / "output").exists()


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


@pytest.mark.parametrize("bundle", ["td-daemon", "td-agent"])
def test_release_refuses_an_executable_without_its_onedir_runtime(
    tmp_path: Path, bundle: str
) -> None:
    _write_executables(tmp_path)
    _write_agent_stage(tmp_path)
    shutil.rmtree(tmp_path / bundle / f"{bundle}-runtime")

    with pytest.raises(ValueError, match=f"{bundle}-runtime"):
        package_release(tmp_path, tmp_path, tmp_path / "output", source_epoch=1_700_000_000)


def test_release_ships_only_the_native_td_exe(tmp_path: Path) -> None:
    _write_executables(tmp_path)
    _write_agent_stage(tmp_path)
    leftover = tmp_path / "td/td-runtime/python311.dll"
    leftover.parent.mkdir()
    leftover.write_bytes(b"stale PyInstaller runtime")

    artifacts = package_release(tmp_path, tmp_path, tmp_path / "output", source_epoch=1_700_000_000)

    td = next(path for path in artifacts if path.name.startswith("td-v"))
    with zipfile.ZipFile(td) as opened:
        assert [name for name in opened.namelist() if not name.startswith("LICENSE")] == [
            "THIRD_PARTY_NOTICES.md",
            "td.exe",
        ]


def test_release_refuses_a_missing_native_td(tmp_path: Path) -> None:
    _write_executables(tmp_path)
    _write_agent_stage(tmp_path)
    (tmp_path / "td/td.exe").unlink()

    with pytest.raises(ValueError, match="td.exe"):
        package_release(tmp_path, tmp_path, tmp_path / "output", source_epoch=1_700_000_000)
