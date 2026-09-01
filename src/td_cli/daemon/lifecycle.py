from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, TypeAlias

from td_cli.command_catalog import COMMAND_CATALOG
from td_cli.daemon.storage import RequestIdentityConflict


class LifecycleStore(Protocol):
    async def create_or_get(self, snapshot: dict[str, Any]) -> tuple[dict[str, Any], bool]: ...

    async def get(self, request_id: str) -> dict[str, Any] | None: ...

    async def find_by_statuses(self, statuses: set[str]) -> list[dict[str, Any]]: ...

    async def compare_and_set(
        self,
        request_id: str,
        *,
        expected_statuses: set[str],
        changes: dict[str, Any],
    ) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class LifecycleEffect:
    kind: str
    instance_id: str | None = None
    connection_id: str | None = None
    request_id: str | None = None
    execution_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Connection:
    connection_id: str
    capabilities: frozenset[str]
    synchronized: bool = False
    draining: bool = False
    heartbeat_deadline: float | None = None
    lease_deadline: float | None = None


@dataclass(frozen=True)
class _Offline:
    connection_id: str
    deadline: float


@dataclass
class _Call:
    operation: LifecycleOperation
    arguments: tuple[Any, ...]
    response: asyncio.Future[Any]


class LifecycleBusy(RuntimeError):
    """The bounded Lifecycle inbox cannot accept more work."""


class AdmissionRejected(RuntimeError):
    """A Request cannot enter its target Instance lane."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class RequestLifecycle:
    """Single-owner authority for Request, Instance and Connection state."""

    def __init__(
        self,
        store: LifecycleStore,
        *,
        monotonic: Callable[[], float] | None = None,
        now: Callable[[], str] | None = None,
        execution_id: Callable[[], str] | None = None,
        inbox_capacity: int = 256,
        effect_capacity: int = 256,
        lane_capacity: int = 32,
        registration_timeout: float = 5.0,
        heartbeat_timeout: float = 6.0,
        offline_retention: float = 30.0,
        execution_leases: dict[str, float] | None = None,
    ) -> None:
        self._store = store
        self._monotonic = monotonic or asyncio.get_running_loop().time
        self._now = now or _utc_now
        self._execution_id = execution_id or (lambda: str(uuid.uuid4()))
        self._inbox: asyncio.Queue[_Call] = asyncio.Queue(maxsize=inbox_capacity)
        self._effects: asyncio.Queue[LifecycleEffect] = asyncio.Queue(maxsize=effect_capacity)
        self._lane_capacity = lane_capacity
        self._registration_timeout = registration_timeout
        self._heartbeat_timeout = heartbeat_timeout
        self._offline_retention = offline_retention
        self._execution_leases = execution_leases or {}
        self._connections: dict[str, _Connection] = {}
        self._registration_deadlines: dict[str, float] = {}
        self._offline: dict[str, _Offline] = {}
        self._lanes: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        self._in_flight: dict[str, dict[str, Any]] = {}
        self._task: asyncio.Task[None] | None = None
        self.ready = True
        self.accepting = True

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("RequestLifecycle is already started")
        self._task = asyncio.create_task(self._run(), name="request-lifecycle")
        await self._call("recover")

    async def close(self) -> None:
        if self._task is None:
            return
        await self._call("shutdown", False)
        await self._task
        self._task = None

    async def register(
        self,
        instance_id: str,
        connection_id: str,
        capabilities: set[str],
        *,
        pending_connection_id: str | None = None,
    ) -> None:
        await self._call(
            "register",
            instance_id,
            connection_id,
            frozenset(capabilities),
            pending_connection_id,
        )

    async def connected(self, connection_id: str) -> None:
        await self._call("connected", connection_id)

    async def synchronized(
        self,
        instance_id: str,
        connection_id: str,
        records: list[dict[str, Any]] | None = None,
    ) -> None:
        await self._call("synchronized", instance_id, connection_id, records or [])

    async def submit(self, snapshot: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        return await self._call("submit", snapshot)

    async def accepted(self, instance_id: str, connection_id: str, request_id: str) -> None:
        await self._call("accepted", instance_id, connection_id, request_id)

    async def rejected(
        self, instance_id: str, connection_id: str, request_id: str, code: str
    ) -> None:
        await self._call("rejected", instance_id, connection_id, request_id, code)

    async def authorize(self, instance_id: str, connection_id: str, request_id: str) -> None:
        await self._call("authorize", instance_id, connection_id, request_id)

    async def outcome(
        self,
        instance_id: str,
        connection_id: str,
        request_id: str,
        execution_id: str,
        *,
        status: str,
        result: object,
        error: object,
    ) -> None:
        await self._call(
            "outcome",
            instance_id,
            connection_id,
            request_id,
            execution_id,
            status,
            result,
            error,
        )

    async def disconnect(self, instance_id: str, connection_id: str) -> None:
        await self._call("disconnect", instance_id, connection_id)

    async def heartbeat(self, instance_id: str, connection_id: str) -> None:
        await self._call("heartbeat", instance_id, connection_id)

    async def set_draining(self, instance_id: str, connection_id: str) -> None:
        await self._call("set_draining", instance_id, connection_id)

    async def expire_deadlines(self) -> None:
        await self._call("expire_deadlines")

    async def drain(self) -> None:
        await self._call("shutdown", True)

    async def fail(self, code: str) -> None:
        await self._call("fail", code)

    async def snapshot(self) -> dict[str, Any]:
        return await self._call("snapshot")

    async def next_effect(self) -> LifecycleEffect:
        return await self._effects.get()

    def effects_empty(self) -> bool:
        return self._effects.empty()

    async def _call(self, operation: LifecycleOperation, *arguments: object) -> Any:
        if self._task is None:
            raise RuntimeError("RequestLifecycle is not started")
        response = asyncio.get_running_loop().create_future()
        try:
            self._inbox.put_nowait(_Call(operation, arguments, response))
        except asyncio.QueueFull as error:
            raise LifecycleBusy("RequestLifecycle inbox is full") from error
        return await response

    async def _run(self) -> None:
        while True:
            call = await self._inbox.get()
            stop = call.operation == "shutdown" and not bool(call.arguments[0])
            try:
                result = await self._handle(call.operation, call.arguments)
            except (AdmissionRejected, LifecycleBusy, RequestIdentityConflict) as error:
                call.response.set_exception(error)
            except Exception as error:  # noqa: BLE001 - owner boundary converts dependency failure
                await self._fatal(error)
                if not call.response.done():
                    call.response.set_exception(error)
            else:
                call.response.set_result(result)
            if stop:
                return

    async def _handle(self, operation: LifecycleOperation, arguments: tuple[Any, ...]) -> Any:
        if operation == "recover":
            return await self._recover()
        if operation == "register":
            return await self._register(*arguments)
        if operation == "connected":
            return self._connected(*arguments)
        if operation == "synchronized":
            return await self._synchronized(*arguments)
        if operation == "submit":
            return await self._submit(*arguments)
        if operation == "accepted":
            return await self._accepted(*arguments)
        if operation == "rejected":
            return await self._rejected(*arguments)
        if operation == "authorize":
            return await self._authorize(*arguments)
        if operation == "outcome":
            return await self._outcome(*arguments)
        if operation == "disconnect":
            return await self._disconnect(*arguments)
        if operation == "heartbeat":
            return self._heartbeat(*arguments)
        if operation == "set_draining":
            return self._set_draining(*arguments)
        if operation == "expire_deadlines":
            return await self._expire_deadlines()
        if operation == "snapshot":
            return self._snapshot()
        if operation == "shutdown":
            return await self._shutdown(controlled=bool(arguments[0]))
        if operation == "fail":
            return await self._fatal(RuntimeError(str(arguments[0])))
        raise ValueError(f"unknown Lifecycle operation: {operation}")

    async def _register(
        self,
        instance_id: str,
        connection_id: str,
        capabilities: frozenset[str],
        pending_connection_id: str | None,
    ) -> None:
        self._registration_deadlines.pop(pending_connection_id or connection_id, None)
        existing = self._connections.get(instance_id)
        if existing is not None and existing.connection_id != connection_id:
            await self._disconnect(instance_id, existing.connection_id)
        self._offline.pop(instance_id, None)
        self._connections[instance_id] = _Connection(
            connection_id=connection_id,
            capabilities=capabilities,
            heartbeat_deadline=self._monotonic() + self._heartbeat_timeout,
        )
        self._emit("registered", instance_id, connection_id)

    def _connected(self, connection_id: str) -> None:
        self._registration_deadlines[connection_id] = self._monotonic() + self._registration_timeout

    async def _recover(self) -> None:
        policies = (
            ({"queued", "dispatched", "accepted"}, "daemon_shutdown", "daemon_shutdown"),
            ({"running"}, "unknown", "request_outcome_unknown"),
        )
        for statuses, target, code in policies:
            for item in await self._store.find_by_statuses(statuses):
                await self._store.compare_and_set(
                    str(item["request_id"]),
                    expected_statuses={str(item["status"])},
                    changes={
                        "status": target,
                        "error": _error(code),
                        "completed_at": self._now(),
                    },
                )

    async def _synchronized(
        self, instance_id: str, connection_id: str, records: list[dict[str, Any]]
    ) -> None:
        connection = self._current(instance_id, connection_id)
        if connection is None:
            return
        for record in records:
            request_id = str(record.get("request_id", ""))
            stored = await self._store.get(request_id)
            if stored is None or stored["instance_id"] != instance_id:
                self._emit("record_release", instance_id, connection_id, request_id=request_id)
                continue
            phase = record.get("phase")
            if phase == "outcome":
                await self._outcome(
                    instance_id,
                    connection_id,
                    request_id,
                    str(record.get("execution_id", "")),
                    str(record.get("status", "failed")),
                    record.get("result"),
                    record.get("error"),
                )
            elif phase == "reserved" and stored["status"] in {"dispatched", "accepted"}:
                queued = await self._store.compare_and_set(
                    request_id,
                    expected_statuses={str(stored["status"])},
                    changes={
                        "status": "queued",
                        "execution_id": None,
                        "dispatched_at": None,
                        "accepted_at": None,
                        "execute_authorized_at": None,
                    },
                )
                if queued is not None:
                    self._lanes[instance_id].appendleft(queued)
                self._emit("record_release", instance_id, connection_id, request_id=request_id)
            elif (
                phase in {"authorized", "executing"}
                and stored["status"]
                in {
                    "running",
                    "unknown",
                }
                and stored["execution_id"] == record.get("execution_id")
            ):
                self._in_flight[instance_id] = stored
        connection.synchronized = True
        await self._dispatch_next(instance_id)

    async def _submit(self, snapshot: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        request_id = str(snapshot["request_id"])
        if await self._store.get(request_id) is not None:
            return await self._store.create_or_get(snapshot)
        if not self.accepting:
            raise AdmissionRejected("daemon_shutdown")
        instance_id = str(snapshot["instance_id"])
        connection = self._connections.get(instance_id)
        if connection is None:
            raise AdmissionRejected("instance_offline")
        if connection.draining:
            raise AdmissionRejected("instance_draining")
        command = snapshot.get("command")
        name = command.get("name") if isinstance(command, dict) else None
        if name not in connection.capabilities:
            raise AdmissionRejected("command_unsupported")
        if (
            len(self._lanes[instance_id]) + int(instance_id in self._in_flight)
            >= self._lane_capacity
        ):
            raise LifecycleBusy("Instance Request lane is full")
        persisted, created = await self._store.create_or_get(snapshot)
        known = any(
            item["request_id"] == persisted["request_id"] for item in self._lanes[instance_id]
        )
        if (
            created
            or not known
            and instance_id not in self._in_flight
            and persisted["status"] == "queued"
        ):
            self._lanes[instance_id].append(persisted)
        await self._dispatch_next(instance_id)
        return persisted, created

    async def _dispatch_next(self, instance_id: str) -> None:
        connection = self._connections.get(instance_id)
        if (
            connection is None
            or not connection.synchronized
            or instance_id in self._in_flight
            or not self._lanes[instance_id]
        ):
            return
        queued = self._lanes[instance_id].popleft()
        command = queued.get("command")
        name = command.get("name") if isinstance(command, dict) else None
        if name not in connection.capabilities:
            await self._store.compare_and_set(
                str(queued["request_id"]),
                expected_statuses={"queued"},
                changes={
                    "status": "failed",
                    "error": _error("command_unsupported"),
                    "completed_at": self._now(),
                },
            )
            await self._dispatch_next(instance_id)
            return
        dispatched = await self._store.compare_and_set(
            str(queued["request_id"]),
            expected_statuses={"queued"},
            changes={"status": "dispatched", "dispatched_at": self._now()},
        )
        if dispatched is None:
            await self._dispatch_next(instance_id)
            return
        self._in_flight[instance_id] = dispatched
        self._emit(
            "request_dispatch",
            instance_id,
            connection.connection_id,
            request_id=str(dispatched["request_id"]),
            payload=dispatched,
        )

    async def _accepted(self, instance_id: str, connection_id: str, request_id: str) -> None:
        if self._current(instance_id, connection_id) is None:
            return
        current = self._in_flight.get(instance_id)
        if current is None or current["request_id"] != request_id:
            return
        accepted = await self._store.compare_and_set(
            request_id,
            expected_statuses={"dispatched"},
            changes={"status": "accepted", "accepted_at": self._now()},
        )
        if accepted is not None:
            self._in_flight[instance_id] = accepted

    async def _rejected(
        self, instance_id: str, connection_id: str, request_id: str, code: str
    ) -> None:
        if self._current(instance_id, connection_id) is None:
            return
        current = self._in_flight.get(instance_id)
        if current is None or current["request_id"] != request_id:
            return
        failed = await self._store.compare_and_set(
            request_id,
            expected_statuses={"dispatched"},
            changes={
                "status": "failed",
                "error": _error(code),
                "completed_at": self._now(),
            },
        )
        if failed is not None:
            self._in_flight.pop(instance_id, None)
            await self._dispatch_next(instance_id)

    async def _authorize(self, instance_id: str, connection_id: str, request_id: str) -> None:
        connection = self._current(instance_id, connection_id)
        current = self._in_flight.get(instance_id)
        if connection is None or current is None or current["request_id"] != request_id:
            return
        execution_id = self._execution_id()
        running = await self._store.compare_and_set(
            request_id,
            expected_statuses={"accepted"},
            changes={
                "status": "running",
                "execution_id": execution_id,
                "execute_authorized_at": self._now(),
            },
        )
        if running is None:
            return
        self._in_flight[instance_id] = running
        command = running.get("command")
        name = command.get("name") if isinstance(command, dict) else ""
        execution_class = COMMAND_CATALOG.execution_class(str(name))
        lease = self._execution_leases.get(str(execution_class), 0)
        connection.lease_deadline = self._monotonic() + lease if lease > 0 else None
        self._emit(
            "request_execute",
            instance_id,
            connection_id,
            request_id=request_id,
            execution_id=execution_id,
            payload=running,
        )

    async def _outcome(
        self,
        instance_id: str,
        connection_id: str,
        request_id: str,
        execution_id: str,
        status: str,
        result: object,
        error: object,
    ) -> None:
        if self._current(instance_id, connection_id) is None or status not in {
            "succeeded",
            "failed",
        }:
            return
        stored = await self._store.get(request_id)
        if (
            stored is None
            or stored["instance_id"] != instance_id
            or stored["execution_id"] != execution_id
        ):
            return
        completed = await self._store.compare_and_set(
            request_id,
            expected_statuses={"running", "unknown"},
            changes={
                "status": status,
                "result": COMMAND_CATALOG.normalize_result(stored["command"], result),
                "error": error,
                "completed_at": self._now(),
            },
        )
        if completed is None:
            return
        current = self._in_flight.get(instance_id)
        if current is not None and current["request_id"] == request_id:
            self._in_flight.pop(instance_id, None)
        connection = self._connections.get(instance_id)
        if connection is not None:
            connection.lease_deadline = None
        self._emit(
            "outcome_recorded",
            instance_id,
            connection_id,
            request_id=request_id,
            execution_id=execution_id,
        )
        await self._dispatch_next(instance_id)

    async def _disconnect(self, instance_id: str, connection_id: str) -> None:
        if self._current(instance_id, connection_id) is None:
            return
        self._connections.pop(instance_id, None)
        self._offline[instance_id] = _Offline(
            connection_id, self._monotonic() + self._offline_retention
        )
        current = self._in_flight.pop(instance_id, None)
        if current is None:
            return
        status = str(current["status"])
        if status in {"dispatched", "accepted"}:
            queued = await self._store.compare_and_set(
                str(current["request_id"]),
                expected_statuses={status},
                changes={
                    "status": "queued",
                    "execution_id": None,
                    "dispatched_at": None,
                    "accepted_at": None,
                    "execute_authorized_at": None,
                },
            )
            if queued is not None:
                self._lanes[instance_id].appendleft(queued)
        elif status == "running":
            await self._store.compare_and_set(
                str(current["request_id"]),
                expected_statuses={"running"},
                changes={
                    "status": "unknown",
                    "error": _error("request_outcome_unknown"),
                    "completed_at": self._now(),
                },
            )

    def _heartbeat(self, instance_id: str, connection_id: str) -> None:
        connection = self._current(instance_id, connection_id)
        if connection is not None:
            connection.heartbeat_deadline = self._monotonic() + self._heartbeat_timeout

    def _set_draining(self, instance_id: str, connection_id: str) -> None:
        connection = self._current(instance_id, connection_id)
        if connection is not None:
            connection.draining = True

    async def _expire_deadlines(self) -> None:
        current_time = self._monotonic()
        for connection_id, registration_deadline in list(self._registration_deadlines.items()):
            if current_time >= registration_deadline:
                self._registration_deadlines.pop(connection_id, None)
                self._emit("registration_expired", connection_id=connection_id)
        for instance_id, connection in list(self._connections.items()):
            deadline = connection.heartbeat_deadline
            if connection.lease_deadline is not None:
                deadline = max(deadline or 0, connection.lease_deadline)
            if deadline is not None and current_time >= deadline:
                await self._disconnect(instance_id, connection.connection_id)
                self._emit("connection_expired", instance_id, connection.connection_id)
        for instance_id, offline in list(self._offline.items()):
            if current_time < offline.deadline:
                continue
            self._offline.pop(instance_id, None)
            while self._lanes[instance_id]:
                queued = self._lanes[instance_id].popleft()
                await self._store.compare_and_set(
                    str(queued["request_id"]),
                    expected_statuses={"queued"},
                    changes={
                        "status": "instance_offline",
                        "error": _error("instance_offline"),
                        "completed_at": self._now(),
                    },
                )
            self._emit("instance_expired", instance_id, offline.connection_id)

    async def _shutdown(self, *, controlled: bool) -> None:
        self.accepting = False
        if controlled:
            for instance_id, connection in self._connections.items():
                self._emit("daemon_draining", instance_id, connection.connection_id)
        policies = (
            ({"queued", "dispatched", "accepted"}, "daemon_shutdown", "daemon_shutdown"),
            ({"running"}, "unknown", "request_outcome_unknown"),
        )
        for statuses, target, code in policies:
            for item in await self._store.find_by_statuses(statuses):
                await self._store.compare_and_set(
                    str(item["request_id"]),
                    expected_statuses={str(item["status"])},
                    changes={"status": target, "error": _error(code), "completed_at": self._now()},
                )
        self._lanes.clear()
        self._in_flight.clear()

    def _snapshot(self) -> dict[str, Any]:
        instances = {
            instance_id: {
                "connection_id": connection.connection_id,
                "status": (
                    "draining"
                    if connection.draining
                    else "online"
                    if connection.synchronized
                    else "synchronizing"
                ),
                "synchronized": connection.synchronized,
                "queue_depth": len(self._lanes[instance_id]),
                "in_flight_request_id": (
                    self._in_flight[instance_id]["request_id"]
                    if instance_id in self._in_flight
                    else None
                ),
                "offline_expires_at": None,
            }
            for instance_id, connection in self._connections.items()
        }
        instances.update(
            {
                instance_id: {
                    "connection_id": offline.connection_id,
                    "status": "offline",
                    "synchronized": False,
                    "queue_depth": len(self._lanes[instance_id]),
                    "in_flight_request_id": None,
                    "offline_expires_at": offline.deadline,
                }
                for instance_id, offline in self._offline.items()
                if instance_id not in instances
            }
        )
        return {
            "ready": self.ready,
            "accepting": self.accepting,
            "instances": instances,
        }

    def _current(self, instance_id: str, connection_id: str) -> _Connection | None:
        connection = self._connections.get(instance_id)
        return (
            connection
            if connection is not None and connection.connection_id == connection_id
            else None
        )

    def _emit(
        self,
        kind: str,
        instance_id: str | None = None,
        connection_id: str | None = None,
        *,
        request_id: str | None = None,
        execution_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._effects.put_nowait(
            LifecycleEffect(
                kind, instance_id, connection_id, request_id, execution_id, payload or {}
            )
        )

    async def _fatal(self, error: BaseException) -> None:
        self.ready = False
        self.accepting = False
        try:
            await self._shutdown(controlled=True)
        except Exception:  # noqa: BLE001 - the original fatal error remains authoritative
            self.ready = False
        try:
            self._emit("fatal", payload={"type": type(error).__name__})
        except asyncio.QueueFull:
            pass


def _error(code: str) -> dict[str, object]:
    return {"code": code, "message": code, "details": {}, "retryable": False}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


LifecycleOperation: TypeAlias = Literal[
    "recover",
    "connected",
    "register",
    "synchronized",
    "submit",
    "accepted",
    "rejected",
    "authorize",
    "outcome",
    "disconnect",
    "heartbeat",
    "set_draining",
    "expire_deadlines",
    "snapshot",
    "shutdown",
    "fail",
]
