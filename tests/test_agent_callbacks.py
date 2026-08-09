from pathlib import Path
from runpy import run_path
from types import SimpleNamespace


class FakeAgentExtension:
    def __init__(self) -> None:
        self.auth_table = None
        self.instance_id = "instance-1"
        self.connection_id = None
        self.draining = False
        self.last_heartbeat_at = 0.0
        self.pending_results = {"request-1": {"request_id": "request-1", "connection_id": None}}

    def refresh_auth(self, table) -> None:
        self.auth_table = table

    def registration_payload(self) -> dict[str, object]:
        return {"instance_id": "instance-1"}

    def heartbeat_payload(self) -> dict[str, object]:
        return {"instance_id": "instance-1", "connection_id": self.connection_id}

    def accept(self, request):
        return "request_result", {
            "request_id": request["request_id"],
            "connection_id": self.connection_id,
        }

    def begin_draining(self) -> None:
        self.draining = True

    def end_draining(self) -> None:
        self.draining = False


class FakeSocket:
    def __init__(self) -> None:
        self.emitted = []
        self.par = SimpleNamespace(active=True)

    def emit(self, event, *, data) -> None:
        self.emitted.append((event, data))


def test_heartbeat_start_uses_named_extension_object() -> None:
    extension = FakeAgentExtension()
    component = SimpleNamespace(ext=SimpleNamespace(Agent=extension), extensions=[extension])
    auth_table = object()
    callbacks = run_path(
        str(Path("agent/heartbeat_execute.py")),
        init_globals={"parent": lambda: component, "op": lambda path: auth_table},
    )

    callbacks["onStart"]()

    assert extension.auth_table is auth_table
    assert not hasattr(component, "Agent")


def test_socket_open_uses_named_extension_object() -> None:
    extension = FakeAgentExtension()
    extension.draining = True
    component = SimpleNamespace(ext=SimpleNamespace(Agent=extension), extensions=[extension])
    socket = FakeSocket()
    callbacks = run_path(
        str(Path("agent/socket_callbacks.py")),
        init_globals={"parent": lambda: component},
    )

    callbacks["onOpen"](socket)

    assert socket.emitted == [("register", {"instance_id": "instance-1"})]
    assert extension.draining is False
    assert not hasattr(component, "Agent")


def test_periodic_heartbeat_uses_locked_runtime_emit_contract() -> None:
    extension = FakeAgentExtension()
    extension.connection_id = "connection-1"
    component = SimpleNamespace(ext=SimpleNamespace(Agent=extension), extensions=[extension])
    socket = FakeSocket()
    callbacks = run_path(
        str(Path("agent/heartbeat_execute.py")),
        init_globals={
            "parent": lambda: component,
            "op": lambda path: socket,
            "absTime": SimpleNamespace(seconds=3.0),
        },
    )

    callbacks["onFrameStart"](1)

    assert socket.emitted == [
        (
            "heartbeat",
            {"instance_id": "instance-1", "connection_id": "connection-1"},
        )
    ]


def test_registration_replays_results_with_locked_runtime_emit_contract() -> None:
    extension = FakeAgentExtension()
    component = SimpleNamespace(ext=SimpleNamespace(Agent=extension), extensions=[extension])
    socket = FakeSocket()
    callbacks = run_path(
        str(Path("agent/socket_callbacks.py")),
        init_globals={"parent": lambda: component},
    )

    callbacks["onReceiveEvent"](
        socket,
        0,
        {"connection_id": "connection-1"},
        "registered",
    )

    assert extension.connection_id == "connection-1"
    assert socket.emitted == [
        (
            "request_result",
            {"request_id": "request-1", "connection_id": "connection-1"},
        ),
        (
            "results_replayed",
            {"instance_id": "instance-1", "connection_id": "connection-1"},
        ),
    ]


def test_request_dispatch_uses_locked_runtime_emit_contract() -> None:
    extension = FakeAgentExtension()
    extension.connection_id = "connection-1"
    component = SimpleNamespace(ext=SimpleNamespace(Agent=extension), extensions=[extension])
    socket = FakeSocket()
    callbacks = run_path(
        str(Path("agent/socket_callbacks.py")),
        init_globals={"parent": lambda: component},
    )

    callbacks["onReceiveEvent"](
        socket,
        0,
        {"request_id": "request-2", "command": {"name": "ops.get"}},
        "request_dispatch",
    )

    assert socket.emitted == [
        (
            "request_accepted",
            {
                "request_id": "request-2",
                "instance_id": "instance-1",
                "connection_id": "connection-1",
            },
        ),
        (
            "request_result",
            {"request_id": "request-2", "connection_id": "connection-1"},
        ),
    ]


def test_orderly_draining_uses_locked_runtime_emit_contract() -> None:
    extension = FakeAgentExtension()
    extension.connection_id = "connection-1"
    extension.pending_results.clear()
    component = SimpleNamespace(ext=SimpleNamespace(Agent=extension), extensions=[extension])
    socket = FakeSocket()
    scheduled = []

    def schedule(script, dat, **options) -> None:
        scheduled.append((script, dat, options))

    callbacks = run_path(
        str(Path("agent/socket_callbacks.py")),
        init_globals={"parent": lambda: component, "run": schedule, "me": object()},
    )

    callbacks["onReceiveEvent"](
        socket,
        0,
        {"deadline_seconds": 1},
        "daemon_draining",
    )

    assert extension.draining is True
    assert socket.emitted == [
        (
            "heartbeat",
            {"instance_id": "instance-1", "connection_id": "connection-1"},
        ),
        (
            "unregister",
            {"instance_id": "instance-1", "connection_id": "connection-1"},
        ),
    ]
    assert socket.par.active is False
    assert len(scheduled) == 1
    assert scheduled[0][0] == "op('socket_callbacks').module.resumeAfterDraining(args[0])"
    assert scheduled[0][2]["delayMilliSeconds"] == 1500

    callbacks["resumeAfterDraining"](socket)
    assert socket.par.active is True
    assert extension.draining is True
