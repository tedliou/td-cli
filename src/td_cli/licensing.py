"""Validate the reviewed third-party snapshot before distributing it."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import ssl
import sys
import tomllib
import zlib
from pathlib import Path
from xml.parsers import expat


def license_files(root: Path, *, check_runtime: bool = False) -> dict[str, Path]:
    inventory_path = root / "LICENSES/inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    lock_text = (root / "uv.lock").read_text(encoding="utf-8")
    lock = tomllib.loads(lock_text)
    packages = {
        package["name"]: package["version"]
        for package in lock["package"]
        if package["name"] != "touchdesigner-cli"
    }
    if (
        packages != inventory["locked_packages"]
        or hashlib.sha256(lock_text.encode()).hexdigest() != inventory["lock_sha256"]
    ):
        raise ValueError("Third-party license inventory is stale: review uv.lock changes")
    files = {name: root / name for name in ("LICENSE", "THIRD_PARTY_NOTICES.md")}
    files["LICENSES/inventory.json"] = inventory_path
    for name, digest in inventory["files"].items():
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Third-party license is missing or changed: {name}")
        files[name] = path
    actual = {path.relative_to(root).as_posix() for path in (root / "LICENSES").rglob("*")}
    actual = {name for name in actual if (root / name).is_file()}
    if actual != set(inventory["files"]) | {"LICENSES/inventory.json"}:
        raise ValueError("Third-party license inventory does not match LICENSES contents")
    for component in inventory["components"]:
        if not component["files"] or not set(component["files"]) <= files.keys():
            raise ValueError(f"Third-party component has no recorded license: {component['name']}")
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        if not files[name].is_file() or not files[name].read_text(encoding="utf-8").strip():
            raise ValueError(f"Project license document is missing or empty: {name}")
    if check_runtime:
        runtime = {
            "python": sys.version.split()[0],
            "openssl": ssl.OPENSSL_VERSION,
            "sqlite": sqlite3.sqlite_version,
            "zlib": zlib.ZLIB_VERSION,
            "expat": expat.EXPAT_VERSION,
        }
        if runtime != inventory["runtime"]:
            raise ValueError("Python runtime changed: review native third-party license snapshot")
        runtime_license = Path(sys.base_prefix) / "LICENSE.txt"
        if (
            runtime_license.read_bytes()
            != (root / "LICENSES/runtime__CPython__LICENSE.txt").read_bytes()
        ):
            raise ValueError("Python runtime license changed: refresh the reviewed snapshot")
    return files
