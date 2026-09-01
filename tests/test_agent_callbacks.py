from pathlib import Path
from runpy import run_path
from types import SimpleNamespace


class FakeAgentExtension:
    def __init__(self) -> None:
        self.auth_table = None
        self.instance_id = "instance-1"
        self.connection_id = None
        self.draining = False
        self.records = []
        self.authorized = []

    def refresh_auth(self, table) -> None:
        self.auth_table = table

    def registration_payload(self) -> dict[str, object]:
        return {"instance_id": self.instance_id}

    def heartbeat_payload(self) -> dict[str, object]:
        return {"instance_id": self.instance_id, "connection_id": self.connection_id}

    def synchronization_records(self):
        return list(self.records)

    def authorized_records(self):
        return []

    def reserve(self, request):
        return "request_accepted", {**self.heartbeat_payload(), "request_id": request["request_id"]}

    def authorize(self, message):
        self.authorized.append(message["execution_id"])
        return True

    def execute_authorized(self, request_id, execution_id):
        return {
            **self.heartbeat_payload(),
            "request_id": request_id,
            "execution_id": execution_id,
            "status": "succeeded",
            "result": {},
            "error": None,
        }

    def acknowledge_outcome(self, request_id, execution_id=None):
        del request_id, execution_id
        return True

    def release_record(self, request_id):
        del request_id
        return True

    def retention_snapshot(self):
        return {"record_count": len(self.records), "outcome_bytes": 0}

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


class FakeOp:
    def __init__(self, values):
        self.values = values
        self.TDResources = object()

    def __call__(self, name):
        return self.values[name]


def component(extension):
    return SimpleNamespace(ext=SimpleNamespace(Agent=extension), extensions=[extension])


def test_independent_scheduler_uses_tdresources_for_heartbeat() -> None:
    extension = FakeAgentExtension()
    socket = FakeSocket()
    auth_table = object()
    scheduled = []
    lookup = FakeOp({"auth_table": auth_table, "socketio1": socket})

    callbacks = run_path(
        str(Path("agent/heartbeat_execute.py")),
        init_globals={
            "parent": lambda: component(extension),
            "op": lookup,
            "run": lambda script, **options: scheduled.append((script, options)),
        },
    )
    callbacks["onStart"]()
    assert extension.auth_table is auth_table
    assert scheduled[0][1] == {"delayMilliSeconds": 2000, "delayRef": lookup.TDResources}

    extension.connection_id = "connection-1"
    callbacks["schedulerTick"]()
    assert socket.emitted == [("heartbeat", extension.heartbeat_payload())]
    assert len(scheduled) == 2


def test_socket_open_registers_named_extension() -> None:
    extension = FakeAgentExtension()
    extension.draining = True
    socket = FakeSocket()
    callbacks = run_path(
        str(Path("agent/socket_callbacks.py")),
        init_globals={"parent": lambda: component(extension)},
    )
    callbacks["onOpen"](socket)
    assert socket.emitted == [("register", {"instance_id": "instance-1"})]
    assert extension.draining is False


def test_registration_replays_all_execution_phases_before_dispatch() -> None:
    extension = FakeAgentExtension()
    extension.records = [{"phase": "outcome", "request_id": "request-1"}]
    socket = FakeSocket()
    callbacks = run_path(
        str(Path("agent/socket_callbacks.py")),
        init_globals={"parent": lambda: component(extension)},
    )
    callbacks["onReceiveEvent"](socket, 0, {"connection_id": "connection-1"}, "registered")
    assert socket.emitted == [
        (
            "execution_sync",
            {
                "instance_id": "instance-1",
                "connection_id": "connection-1",
                "records": extension.records,
            },
        )
    ]


def test_dispatch_accepts_without_execution_then_authorization_uses_main_thread_run() -> None:
    extension = FakeAgentExtension()
    extension.connection_id = "connection-1"
    socket = FakeSocket()
    scheduled = []
    lookup = FakeOp({})

    def schedule(script, *args, **options):
        scheduled.append((script, args, options))

    callbacks = run_path(
        str(Path("agent/socket_callbacks.py")),
        init_globals={
            "parent": lambda: component(extension),
            "run": schedule,
            "op": lookup,
        },
    )
    callbacks["onReceiveEvent"](socket, 0, {"request_id": "request-2"}, "request_dispatch")
    assert socket.emitted == [
        (
            "request_accepted",
            {
                "request_id": "request-2",
                "instance_id": "instance-1",
                "connection_id": "connection-1",
            },
        )
    ]

    callbacks["onReceiveEvent"](
        socket,
        0,
        {
            "request_id": "request-2",
            "instance_id": "instance-1",
            "connection_id": "connection-1",
            "execution_id": "execution-1",
        },
        "request_execute",
    )
    assert extension.authorized == ["execution-1"]
    assert scheduled[0][2] == {"delayMilliSeconds": 1, "delayRef": lookup.TDResources}
    callbacks["executeScheduled"](socket, "request-2", "execution-1")
    assert socket.emitted[-1][0] == "request_outcome"


def test_orderly_draining_uses_independent_time_and_unregisters_when_empty() -> None:
    extension = FakeAgentExtension()
    extension.connection_id = "connection-1"
    socket = FakeSocket()
    scheduled = []
    lookup = FakeOp({})
    callbacks = run_path(
        str(Path("agent/socket_callbacks.py")),
        init_globals={
            "parent": lambda: component(extension),
            "op": lookup,
            "run": lambda script, *args, **options: scheduled.append((script, args, options)),
        },
    )
    callbacks["onReceiveEvent"](socket, 0, {"deadline_seconds": 1}, "daemon_draining")
    assert extension.draining is True
    assert [event for event, _ in socket.emitted] == ["heartbeat", "unregister"]
    assert socket.par.active is False
    assert scheduled[0][2] == {
        "delayMilliSeconds": 1500,
        "delayRef": lookup.TDResources,
    }
