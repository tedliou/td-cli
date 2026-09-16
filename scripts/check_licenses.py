from __future__ import annotations

import argparse
import email
import tarfile
import zipfile
from pathlib import Path

from td_cli.licensing import license_files


def check_python_artifacts(directory: Path, files: dict[str, Path]) -> None:
    wheels = list(directory.glob("*.whl"))
    sdists = list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("Expected exactly one wheel and one sdist")
    with zipfile.ZipFile(wheels[0]) as wheel:
        metadata_path = next(
            name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = email.message_from_bytes(wheel.read(metadata_path))
        if metadata["License-Expression"] != "MIT" or set(
            metadata.get_all("License-File", [])
        ) != set(files):
            raise ValueError("Wheel license metadata does not match the reviewed snapshot")
        prefix = metadata_path.removesuffix("METADATA") + "licenses/"
        for name, source in files.items():
            if wheel.read(prefix + name) != source.read_bytes():
                raise ValueError(f"Wheel license differs from source: {name}")
    with tarfile.open(sdists[0]) as sdist:
        prefix = sdists[0].name.removesuffix(".tar.gz") + "/"
        for name, source in files.items():
            member = sdist.extractfile(prefix + name)
            if member is None or member.read() != source.read_bytes():
                raise ValueError(f"Sdist license differs from source: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the reviewed third-party license snapshot")
    parser.add_argument("--runtime", action="store_true", help="also verify the build interpreter")
    parser.add_argument("--python-artifacts", type=Path, help="verify a built wheel and sdist")
    args = parser.parse_args()
    files = license_files(Path(__file__).resolve().parents[1], check_runtime=args.runtime)
    if args.python_artifacts:
        check_python_artifacts(args.python_artifacts, files)
    print(f"Verified {len(files)} license documents")


if __name__ == "__main__":
    main()
