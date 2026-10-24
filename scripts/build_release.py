from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from td_cli.licensing import license_files
from td_cli.release import package_release

NATIVE_TD = Path("native/td")
# Every build step finishes well within this; a hung toolchain must not hang the release.
BUILD_TIMEOUT_SECONDS = 1800
VERSION_SMOKE_TIMEOUT_SECONDS = 60


def build_native_td(binaries: Path, source_epoch: int) -> None:
    """Build td.exe with the pinned Rust toolchain, without machine paths in the binary."""
    root = Path.cwd().resolve()
    cargo_home = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo")).resolve()
    rustflags = "\x1f".join(
        [f"--remap-path-prefix={root}=td-cli", f"--remap-path-prefix={cargo_home}=cargo"]
    )
    env = {
        **os.environ,
        "SOURCE_DATE_EPOCH": str(source_epoch),
        "CARGO_ENCODED_RUSTFLAGS": rustflags,
    }
    env.pop("RUSTFLAGS", None)
    subprocess.run(
        ["cargo", "build", "--release", "--locked"],
        check=True,
        cwd=NATIVE_TD,
        env=env,
        timeout=BUILD_TIMEOUT_SECONDS,
    )
    bundle = binaries / "td"
    shutil.rmtree(bundle, ignore_errors=True)
    bundle.mkdir(parents=True)
    shutil.copy2(NATIVE_TD / "target/release/td.exe", bundle / "td.exe")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-stage", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("release-dist"))
    parser.add_argument("--source-epoch", type=int, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    license_files(Path("."), check_runtime=True)
    binaries = Path("dist")
    if not args.skip_build:
        env = {**os.environ, "PYTHONHASHSEED": "0", "SOURCE_DATE_EPOCH": str(args.source_epoch)}
        for spec in ("td-daemon.spec", "td-agent.spec"):
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "PyInstaller",
                    "--clean",
                    "--noconfirm",
                    f"packaging/{spec}",
                ],
                check=True,
                env=env,
                timeout=BUILD_TIMEOUT_SECONDS,
            )
        build_native_td(binaries, args.source_epoch)
        with tempfile.TemporaryDirectory(prefix="td-cli-clean-") as clean:
            for executable in ("td/td.exe", "td-daemon/td-daemon.exe", "td-agent/td-agent.exe"):
                subprocess.run(
                    [str((binaries / executable).resolve()), "--version"],
                    check=True,
                    cwd=clean,
                    timeout=VERSION_SMOKE_TIMEOUT_SECONDS,
                )
    artifacts = package_release(
        binaries,
        args.agent_stage,
        args.output,
        source_epoch=args.source_epoch,
        expected_commit=args.source_commit,
    )
    print("\n".join(str(path) for path in artifacts))


if __name__ == "__main__":
    main()
