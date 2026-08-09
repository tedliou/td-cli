import builtins
import importlib.util
import uuid
from pathlib import Path

import pytest

from td_cli.command_catalog import COMMAND_CATALOG

spec = importlib.util.spec_from_file_location("td_agent_extension", Path("agent/extension.py"))
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
AgentExt = module.AgentExt
OperatorControl = module.OperatorControl


@pytest.fixture(autouse=True)
def isolated_touchdesigner_runtime():
    original_session = builtins._td_cli_runtime_session_id
    original_state = getattr(builtins, "_td_cli_agent_state", None)
    original_app = getattr(builtins, "app", None)
    builtins._td_cli_runtime_session_id = str(uuid.uuid4())
    builtins.app = SimpleNamespace(build="2025.32050")
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
        if original_app is None:
            if hasattr(builtins, "app"):
                del builtins.app
        else:
            builtins.app = original_app


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


class FakeConnector:
    def __init__(self, owner, index: int, *, is_input: bool) -> None:
        self.owner = owner
        self.index = index
        self.isInput = is_input
        self.isOutput = not is_input
        self.connections = []

    def connect(self, target) -> None:
        self.connections.append(target)
        target.connections.append(FakeConnector(self.owner, self.index, is_input=False))

    def disconnect(self) -> None:
        self.connections.clear()


class FakeOperator:
    def __init__(
        self, path: str, *, op_type="base", family="COMP", inputs: int = 0, outputs: int = 0
    ) -> None:
        self.path = path
        self.name = path.rsplit("/", 1)[-1]
        self.OPType = op_type
        self.family = family
        self.children = []
        self.par = SimpleNamespace()
        self.nodeX = 0
        self.nodeY = 0
        self.inputConnectors = [
            FakeConnector(self, index, is_input=True) for index in range(inputs)
        ]
        self.outputConnectors = [
            FakeConnector(self, index, is_input=False) for index in range(outputs)
        ]

    def create(self, op_type: str, name: str):
        created = FakeOperator(f"{self.path}/{name}", op_type=op_type, family="TOP", outputs=1)
        if op_type != "constantTOP":
            created.inputConnectors = [FakeConnector(created, 0, is_input=True)]
        self.children.append(created)
        return created

    def saveByteArray(self, *_):
        return bytearray(b"TD-BINARY")

    def errors(self, recurse=False):
        assert recurse is True
        return ["sample error"]


from types import SimpleNamespace


def test_operator_control_is_the_touchdesigner_graph_interface() -> None:
    root = FakeOperator("/project1")
    control = OperatorControl({root.path: root}.get)

    assert control.execute({"name": "ops.get", "input": {"operator_path": root.path}}) == {
        "path": "/project1",
        "name": "project1",
        "op_type": "base",
        "family": "COMP",
    }
    with pytest.raises(module.AgentCommandError, match="operator_not_found"):
        control.execute({"name": "ops.get", "input": {"operator_path": "/missing"}})


def test_extension_reload_preserves_instance_identity_and_unconfirmed_results() -> None:
    owner = FakeOwner()
    first = AgentExt(owner)
    first.pending_results["request"] = {"result": "pending"}

    reloaded = AgentExt(owner)
    assert reloaded.instance_id == first.instance_id
    assert reloaded.pending_results == {"request": {"result": "pending"}}


def test_extension_rejects_missing_touchdesigner_build() -> None:
    del builtins.app
    with pytest.raises(RuntimeError, match="app build"):
        AgentExt(FakeOwner())


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


def test_agent_advertises_and_executes_all_typed_commands() -> None:
    root = FakeOperator("/project1")
    child_b = FakeOperator("/project1/z", op_type="null")
    child_a = FakeOperator("/project1/a", op_type="base")
    root.children = [child_b, child_a]
    root.par.display = FakeParameter(True)
    root.par.reset = FakeParameter(pulseable=True)
    operators = {item.path: item for item in (root, child_a, child_b)}
    agent = AgentExt(FakeOwner(), operator_lookup=operators.get)

    assert agent.registration_payload()["td_build"] == "2025.32050"
    assert set(agent.registration_payload()["capabilities"]) == set(COMMAND_CATALOG.names)
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


def test_agent_creates_and_connects_a_bounded_basic_network() -> None:
    parent = FakeOperator("/project1")
    operators = {parent.path: parent}

    def lookup(path: str):
        for child in parent.children:
            operators[child.path] = child
        return operators.get(path)

    agent = AgentExt(FakeOwner(), operator_lookup=lookup)
    created = agent.execute_command(
        {
            "name": "ops.create",
            "input": {
                "parent_path": "/project1",
                "op_type": "constantTOP",
                "name": "source",
                "node_x": -100,
                "node_y": 25,
            },
        }
    )
    agent.execute_command(
        {
            "name": "ops.create",
            "input": {
                "parent_path": "/project1",
                "op_type": "nullTOP",
                "name": "output",
                "node_x": 100,
                "node_y": 25,
            },
        }
    )
    connected = agent.execute_command(
        {
            "name": "ops.connect",
            "input": {
                "source_path": "/project1/source",
                "target_path": "/project1/output",
                "output_index": 0,
                "input_index": 0,
            },
        }
    )

    assert created == {
        "path": "/project1/source",
        "name": "source",
        "op_type": "constantTOP",
        "family": "TOP",
    }
    assert operators["/project1/source"].nodeX == -100
    assert operators["/project1/source"].nodeY == 25
    assert connected == {
        "source_path": "/project1/source",
        "target_path": "/project1/output",
        "output_index": 0,
        "input_index": 0,
        "connected": True,
    }
    connection = operators["/project1/output"].inputConnectors[0].connections[0]
    assert (connection.owner.path, connection.index, connection.isOutput) == (
        "/project1/source",
        0,
        True,
    )

    with pytest.raises(module.AgentCommandError, match="operator_already_exists"):
        agent.execute_command(
            {
                "name": "ops.create",
                "input": {
                    "parent_path": "/project1",
                    "op_type": "constantTOP",
                    "name": "source",
                    "node_x": 0,
                    "node_y": 0,
                },
            }
        )

    with pytest.raises(module.AgentCommandError, match="connector_occupied"):
        agent.execute_command(
            {
                "name": "ops.connect",
                "input": {
                    "source_path": "/project1/source",
                    "target_path": "/project1/output",
                    "output_index": 0,
                    "input_index": 0,
                },
            }
        )


def test_network_mutation_failures_roll_back_partial_changes() -> None:
    parent = FakeOperator("/project1")

    class FailingCreated:
        path = "/project1/failing"
        name = "failing"
        family = "TOP"
        OPType = "constantTOP"
        destroyed = False

        @property
        def nodeX(self):
            return 0

        @nodeX.setter
        def nodeX(self, value):
            del value
            raise RuntimeError("position rejected")

        def destroy(self) -> None:
            self.destroyed = True

    partial = FailingCreated()
    parent.create = lambda op_type, name: partial
    operators = {parent.path: parent}
    agent = AgentExt(FakeOwner(), operator_lookup=operators.get)

    with pytest.raises(module.AgentCommandError, match="operator_create_failed"):
        agent.execute_command(
            {
                "name": "ops.create",
                "input": {
                    "parent_path": "/project1",
                    "op_type": "constantTOP",
                    "name": "failing",
                    "node_x": 1,
                    "node_y": 2,
                },
            }
        )
    assert partial.destroyed is True

    source = FakeOperator("/project1/source", family="TOP", outputs=1)
    target = FakeOperator("/project1/target", family="TOP", inputs=1)

    def connect_with_wrong_wrapper(target_connector) -> None:
        wrong_owner = FakeOperator("/project1/wrong", family="TOP")
        target_connector.connections.append(FakeConnector(wrong_owner, 0, is_input=False))

    source.outputConnectors[0].connect = connect_with_wrong_wrapper
    operators.update({source.path: source, target.path: target})

    with pytest.raises(module.AgentCommandError, match="connector_connect_failed"):
        agent.execute_command(
            {
                "name": "ops.connect",
                "input": {
                    "source_path": source.path,
                    "target_path": target.path,
                    "output_index": 0,
                    "input_index": 0,
                },
            }
        )
    assert target.inputConnectors[0].connections == []


def test_agent_rejects_invalid_expression_with_typed_error() -> None:
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


def test_batch_preflights_unsupported_parameter_value_before_mutation() -> None:
    root = FakeOperator("/project1")
    root.par.display = FakeParameter(True)
    root.par.unsupported = FakeParameter(object())
    agent = AgentExt(FakeOwner(), operator_lookup=lambda _: root)

    with pytest.raises(module.AgentCommandError, match="parameter_type_unsupported"):
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
                            "input": {"operator_path": "/project1", "parameter": "unsupported"},
                        },
                    ]
                },
            }
        )
    assert root.par.display.val is True


def test_snapshot_is_deterministic_breadth_first_and_enforces_operator_cap() -> None:
    root = FakeOperator("/project1")
    child_b = FakeOperator("/project1/b")
    child_a = FakeOperator("/project1/a")
    grandchild = FakeOperator("/project1/a/z")
    child_a.children = [grandchild]
    root.children = [child_b, child_a]
    agent = AgentExt(FakeOwner(), operator_lookup=lambda _: root)

    result = agent.execute_command(
        {
            "name": "project.snapshot",
            "input": {"operator_path": "/project1", "max_depth": 2, "max_operators": 4},
        }
    )
    assert [(item["path"], item["depth"]) for item in result["operators"]] == [
        ("/project1", 0),
        ("/project1/a", 1),
        ("/project1/b", 1),
        ("/project1/a/z", 2),
    ]
    with pytest.raises(module.AgentCommandError, match="result_too_large"):
        agent.execute_command(
            {
                "name": "project.snapshot",
                "input": {"operator_path": "/project1", "max_depth": 2, "max_operators": 3},
            }
        )


def test_binary_export_enforces_family_and_raw_byte_cap() -> None:
    comp = FakeOperator("/project1/component", family="COMP")
    agent = AgentExt(FakeOwner(), operator_lookup=lambda _: comp)

    with pytest.raises(module.AgentCommandError, match="command_unsupported"):
        agent.execute_command(
            {
                "name": "binary.export",
                "input": {"operator_path": comp.path, "format": "png", "max_bytes": 100},
            }
        )
    with pytest.raises(module.AgentCommandError, match="result_too_large"):
        agent.execute_command(
            {
                "name": "binary.export",
                "input": {"operator_path": comp.path, "format": "tox", "max_bytes": 1},
            }
        )


def test_event_ring_retains_1000_and_reads_at_most_requested_200() -> None:
    root = FakeOperator("/")
    agent = AgentExt(FakeOwner(), operator_lookup=lambda _: root)
    for index in range(1001):
        agent._record_event("command.succeeded", f"request-{index}")

    result = agent.execute_command(
        {"name": "events.read", "input": {"after": 0, "limit": 200, "include_errors": False}}
    )

    assert len(agent.events) == 1000
    assert len(result["events"]) == 200
    assert result["events"][0]["id"] == 2
    assert result["next_after"] == 201


def test_accept_records_internal_and_oversized_outcomes() -> None:
    root = FakeOperator("/project1")
    lookup_fails = {"value": True}

    def lookup(_):
        if lookup_fails["value"]:
            raise RuntimeError("boom")
        return root

    agent = AgentExt(FakeOwner(), operator_lookup=lookup)
    agent.connection_id = "connection-1"
    event, result = agent.accept(
        {
            "request_id": "internal",
            "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
        }
    )
    assert (event, result["code"]) == ("request_rejected", "internal_error")

    lookup_fails["value"] = False
    agent.MAX_RESULT_BYTES = 1
    event, result = agent.accept(
        {
            "request_id": "oversized",
            "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
        }
    )
    assert (event, result["code"]) == ("request_rejected", "result_too_large")
    assert [(item["request_id"], item["code"]) for item in agent.events] == [
        ("internal", "internal_error"),
        ("oversized", "result_too_large"),
    ]
