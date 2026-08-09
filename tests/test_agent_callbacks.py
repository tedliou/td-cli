from pathlib import Path
from runpy import run_path
from types import SimpleNamespace


class FakeAgentExtension:
    def __init__(self) -> None:
        self.auth_table = None

    def refresh_auth(self, table) -> None:
        self.auth_table = table

    def registration_payload(self) -> dict[str, object]:
        return {"instance_id": "instance-1"}


class FakeSocket:
    def __init__(self) -> None:
        self.emitted = []

    def emit(self, event, payload) -> None:
        self.emitted.append((event, payload))


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
    component = SimpleNamespace(ext=SimpleNamespace(Agent=extension), extensions=[extension])
    socket = FakeSocket()
    callbacks = run_path(
        str(Path("agent/socket_callbacks.py")),
        init_globals={"parent": lambda: component},
    )

    callbacks["onOpen"](socket)

    assert socket.emitted == [("register", {"instance_id": "instance-1"})]
    assert not hasattr(component, "Agent")
