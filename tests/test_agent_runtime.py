import builtins
import importlib.util
import uuid
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("td_agent_extension", Path("agent/extension.py"))
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
AgentExt = module.AgentExt


@pytest.fixture(autouse=True)
def isolated_touchdesigner_runtime():
    original_session = builtins._td_cli_runtime_session_id
    original_state = getattr(builtins, "_td_cli_agent_state", None)
    builtins._td_cli_runtime_session_id = str(uuid.uuid4())
    if hasattr(builtins, "_td_cli_agent_state"):
        del builtins._td_cli_agent_state
    try:
        yield
    finally:
        builtins._td_cli_runtime_session_id = original_session
        if original_state is None:
            if hasattr(builtins, "_td_cli_agent_state"):
                del builtins._td_cli_agent_state
        else:
            builtins._td_cli_agent_state = original_state


class FakeOwner:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def fetch(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)

    def store(self, key: str, value: object) -> None:
        self.values[key] = value


def test_extension_reload_preserves_instance_identity_and_unconfirmed_results() -> None:
    owner = FakeOwner()
    first = AgentExt(owner)
    first.pending_results["request"] = {"result": "pending"}

    reloaded = AgentExt(owner)
    assert reloaded.instance_id == first.instance_id
    assert reloaded.pending_results == {"request": {"result": "pending"}}


def test_new_touchdesigner_runtime_session_creates_new_instance_identity() -> None:
    owner = FakeOwner()
    first = AgentExt(owner)
    original_session = builtins._td_cli_runtime_session_id
    try:
        builtins._td_cli_runtime_session_id = "new-runtime-session"
        restarted = AgentExt(owner)
        assert restarted.instance_id != first.instance_id
        assert restarted.pending_results == {}
    finally:
        builtins._td_cli_runtime_session_id = original_session


def test_replacing_agent_component_in_same_runtime_preserves_identity_and_results() -> None:
    first = AgentExt(FakeOwner())
    first.pending_results["request"] = {"result": "pending"}

    replacement = AgentExt(FakeOwner())
    assert replacement.instance_id == first.instance_id
    assert replacement.pending_results == {"request": {"result": "pending"}}
