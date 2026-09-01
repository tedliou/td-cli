from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import socketio
from fastapi import HTTPException

from td_cli.daemon.app import create_app
from td_cli.daemon.lifecycle import (
    AdmissionRejected,
    LifecycleBusy,
    LifecycleEffect,
    RequestLifecycle,
)
from td_cli.daemon.storage import RequestStore
from td_cli.release import LOCKED_TOUCHDESIGNER_VERSION

LOGGER = logging.getLogger("td_cli.lifecycle")
DEFAULT_EXECUTION_LEASES = {
    "fast_read": 5.0,
    "bounded_scan_or_export": 30.0,
    "bounded_mutation": 30.0,
    "trusted_asset_mutation": 120.0,
}


@dataclass
class _Outbound:
    event: str | None = None
    data: dict[str, Any] | None = None
    disconnect: bool = False
    completed: asyncio.Future[None] | None = None


@dataclass
class _ConnectionAdapter:
    instance_id: str
    connection_id: str
    sid: str
    queue: asyncio.Queue[_Outbound]
    task: asyncio.Task[None] | None = None


@dataclass
class _InstanceMetadata:
    connection_id: str
    status: str
    agent_version: str
    capabilities: list[str]
    last_heartbeat_at: str
    offline_expires_at: str | None = None


def create_transport_app(
    root: Path,
    *,
    token: str,
    heartbeat_timeout: float = 6,
    offline_retention: float = 30,
    shutdown: Callable[[], None] | None = None,
    drain_timeout: float = 5,
    runtime_health: Callable[[], bool] | None = None,
    outbound_capacity: int = 64,
    execution_leases: dict[str, float] | None = None,
) -> socketio.ASGIApp:
    """Create thin authenticated Protocol v2 adapters around RequestLifecycle."""
    sio = socketio.AsyncServer(
        async_mode="asgi", cors_allowed_origins=[], max_http_buffer_size=256 * 1024
    )
    lifecycle: RequestLifecycle | None = None
    effect_task: asyncio.Task[None] | None = None
    deadline_task: asyncio.Task[None] | None = None
    connections: dict[str, _ConnectionAdapter] = {}
    by_sid: dict[str, _ConnectionAdapter] = {}
    pending_sids: set[str] = set()
    metadata: dict[str, _InstanceMetadata] = {}
    stopping = False

    def require_lifecycle() -> RequestLifecycle:
        if lifecycle is None:
            raise RuntimeError("RequestLifecycle is not started")
        return lifecycle

    async def startup(store: RequestStore) -> None:
        nonlocal lifecycle, effect_task, deadline_task
        lifecycle = RequestLifecycle(
            store,
            heartbeat_timeout=heartbeat_timeout,
            offline_retention=offline_retention,
            execution_leases=execution_leases or DEFAULT_EXECUTION_LEASES,
        )
        await lifecycle.start()
        effect_task = asyncio.create_task(effect_pump(), name="lifecycle-effects")
        deadline_task = asyncio.create_task(deadline_scheduler(), name="lifecycle-deadlines")

    async def teardown() -> None:
        nonlocal stopping
        stopping = True
        if lifecycle is not None:
            await lifecycle.close()
        await asyncio.sleep(0)
        for task in (deadline_task, effect_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        for adapter in list(connections.values()):
            await stop_sender(adapter)

    def healthy() -> bool:
        external = runtime_health() if runtime_health is not None else True
        tasks_healthy = all(
            task is None or not task.done() for task in (effect_task, deadline_task)
        )
        return external and lifecycle is not None and lifecycle.ready and tasks_healthy

    async def admit(snapshot: dict[str, object]) -> tuple[dict[str, object], bool]:
        try:
            return await require_lifecycle().submit(snapshot)
        except AdmissionRejected as error:
            status = 503 if error.code == "daemon_shutdown" else 409
            raise HTTPException(status_code=status, detail=error.code) from error
        except LifecycleBusy as error:
            raise HTTPException(status_code=409, detail="instance_busy") from error

    async def instance_snapshots() -> list[dict[str, object]]:
        view = await require_lifecycle().snapshot()
        visible = view["instances"]
        ids = sorted(visible)
        result = []
        for instance_id in ids:
            item = metadata.get(instance_id)
            state = visible[instance_id]
            if item is None:
                continue
            result.append(
                {
                    "instance_id": instance_id,
                    "connection_id": state["connection_id"],
                    "selector": _selector(instance_id, ids),
                    "status": item.status if item.status == "draining" else state["status"],
                    "agent_version": item.agent_version,
                    "protocol_version": 2,
                    "capabilities": sorted(item.capabilities),
                    "last_heartbeat_at": item.last_heartbeat_at,
                    "offline_expires_at": item.offline_expires_at,
                }
            )
        return result

    async def orderly_shutdown() -> None:
        await require_lifecycle().drain()
        deadline = time.monotonic() + drain_timeout
        while connections and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        for adapter in list(connections.values()):
            await enqueue(adapter, _Outbound(disconnect=True), wait=True)
        if shutdown is not None:
            shutdown()

    management = create_app(
        root,
        token=token,
        admit=admit,
        instances=instance_snapshots,
        shutdown=orderly_shutdown,
        runtime_health=healthy,
        startup=startup,
        teardown=teardown,
    )

    async def deadline_scheduler() -> None:
        interval = max(0.01, min(0.25, heartbeat_timeout / 4, offline_retention / 4))
        while True:
            await asyncio.sleep(interval)
            await require_lifecycle().expire_deadlines()

    async def effect_pump() -> None:
        while True:
            effect = await require_lifecycle().next_effect()
            _log_effect(effect)
            if effect.kind == "registration_expired":
                sid = effect.connection_id
                if sid is not None and sid in pending_sids:
                    pending_sids.discard(sid)
                    await sio.disconnect(sid)
                continue
            if effect.kind == "instance_expired":
                if effect.instance_id is not None:
                    metadata.pop(effect.instance_id, None)
                continue
            if effect.kind == "fatal":
                if shutdown is not None:
                    shutdown()
                continue
            if effect.instance_id is None or effect.connection_id is None:
                continue
            adapter = connections.get(effect.instance_id)
            if adapter is None or adapter.connection_id != effect.connection_id:
                continue
            outbound = _effect_outbound(effect, drain_timeout)
            if outbound is not None:
                await enqueue(adapter, outbound)

    async def enqueue(
        adapter: _ConnectionAdapter, outbound: _Outbound, *, wait: bool = False
    ) -> None:
        current = connections.get(adapter.instance_id)
        if current is not adapter:
            return
        if wait:
            outbound.completed = asyncio.get_running_loop().create_future()
        try:
            adapter.queue.put_nowait(outbound)
        except asyncio.QueueFull:
            await require_lifecycle().fail("outbound_queue_saturated")
            return
        if outbound.completed is not None:
            await outbound.completed

    async def sender(adapter: _ConnectionAdapter) -> None:
        try:
            while True:
                outbound = await adapter.queue.get()
                if connections.get(adapter.instance_id) is not adapter:
                    if outbound.completed is not None:
                        outbound.completed.set_result(None)
                    return
                if outbound.event is not None:
                    await sio.emit(outbound.event, outbound.data or {}, to=adapter.sid)
                if outbound.disconnect:
                    await sio.disconnect(adapter.sid)
                if outbound.completed is not None and not outbound.completed.done():
                    outbound.completed.set_result(None)
                if outbound.disconnect:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - sender is a fatal task boundary
            if not stopping:
                await require_lifecycle().fail("outbound_sender_failed")

    async def stop_sender(adapter: _ConnectionAdapter) -> None:
        if adapter.task is None:
            return
        adapter.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await adapter.task

    @sio.event
    async def connect(sid: str, environ: dict[str, object], auth: object) -> bool:
        del environ
        supplied = auth.get("token", "") if isinstance(auth, dict) else ""
        if not secrets.compare_digest(str(supplied), token):
            return False
        pending_sids.add(sid)
        await require_lifecycle().connected(sid)
        return True

    @sio.event
    async def register(sid: str, data: object) -> None:
        parsed = _registration(data)
        if parsed is None or sid not in pending_sids:
            await sio.emit("registration_error", {"code": "protocol_incompatible"}, to=sid)
            await sio.disconnect(sid)
            return
        instance_id, agent_version, capabilities = parsed
        previous = connections.get(instance_id)
        if previous is not None:
            await enqueue(previous, _Outbound(disconnect=True), wait=True)
            await stop_sender(previous)
        connection_id = str(uuid.uuid4())
        adapter = _ConnectionAdapter(
            instance_id,
            connection_id,
            sid,
            asyncio.Queue(maxsize=outbound_capacity),
        )
        connections[instance_id] = adapter
        by_sid[sid] = adapter
        pending_sids.discard(sid)
        adapter.task = asyncio.create_task(sender(adapter), name=f"sender:{connection_id}")
        metadata[instance_id] = _InstanceMetadata(
            connection_id,
            "synchronizing",
            agent_version,
            capabilities,
            _now(),
        )
        await require_lifecycle().register(
            instance_id,
            connection_id,
            set(capabilities),
            pending_connection_id=sid,
        )

    @sio.event
    async def execution_sync(sid: str, data: object) -> None:
        adapter = _current(by_sid, sid, data)
        if adapter is None or not isinstance(data, dict):
            return
        records = data.get("records")
        if (
            not isinstance(records, list)
            or len(records) > 64
            or not all(isinstance(record, dict) for record in records)
        ):
            await require_lifecycle().fail("invalid_execution_sync")
            return
        await require_lifecycle().synchronized(adapter.instance_id, adapter.connection_id, records)
        metadata[adapter.instance_id].status = "online"

    @sio.event
    async def heartbeat(sid: str, data: object) -> None:
        adapter = _current(by_sid, sid, data)
        if adapter is None:
            return
        await require_lifecycle().heartbeat(adapter.instance_id, adapter.connection_id)
        item = metadata[adapter.instance_id]
        item.last_heartbeat_at = _now()
        if isinstance(data, dict) and data.get("status") == "draining":
            item.status = "draining"
            await require_lifecycle().set_draining(adapter.instance_id, adapter.connection_id)
        await enqueue(
            adapter,
            _Outbound("heartbeat_recorded", {"connection_id": adapter.connection_id}),
        )

    @sio.event
    async def request_accepted(sid: str, data: object) -> None:
        adapter = _current(by_sid, sid, data)
        if adapter is None or not isinstance(data, dict):
            return
        request_id = str(data.get("request_id", ""))
        await require_lifecycle().accepted(adapter.instance_id, adapter.connection_id, request_id)
        await require_lifecycle().authorize(adapter.instance_id, adapter.connection_id, request_id)

    @sio.event
    async def request_rejected(sid: str, data: object) -> None:
        adapter = _current(by_sid, sid, data)
        if adapter is None or not isinstance(data, dict):
            return
        await require_lifecycle().rejected(
            adapter.instance_id,
            adapter.connection_id,
            str(data.get("request_id", "")),
            str(data.get("code", "request_rejected")),
        )

    @sio.event
    async def request_outcome(sid: str, data: object) -> None:
        adapter = _current(by_sid, sid, data)
        if adapter is None or not isinstance(data, dict):
            return
        await require_lifecycle().outcome(
            adapter.instance_id,
            adapter.connection_id,
            str(data.get("request_id", "")),
            str(data.get("execution_id", "")),
            status=str(data.get("status", "failed")),
            result=_decode_wire_value(data.get("result")),
            error=data.get("error"),
        )

    @sio.event
    async def unregister(sid: str, data: object) -> None:
        adapter = _current(by_sid, sid, data)
        if adapter is not None:
            await enqueue(adapter, _Outbound(disconnect=True))

    @sio.event
    async def disconnect(sid: str, reason: object = None) -> None:
        del reason
        pending_sids.discard(sid)
        adapter = by_sid.pop(sid, None)
        if adapter is None:
            return
        if connections.get(adapter.instance_id) is adapter:
            connections.pop(adapter.instance_id, None)
            item = metadata.get(adapter.instance_id)
            if item is not None:
                item.status = "offline"
                item.offline_expires_at = (
                    (datetime.now(UTC) + timedelta(seconds=offline_retention))
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z")
                )
            await require_lifecycle().disconnect(adapter.instance_id, adapter.connection_id)
        if adapter.task is not asyncio.current_task():
            await stop_sender(adapter)

    return socketio.ASGIApp(sio, other_asgi_app=management)


def _effect_outbound(effect: LifecycleEffect, drain_timeout: float) -> _Outbound | None:
    envelope = {
        "instance_id": effect.instance_id,
        "connection_id": effect.connection_id,
    }
    if effect.kind == "registered":
        return _Outbound("registered", {**envelope, "protocol_version": 2})
    if effect.kind == "request_dispatch":
        return _Outbound("request_dispatch", {**effect.payload, **envelope})
    if effect.kind == "request_execute":
        return _Outbound(
            "request_execute",
            {
                **envelope,
                "request_id": effect.request_id,
                "execution_id": effect.execution_id,
            },
        )
    if effect.kind in {"outcome_recorded", "record_release"}:
        return _Outbound(
            effect.kind,
            {
                **envelope,
                "request_id": effect.request_id,
                "execution_id": effect.execution_id,
            },
        )
    if effect.kind == "daemon_draining":
        return _Outbound("daemon_draining", {"deadline_seconds": drain_timeout})
    if effect.kind == "connection_expired":
        return _Outbound(disconnect=True)
    return None


def _registration(data: object) -> tuple[str, str, list[str]] | None:
    if not isinstance(data, dict):
        return None
    versions = data.get("protocol_versions")
    try:
        instance_id = str(uuid.UUID(str(data.get("instance_id"))))
    except ValueError:
        return None
    if (
        not isinstance(versions, list)
        or any(type(version) is not int for version in versions)
        or versions != [2]
        or data.get("td_build") != LOCKED_TOUCHDESIGNER_VERSION
    ):
        return None
    capabilities = data.get("capabilities")
    if not isinstance(capabilities, list) or not all(
        isinstance(capability, str) for capability in capabilities
    ):
        return None
    return instance_id, str(data.get("agent_version", "unknown")), capabilities


def _current(
    by_sid: dict[str, _ConnectionAdapter], sid: str, data: object
) -> _ConnectionAdapter | None:
    if not isinstance(data, dict):
        return None
    adapter = by_sid.get(sid)
    if adapter is None:
        return None
    if (
        data.get("instance_id") != adapter.instance_id
        or data.get("connection_id") != adapter.connection_id
    ):
        return None
    return adapter


def _log_effect(effect: LifecycleEffect) -> None:
    LOGGER.info(
        json.dumps(
            {
                "event": effect.kind,
                "instance_id": effect.instance_id,
                "connection_id": effect.connection_id,
                "request_id": effect.request_id,
                "execution_id": effect.execution_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _decode_wire_value(value: object) -> object:
    if value == {"__td_cli_null__": True}:
        return None
    if isinstance(value, dict):
        return {key: _decode_wire_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_wire_value(item) for item in value]
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _selector(instance_id: str, all_ids: list[str]) -> str:
    for length in range(4, len(instance_id) + 1):
        candidate = instance_id[:length]
        if sum(value.startswith(candidate) for value in all_ids) == 1:
            return candidate
    return instance_id
