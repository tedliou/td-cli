import json
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
        self.heartbeat_generation = 0
        self.runtime_active = True
        self.heartbeat_marks = 0
        self.socket_generations = []
        self.current_socket_generation = None

    def refresh_auth(self, table) -> None:
        self.auth_table = table

    def clear_auth(self, table) -> None:
        table.clear()

    def registration_payload(self) -> dict[str, object]:
        return {"instance_id": self.instance_id}

    def begin_socket_generation(self):
        generation = f"socket-{len(self.socket_generations) + 1}"
        self.socket_generations.append(generation)
        self.current_socket_generation = generation
        return generation

    def end_socket_generation(self):
        if not self.socket_generations:
            return False
        generation = self.socket_generations.pop(0)
        if generation != self.current_socket_generation:
            return False
        self.current_socket_generation = None
        self.connection_id = None
        return True

    def heartbeat_payload(self) -> dict[str, object]:
        return {"instance_id": self.instance_id, "connection_id": self.connection_id}

    def synchronization_records(self):
        return list(self.records)

    def outcome_chunks(self, outcome):
        payload = json.dumps(outcome, separators=(",", ":"), sort_keys=True)
        size = 24 * 1024
        count = (len(payload) + size - 1) // size
        return [
            {
                **self.heartbeat_payload(),
                "request_id": outcome["request_id"],
                "execution_id": outcome["execution_id"],
                "chunk_index": index,
                "chunk_count": count,
                "payload": payload[offset : offset + size],
            }
            for index, offset in enumerate(range(0, len(payload), size))
        ]

    def rebind_connection(self, connection_id):
        self.connection_id = connection_id
        for record in self.records:
            record["connection_id"] = connection_id

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

    def start_heartbeat(self):
        self.heartbeat_generation += 1
        return f"generation-{self.heartbeat_generation}"

    def stop_heartbeat(self):
        self.heartbeat_generation += 1

    def heartbeat_active(self, generation):
        return generation == f"generation-{self.heartbeat_generation}"

    def mark_heartbeat(self):
        self.heartbeat_marks += 1


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


def component(extension, reinitialize=None):
    pulse = SimpleNamespace(pulse=reinitialize or (lambda: None))
    return SimpleNamespace(
        ext=SimpleNamespace(Agent=extension),
        extensions=[extension],
        par=SimpleNamespace(reinitextensions=pulse),
    )


def test_connection_starts_generation_tagged_independent_heartbeat_scheduler() -> None:
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
            "run": lambda script, *args, **options: scheduled.append((script, args, options)),
        },
    )
    assert "onStart" not in callbacks
    callbacks["startScheduler"]()
    assert scheduled[0][0] is callbacks["schedulerTick"]
    generation = scheduled[0][1][0]
    assert isinstance(generation, str)
    assert scheduled[0][2] == {"delayMilliSeconds": 2000, "delayRef": lookup.TDResources}

    extension.connection_id = "connection-1"
    callbacks["schedulerTick"](generation)
    assert socket.emitted == [("heartbeat", extension.heartbeat_payload())]
    assert extension.heartbeat_marks == 1
    assert len(scheduled) == 2

    callbacks["stopScheduler"]()
    callbacks["schedulerTick"](generation)
    assert len(scheduled) == 2


def test_socket_open_registers_named_extension() -> None:
    extension = FakeAgentExtension()
    extension.draining = True
    socket = FakeSocket()
    heartbeat_calls = []
    auth_table = SimpleNamespace(clear=lambda: heartbeat_calls.append("clear-auth"))
    lookup = FakeOp(
        {
            "auth_table": auth_table,
            "heartbeat_execute": SimpleNamespace(
                module=SimpleNamespace(startScheduler=lambda: heartbeat_calls.append("start"))
            ),
        }
    )
    callbacks = run_path(
        str(Path("agent/socket_callbacks.py")),
        init_globals={"parent": lambda: component(extension), "op": lookup},
    )
    callbacks["onOpen"](socket)
    assert socket.emitted == [("register", {"instance_id": "instance-1"})]
    assert extension.draining is False
    assert heartbeat_calls == ["start", "clear-auth"]


def test_socket_close_invalidates_heartbeat_generation() -> None:
    extension = FakeAgentExtension()
    extension.connection_id = "connection-1"
    extension.begin_socket_generation()
    stopped = []
    lookup = FakeOp(
        {
            "auth_table": object(),
            "heartbeat_execute": SimpleNamespace(
                module=SimpleNamespace(stopScheduler=lambda: stopped.append(True))
            ),
        }
    )
    callbacks = run_path(
        str(Path("agent/socket_callbacks.py")),
        init_globals={"parent": lambda: component(extension), "op": lookup},
    )

    callbacks["onClose"](FakeSocket(), object())

    assert extension.connection_id is None
    assert stopped == [True]


def test_stale_close_cannot_stop_new_socket_generation() -> None:
    extension = FakeAgentExtension()
    extension.begin_socket_generation()
    extension.begin_socket_generation()
    stopped = []
    lookup = FakeOp(
        {
            "auth_table": object(),
            "heartbeat_execute": SimpleNamespace(
                module=SimpleNamespace(stopScheduler=lambda: stopped.append(True))
            ),
        }
    )
    callbacks = run_path(
        str(Path("agent/socket_callbacks.py")),
        init_globals={"parent": lambda: component(extension), "op": lookup},
    )

    callbacks["onClose"](FakeSocket(), object())

    assert extension.current_socket_generation == "socket-2"
    assert stopped == []


def test_registration_replays_all_execution_phases_before_dispatch() -> None:
    extension = FakeAgentExtension()
    outcome = {
        "request_id": "request-1",
        "instance_id": "instance-1",
        "connection_id": "old-connection",
        "execution_id": "execution-1",
        "status": "succeeded",
        "result": {"payload": "x" * 100},
        "error": None,
    }
    extension.records = [{"phase": "outcome", **outcome}]
    socket = FakeSocket()
    callbacks = run_path(
        str(Path("agent/socket_callbacks.py")),
        init_globals={"parent": lambda: component(extension)},
    )
    callbacks["onReceiveEvent"](socket, 0, {"connection_id": "connection-1"}, "registered")
    assert extension.connection_id == "connection-1"
    replayed = socket.emitted[:-1]
    assert [event for event, _ in replayed] == ["request_outcome_chunk"] * len(replayed)
    assert json.loads("".join(chunk["payload"] for _, chunk in replayed)) == {
        **outcome,
        "connection_id": "connection-1",
    }
    assert socket.emitted[-1] == (
        "execution_sync",
        {"instance_id": "instance-1", "connection_id": "connection-1", "records": []},
    )


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
    assert scheduled[0][0] is callbacks["executeScheduled"]
    assert scheduled[0][2] == {"delayMilliSeconds": 1, "delayRef": lookup.TDResources}
    callbacks["executeScheduled"](socket, "request-2", "execution-1")
    event, chunk = socket.emitted[-1]
    assert event == "request_outcome_chunk"
    assert json.loads(chunk["payload"])["error"] is None


def test_duplicate_dispatch_chunks_a_retained_failure_with_null_result() -> None:
    extension = FakeAgentExtension()
    extension.connection_id = "connection-1"
    outcome = {
        **extension.heartbeat_payload(),
        "request_id": "request-2",
        "execution_id": "execution-1",
        "status": "failed",
        "result": None,
        "error": {"code": "operator_not_found"},
    }
    extension.reserve = lambda _: ("request_outcome", outcome)
    socket = FakeSocket()
    callbacks = run_path(
        str(Path("agent/socket_callbacks.py")),
        init_globals={"parent": lambda: component(extension)},
    )

    callbacks["onReceiveEvent"](socket, 0, {"request_id": "request-2"}, "request_dispatch")

    assert [event for event, _ in socket.emitted] == ["request_outcome_chunk"]
    assert json.loads(socket.emitted[0][1]["payload"]) == outcome


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
    assert scheduled[0][0] is callbacks["resumeAfterDraining"]
