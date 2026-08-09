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


class FakeParameter:
    def __init__(self, value=None, *, mode="constant", read_only=False, pulseable=False) -> None:
        self.val = value
        self.expr = value if mode == "expression" else ""
        self.mode = mode
        self.readOnly = read_only
        self.isPulse = pulseable
        self.pulses = 0

    def eval(self):
        return self.val

    def pulse(self) -> None:
        self.pulses += 1


class FakeOperator:
    def __init__(self, path: str, *, op_type="base", family="COMP") -> None:
        self.path = path
        self.name = path.rsplit("/", 1)[-1]
        self.OPType = op_type
        self.family = family
        self.children = []
        self.par = SimpleNamespace()


from types import SimpleNamespace


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


def test_agent_advertises_and_executes_all_five_typed_commands() -> None:
    root = FakeOperator("/project1")
    child_b = FakeOperator("/project1/z", op_type="null")
    child_a = FakeOperator("/project1/a", op_type="base")
    root.children = [child_b, child_a]
    root.par.display = FakeParameter(True)
    root.par.reset = FakeParameter(pulseable=True)
    operators = {item.path: item for item in (root, child_a, child_b)}
    agent = AgentExt(FakeOwner(), operator_lookup=operators.get)

    assert agent.registration_payload()["capabilities"] == [
        "ops.children",
        "ops.get",
        "parameters.get",
        "parameters.pulse",
        "parameters.set",
    ]
    assert agent.execute_command({"name": "ops.get", "input": {"operator_path": "/project1"}}) == {
        "path": "/project1",
        "name": "project1",
        "op_type": "base",
        "family": "COMP",
    }
    assert agent.execute_command(
        {"name": "ops.children", "input": {"operator_path": "/project1", "op_type": None}}
    ) == [
        {"path": "/project1/a", "name": "a", "op_type": "base", "family": "COMP"},
        {"path": "/project1/z", "name": "z", "op_type": "null", "family": "COMP"},
    ]
    assert (
        agent.execute_command(
            {
                "name": "parameters.set",
                "input": {
                    "operator_path": "/project1",
                    "parameter": "display",
                    "mode": "constant",
                    "value": False,
                },
            }
        )["value"]
        is False
    )
    assert (
        agent.execute_command(
            {
                "name": "parameters.get",
                "input": {"operator_path": "/project1", "parameter": "display"},
            }
        )["value_type"]
        == "boolean"
    )
    assert agent.execute_command(
        {
            "name": "parameters.pulse",
            "input": {"operator_path": "/project1", "parameter": "reset"},
        }
    ) == {"operator_path": "/project1", "parameter": "reset", "pulsed": True}
    assert root.par.reset.pulses == 1
