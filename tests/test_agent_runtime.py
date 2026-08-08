import builtins
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("td_agent_extension", Path("agent/extension.py"))
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
AgentExt = module.AgentExt


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
