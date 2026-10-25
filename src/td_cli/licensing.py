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
from typing import Any
from xml.parsers import expat

_NATIVE_CRATE_KINDS = {"native td Rust crate", "native td Cargo.lock (not built)"}


def _check_native_td(root: Path, inventory: dict[str, Any]) -> None:
    """The native td's crates and Rust toolchain must match the reviewed snapshot."""
    lock_text = (root / "native/td/Cargo.lock").read_text(encoding="utf-8")
    crates = {
        package["name"]: package["version"]
        for package in tomllib.loads(lock_text)["package"]
        if "source" in package
    }
    toolchain_text = (root / "native/td/rust-toolchain.toml").read_text(encoding="utf-8")
    toolchain = tomllib.loads(toolchain_text)["toolchain"]["channel"]
    recorded = {
        (component["name"], component["version"])
        for component in inventory["components"]
        if component["kind"] in _NATIVE_CRATE_KINDS
    }
    if (
        crates != inventory["cargo_packages"]
        or hashlib.sha256(lock_text.encode()).hexdigest() != inventory["cargo_lock_sha256"]
        or toolchain != inventory["rust_toolchain"]
        or set(crates.items()) != recorded
    ):
        raise ValueError(
            "Third-party license inventory is stale: review native td Cargo.lock or toolchain"
        )


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
    _check_native_td(root, inventory)
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
        snapshot = inventory["runtime_variants"].get(runtime["python"])
        if snapshot is None or runtime != snapshot["versions"]:
            raise ValueError(
                f"Python runtime changed: review native third-party license snapshot: {runtime}"
            )
        runtime_license = Path(sys.base_prefix) / "LICENSE.txt"
        if runtime_license.read_bytes() != (root / snapshot["license_file"]).read_bytes():
            raise ValueError("Python runtime license changed: refresh the reviewed snapshot")
    return files
