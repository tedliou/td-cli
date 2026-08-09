from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from td_cli.release import package_release


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-stage", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("release-dist"))
    parser.add_argument("--source-epoch", type=int, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    binaries = Path("dist")
    if not args.skip_build:
        env = {**os.environ, "PYTHONHASHSEED": "0", "SOURCE_DATE_EPOCH": str(args.source_epoch)}
        for spec in ("td.spec", "td-daemon.spec", "td-agent.spec"):
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
            )
        with tempfile.TemporaryDirectory(prefix="td-cli-clean-") as clean:
            for executable in ("td.exe", "td-daemon.exe", "td-agent.exe"):
                subprocess.run(
                    [str((binaries / executable).resolve()), "--version"], check=True, cwd=clean
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
