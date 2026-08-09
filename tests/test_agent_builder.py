from pathlib import Path
from runpy import run_path
from types import SimpleNamespace

import pytest


def load_builder() -> dict[str, object]:
    return run_path(str(Path("agent/build_td.py")))


def test_locked_runtime_uses_touchdesigner_build_as_full_version() -> None:
    builder = load_builder()
    application = SimpleNamespace(version="099", build="2025.32050")

    assert builder["locked_touchdesigner_version"](application) == "2025.32050"


def test_unlocked_runtime_is_rejected_with_observed_build() -> None:
    builder = load_builder()
    application = SimpleNamespace(version="099", build="2026.10000")

    with pytest.raises(
        RuntimeError,
        match=r"locked TouchDesigner 2025\.32050 required; got 2026\.10000",
    ):
        builder["locked_touchdesigner_version"](application)
