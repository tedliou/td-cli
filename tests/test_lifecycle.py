from copy import deepcopy
from typing import Any

import pytest

from td_cli.daemon.lifecycle import LifecycleBusy, LifecycleEffect, RequestLifecycle
from td_cli.protocol import Command, RequestSnapshot

INSTANCE_ID = "8cf81688-b9a4-4c39-9f92-31c77319c761"
CONNECTION_ID = "4b5fd041-06ed-4c3f-b761-173530d99589"


class MemoryStore:
    def __init__(self) -> None:
        self.requests: dict[str, dict[str, Any]] = {}
        self.fail = False

    async def create_or_get(self, snapshot: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        self._check()
        existing = self.requests.get(str(snapshot["request_id"]))
        if existing is not None:
            return deepcopy(existing), False
        self.requests[str(snapshot["request_id"])] = deepcopy(snapshot)
        return deepcopy(snapshot), True

    async def get(self, request_id: str) -> dict[str, Any] | None:
        self._check()
        value = self.requests.get(request_id)
        return deepcopy(value) if value is not None else None

    async def find_by_statuses(self, statuses: set[str]) -> list[dict[str, Any]]:
        self._check()
        return [deepcopy(item) for item in self.requests.values() if item["status"] in statuses]

    async def compare_and_set(
        self,
        request_id: str,
        *,
        expected_statuses: set[str],
        changes: dict[str, Any],
    ) -> dict[str, Any] | None:
        self._check()
        item = self.requests.get(request_id)
        if item is None or item["status"] not in expected_statuses:
            return None
        item.update(deepcopy(changes))
        return deepcopy(item)

    def _check(self) -> None:
        if self.fail:
            raise RuntimeError("store unavailable")


def request(number: int) -> dict[str, Any]:
    request_id = f"018f47ec-7f3b-7a34-8f31-2ad70b6f6e{number:02x}"
    return RequestSnapshot.pending(
        request_id=request_id,
        instance_id=INSTANCE_ID,
        command=Command(name="ops.get", input={"operator_path": f"/project{number}"}),
        submitted_at=f"2026-09-01T00:00:0{number}.000Z",
    ).model_dump(mode="json")


async def effect(lifecycle: RequestLifecycle, kind: str) -> LifecycleEffect:
    item = await lifecycle.next_effect()
    assert item.kind == kind
    return item


@pytest.mark.asyncio
async def test_fifo_handshake_authorizes_only_one_request_per_instance() -> None:
    store = MemoryStore()
    ids = iter(["execution-1", "execution-2"])
    lifecycle = RequestLifecycle(store, execution_id=lambda: next(ids))
    await lifecycle.start()
    try:
        await lifecycle.register(INSTANCE_ID, CONNECTION_ID, {"ops.get"})
        await effect(lifecycle, "registered")
        await lifecycle.synchronized(INSTANCE_ID, CONNECTION_ID)
        first, second = request(1), request(2)
        await lifecycle.submit(first)
        await lifecycle.submit(second)

        dispatched = await effect(lifecycle, "request_dispatch")
        assert dispatched.request_id == first["request_id"]
        await lifecycle.accepted(INSTANCE_ID, CONNECTION_ID, str(first["request_id"]))
        assert (await store.get(str(first["request_id"])))["status"] == "accepted"
        await lifecycle.authorize(INSTANCE_ID, CONNECTION_ID, str(first["request_id"]))
        execute = await effect(lifecycle, "request_execute")
        assert execute.execution_id == "execution-1"
        assert (await store.get(str(first["request_id"])))["status"] == "running"
        assert lifecycle.effects_empty()

        await lifecycle.outcome(
            INSTANCE_ID,
            CONNECTION_ID,
            str(first["request_id"]),
            "execution-1",
            status="succeeded",
            result={"path": "/project1"},
            error=None,
        )
        await effect(lifecycle, "outcome_recorded")
        next_dispatch = await effect(lifecycle, "request_dispatch")
        assert next_dispatch.request_id == second["request_id"]
    finally:
        await lifecycle.close()


@pytest.mark.parametrize(
    ("phase", "expected"),
    [("dispatched", "queued"), ("accepted", "queued"), ("running", "unknown")],
)
@pytest.mark.asyncio
async def test_disconnect_has_deterministic_pre_and_post_authorization_outcomes(
    phase: str, expected: str
) -> None:
    store = MemoryStore()
    lifecycle = RequestLifecycle(store, execution_id=lambda: "execution-1")
    await lifecycle.start()
    item = request(1)
    try:
        await lifecycle.register(INSTANCE_ID, CONNECTION_ID, {"ops.get"})
        await effect(lifecycle, "registered")
        await lifecycle.synchronized(INSTANCE_ID, CONNECTION_ID)
        await lifecycle.submit(item)
        await effect(lifecycle, "request_dispatch")
        if phase in {"accepted", "running"}:
            await lifecycle.accepted(INSTANCE_ID, CONNECTION_ID, str(item["request_id"]))
        if phase == "running":
            await lifecycle.authorize(INSTANCE_ID, CONNECTION_ID, str(item["request_id"]))
            await effect(lifecycle, "request_execute")

        await lifecycle.disconnect(INSTANCE_ID, CONNECTION_ID)
        persisted = await store.get(str(item["request_id"]))
        assert persisted is not None and persisted["status"] == expected
        if expected == "queued":
            assert persisted["execution_id"] is None
        else:
            assert persisted["execution_id"] == "execution-1"
    finally:
        await lifecycle.close()


@pytest.mark.asyncio
async def test_old_generation_messages_and_deadlines_are_no_ops() -> None:
    store = MemoryStore()
    clock = [0.0]
    new_connection = "047126bb-747e-4f92-878d-931142c14531"
    lifecycle = RequestLifecycle(store, monotonic=lambda: clock[0])
    await lifecycle.start()
    try:
        await lifecycle.register(INSTANCE_ID, CONNECTION_ID, {"ops.get"})
        await effect(lifecycle, "registered")
        await lifecycle.register(INSTANCE_ID, new_connection, {"ops.get"})
        await effect(lifecycle, "registered")
        clock[0] = 5
        await lifecycle.heartbeat(INSTANCE_ID, new_connection)
        await lifecycle.synchronized(INSTANCE_ID, new_connection)
        item = request(1)
        await lifecycle.submit(item)
        await effect(lifecycle, "request_dispatch")

        await lifecycle.accepted(INSTANCE_ID, CONNECTION_ID, str(item["request_id"]))
        await lifecycle.disconnect(INSTANCE_ID, CONNECTION_ID)
        clock[0] = 7
        await lifecycle.expire_deadlines()

        persisted = await store.get(str(item["request_id"]))
        view = await lifecycle.snapshot()
        assert persisted is not None and persisted["status"] == "dispatched"
        assert view["instances"][INSTANCE_ID]["connection_id"] == new_connection
    finally:
        await lifecycle.close()


@pytest.mark.asyncio
async def test_unknown_refines_only_from_matching_execution_outcome() -> None:
    store = MemoryStore()
    new_connection = "047126bb-747e-4f92-878d-931142c14531"
    lifecycle = RequestLifecycle(store, execution_id=lambda: "execution-1")
    await lifecycle.start()
    item = request(1)
    try:
        await lifecycle.register(INSTANCE_ID, CONNECTION_ID, {"ops.get"})
        await effect(lifecycle, "registered")
        await lifecycle.synchronized(INSTANCE_ID, CONNECTION_ID)
        await lifecycle.submit(item)
        await effect(lifecycle, "request_dispatch")
        await lifecycle.accepted(INSTANCE_ID, CONNECTION_ID, str(item["request_id"]))
        await lifecycle.authorize(INSTANCE_ID, CONNECTION_ID, str(item["request_id"]))
        await effect(lifecycle, "request_execute")
        await lifecycle.disconnect(INSTANCE_ID, CONNECTION_ID)
        await lifecycle.register(INSTANCE_ID, new_connection, {"ops.get"})
        await effect(lifecycle, "registered")
        await lifecycle.synchronized(INSTANCE_ID, new_connection)

        await lifecycle.outcome(
            INSTANCE_ID,
            new_connection,
            str(item["request_id"]),
            "wrong-execution",
            status="succeeded",
            result={"path": "/wrong"},
            error=None,
        )
        assert (await store.get(str(item["request_id"])))["status"] == "unknown"
        assert lifecycle.effects_empty()

        await lifecycle.outcome(
            INSTANCE_ID,
            new_connection,
            str(item["request_id"]),
            "execution-1",
            status="succeeded",
            result={"path": "/project1"},
            error=None,
        )
        await effect(lifecycle, "outcome_recorded")
        assert (await store.get(str(item["request_id"])))["status"] == "succeeded"
    finally:
        await lifecycle.close()


@pytest.mark.asyncio
async def test_execution_lease_suppresses_heartbeat_expiry_until_class_deadline() -> None:
    store = MemoryStore()
    clock = [0.0]
    lifecycle = RequestLifecycle(
        store,
        monotonic=lambda: clock[0],
        execution_id=lambda: "execution-1",
        heartbeat_timeout=2,
        execution_leases={"fast_read": 10},
    )
    await lifecycle.start()
    item = request(1)
    try:
        await lifecycle.register(INSTANCE_ID, CONNECTION_ID, {"ops.get"})
        await effect(lifecycle, "registered")
        await lifecycle.synchronized(INSTANCE_ID, CONNECTION_ID)
        await lifecycle.submit(item)
        await effect(lifecycle, "request_dispatch")
        await lifecycle.accepted(INSTANCE_ID, CONNECTION_ID, str(item["request_id"]))
        await lifecycle.authorize(INSTANCE_ID, CONNECTION_ID, str(item["request_id"]))
        await effect(lifecycle, "request_execute")

        clock[0] = 5
        await lifecycle.expire_deadlines()
        assert (await store.get(str(item["request_id"])))["status"] == "running"
        clock[0] = 11
        await lifecycle.expire_deadlines()
        await effect(lifecycle, "connection_expired")
        assert (await store.get(str(item["request_id"])))["status"] == "unknown"
    finally:
        await lifecycle.close()


@pytest.mark.asyncio
async def test_offline_retention_expires_only_current_generation_queue() -> None:
    store = MemoryStore()
    clock = [0.0]
    lifecycle = RequestLifecycle(store, monotonic=lambda: clock[0], offline_retention=10)
    await lifecycle.start()
    item = request(1)
    try:
        await lifecycle.register(INSTANCE_ID, CONNECTION_ID, {"ops.get"})
        await effect(lifecycle, "registered")
        await lifecycle.submit(item)
        await lifecycle.disconnect(INSTANCE_ID, CONNECTION_ID)
        clock[0] = 11
        await lifecycle.expire_deadlines()
        await effect(lifecycle, "instance_expired")
        persisted = await store.get(str(item["request_id"]))
        assert persisted is not None and persisted["status"] == "instance_offline"
    finally:
        await lifecycle.close()


@pytest.mark.asyncio
async def test_lane_capacity_is_bounded_before_persistence() -> None:
    store = MemoryStore()
    lifecycle = RequestLifecycle(store, lane_capacity=1)
    await lifecycle.start()
    try:
        await lifecycle.submit(request(1))
        with pytest.raises(LifecycleBusy, match="lane is full"):
            await lifecycle.submit(request(2))
        assert len(store.requests) == 1
    finally:
        await lifecycle.close()


@pytest.mark.asyncio
async def test_store_failure_stops_admission_and_begins_controlled_shutdown() -> None:
    store = MemoryStore()
    lifecycle = RequestLifecycle(store)
    await lifecycle.start()
    try:
        await lifecycle.register(INSTANCE_ID, CONNECTION_ID, {"ops.get"})
        await effect(lifecycle, "registered")
        store.fail = True
        with pytest.raises(RuntimeError, match="store unavailable"):
            await lifecycle.submit(request(1))
        await effect(lifecycle, "daemon_draining")
        fatal = await effect(lifecycle, "fatal")
        assert fatal.payload == {"type": "RuntimeError"}
        view = await lifecycle.snapshot()
        assert view["ready"] is False
        assert view["accepting"] is False
    finally:
        store.fail = False
        await lifecycle.close()


@pytest.mark.asyncio
async def test_registration_deadline_is_generation_tagged() -> None:
    store = MemoryStore()
    clock = [0.0]
    old_connection = "ee9e0785-0bac-45e9-af46-796aca78f951"
    lifecycle = RequestLifecycle(
        store, monotonic=lambda: clock[0], registration_timeout=5, heartbeat_timeout=10
    )
    await lifecycle.start()
    try:
        await lifecycle.connected(old_connection)
        await lifecycle.connected(CONNECTION_ID)
        await lifecycle.register(INSTANCE_ID, CONNECTION_ID, {"ops.get"})
        await effect(lifecycle, "registered")
        clock[0] = 6
        await lifecycle.expire_deadlines()
        expired = await effect(lifecycle, "registration_expired")
        assert expired.connection_id == old_connection
        view = await lifecycle.snapshot()
        assert view["instances"][INSTANCE_ID]["connection_id"] == CONNECTION_ID
    finally:
        await lifecycle.close()


@pytest.mark.asyncio
async def test_controlled_shutdown_maps_each_nonterminal_authorization_boundary() -> None:
    store = MemoryStore()
    statuses = ("queued", "dispatched", "accepted", "running")
    for number, status in enumerate(statuses, start=1):
        item = request(number)
        item["status"] = status
        if status == "running":
            item["execution_id"] = "execution-1"
        store.requests[str(item["request_id"])] = item
    lifecycle = RequestLifecycle(store)
    await lifecycle.start()
    try:
        await lifecycle.drain()
        observed = {item["status"] for item in store.requests.values()}
        assert observed == {"daemon_shutdown", "unknown"}
        running = next(item for item in store.requests.values() if item["execution_id"] is not None)
        assert running["status"] == "unknown"
        assert running["error"]["code"] == "request_outcome_unknown"
    finally:
        await lifecycle.close()


@pytest.mark.asyncio
async def test_synchronization_refines_unknown_from_matching_retained_outcome() -> None:
    store = MemoryStore()
    item = request(1)
    item.update(status="unknown", execution_id="execution-1")
    store.requests[str(item["request_id"])] = item
    lifecycle = RequestLifecycle(store)
    await lifecycle.start()
    try:
        await lifecycle.register(INSTANCE_ID, CONNECTION_ID, {"ops.get"})
        await effect(lifecycle, "registered")
        await lifecycle.synchronized(
            INSTANCE_ID,
            CONNECTION_ID,
            [
                {
                    "phase": "outcome",
                    "request_id": item["request_id"],
                    "execution_id": "execution-1",
                    "status": "succeeded",
                    "result": {"path": "/project1"},
                    "error": None,
                }
            ],
        )
        await effect(lifecycle, "outcome_recorded")
        persisted = await store.get(str(item["request_id"]))
        assert persisted is not None and persisted["status"] == "succeeded"
    finally:
        await lifecycle.close()
