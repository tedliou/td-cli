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

    def saveByteArray(self, *_):
        return bytearray(b"TD-BINARY")

    def errors(self, recurse=False):
        assert recurse is True
        return ["sample error"]


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


def test_phase_2_runtime_state_is_migrated_without_changing_instance_identity() -> None:
    instance_id = str(uuid.uuid4())
    builtins._td_cli_agent_state = {
        "runtime_session_id": builtins._td_cli_runtime_session_id,
        "instance_id": instance_id,
        "pending_results": {},
        "seen_commands": {},
    }

    upgraded = AgentExt(FakeOwner())
    upgraded._record_event("command.succeeded", "request-1")

    assert upgraded.instance_id == instance_id
    assert upgraded.events == [{"id": 1, "kind": "command.succeeded", "request_id": "request-1"}]


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
        "batch.execute",
        "binary.export",
        "events.read",
        "project.metadata",
        "project.snapshot",
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


def test_agent_rejects_invalid_expression_and_oversized_result_with_typed_errors() -> None:
    root = FakeOperator("/project1")
    root.par.display = FakeParameter(True)
    agent = AgentExt(FakeOwner(), operator_lookup=lambda _: root)
    agent.connection_id = "connection-1"

    invalid_event, invalid = agent.accept(
        {
            "request_id": "invalid-expression",
            "command": {
                "name": "parameters.set",
                "input": {
                    "operator_path": "/project1",
                    "parameter": "display",
                    "mode": "expression",
                    "value": ")",
                },
            },
        }
    )
    assert invalid_event == "request_rejected"
    assert invalid["code"] == "expression_invalid"

    agent.MAX_RESULT_BYTES = 1
    oversized_event, oversized = agent.accept(
        {
            "request_id": "oversized-result",
            "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
        }
    )
    assert oversized_event == "request_rejected"
    assert oversized["code"] == "result_too_large"


def test_phase_3_observation_binary_metadata_and_events_are_bounded() -> None:
    root = FakeOperator("/", family="COMP")
    project1 = FakeOperator("/project1", family="COMP")
    root.children = [project1]
    metadata = SimpleNamespace(
        name="Sample.toe",
        folder="E:/td-cli",
        saveVersion="099",
        saveBuild="2025.32050",
        saveTime="2026-08-09",
        saveOSName="Windows",
        saveOSVersion="11",
    )
    agent = AgentExt(
        FakeOwner(), operator_lookup={"/": root, "/project1": project1}.get, project_info=metadata
    )

    snapshot = agent.execute_command(
        {
            "name": "project.snapshot",
            "input": {"operator_path": "/", "max_depth": 1, "max_operators": 2},
        }
    )
    assert [item["path"] for item in snapshot["operators"]] == ["/", "/project1"]
    exported = agent.execute_command(
        {
            "name": "binary.export",
            "input": {"operator_path": "/project1", "format": "tox", "max_bytes": 100},
        }
    )
    assert exported["data_base64"] == "VEQtQklOQVJZ"
    assert agent.execute_command({"name": "project.metadata", "input": {}})["name"] == "Sample.toe"

    agent._record_event("command.succeeded", "request-1")
    observed = agent.execute_command(
        {"name": "events.read", "input": {"after": 0, "limit": 1, "include_errors": True}}
    )
    assert observed == {
        "events": [{"id": 1, "kind": "command.succeeded", "request_id": "request-1"}],
        "errors": ["sample error"],
        "next_after": 1,
    }


def test_batch_preflights_every_item_before_any_mutation() -> None:
    root = FakeOperator("/project1")
    root.par.display = FakeParameter(True)
    agent = AgentExt(FakeOwner(), operator_lookup=lambda _: root)

    with pytest.raises(module.AgentCommandError, match="parameter_not_found"):
        agent.execute_command(
            {
                "name": "batch.execute",
                "input": {
                    "commands": [
                        {
                            "name": "parameters.set",
                            "input": {
                                "operator_path": "/project1",
                                "parameter": "display",
                                "mode": "constant",
                                "value": False,
                            },
                        },
                        {
                            "name": "parameters.get",
                            "input": {"operator_path": "/project1", "parameter": "missing"},
                        },
                    ]
                },
            }
        )
    assert root.par.display.val is True
