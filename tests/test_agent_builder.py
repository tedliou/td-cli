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


class PulseParameter:
    def __init__(self, callback) -> None:
        self.callback = callback

    def pulse(self) -> None:
        self.callback()


class AgentParameters:
    def __init__(self, agent) -> None:
        self.ext0object = None
        self.ext0name = ""
        self.ext0promote = False
        self.reinitextensions = PulseParameter(lambda: setattr(agent, "Agent", FakeExtension()))


class GuardedRuntimeParameters:
    def __init__(self, agent) -> None:
        object.__setattr__(self, "agent", agent)
        object.__setattr__(self, "start", False)
        object.__setattr__(self, "framestart", False)
        object.__setattr__(self, "active", False)

    def __setattr__(self, name: str, value: object) -> None:
        if name in {"start", "framestart", "active"} and value:
            assert hasattr(self.agent, "Agent"), f"{name} enabled before Agent extension"
        object.__setattr__(self, name, value)


class FakeExtension:
    def __init__(self, owner=None) -> None:
        self.owner = owner
        self.auth_table = None

    def refresh_auth(self, table) -> None:
        self.auth_table = table


class ExtensionModule:
    AgentExt = FakeExtension


class FakeAgentComponent:
    def __init__(self) -> None:
        self.par = AgentParameters(self)


def test_runtime_callbacks_start_only_after_promoted_agent_is_ready() -> None:
    builder = load_builder()
    agent = FakeAgentComponent()
    heartbeat = SimpleNamespace(par=GuardedRuntimeParameters(agent))
    socket = SimpleNamespace(par=GuardedRuntimeParameters(agent))
    extension_dat = object()
    auth_table = object()

    builder["activate_agent_runtime"](
        agent=agent,
        extension_dat=extension_dat,
        heartbeat_dat=heartbeat,
        socket_dat=socket,
        auth_table=auth_table,
    )

    assert agent.par.ext0name == "Agent"
    assert agent.par.ext0promote is True
    assert agent.Agent.auth_table is auth_table
    assert heartbeat.par.start is True
    assert heartbeat.par.framestart is True
    assert socket.par.active is True


def test_extension_object_expression_instantiates_agent_extension() -> None:
    builder = load_builder()
    agent = FakeAgentComponent()
    heartbeat = SimpleNamespace(par=GuardedRuntimeParameters(agent))
    socket = SimpleNamespace(par=GuardedRuntimeParameters(agent))
    extension_dat = SimpleNamespace(module=ExtensionModule)

    builder["activate_agent_runtime"](
        agent=agent,
        extension_dat=extension_dat,
        heartbeat_dat=heartbeat,
        socket_dat=socket,
        auth_table=object(),
    )

    expression = agent.par.ext0object
    compiled = compile(expression, "<TouchDesigner Extension 1>", "eval")
    operators = {"./agent_extension": extension_dat}
    extension = eval(
        compiled,
        {"op": operators.__getitem__, "me": agent},
    )

    assert isinstance(extension, FakeExtension)
    assert extension.owner is agent
