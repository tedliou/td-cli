"""Shared identity helpers for disposable locked-runtime acceptance probes."""

import hashlib
import json
from pathlib import Path


def source_revision(source: Path) -> str:
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    for name in sorted(["manifest.json", *manifest["required_files"]]):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update((source / name).read_text(encoding="utf-8").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()
