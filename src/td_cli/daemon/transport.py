from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import socketio
from fastapi import HTTPException

from td_cli.daemon.app import SubmitRequest, create_app


@dataclass
class Registration:
    instance_id: str
    connection_id: str
    sid: str | None
    status: str
    agent_version: str
    capabilities: list[str]
    last_heartbeat_at: float
    offline_expires_at: float | None = None


def create_transport_app(
    root: Path,
    *,
    token: str,
    heartbeat_timeout: float = 6,
    offline_retention: float = 30,
    shutdown: Callable[[], None] | None = None,
) -> socketio.ASGIApp:
    """Create the combined authenticated HTTP and Socket.IO Protocol v1 interface."""
    sio = socketio.AsyncServer(
        async_mode="asgi", cors_allowed_origins=[], max_http_buffer_size=256 * 1024
    )
    registrations: dict[str, Registration] = {}
    queues: dict[str, deque[dict[str, object]]] = defaultdict(deque)
    in_flight: dict[str, dict[str, object]] = {}
    management = None

    async def dispatch(snapshot: dict[str, object]) -> None:
        instance_id = str(snapshot["instance_id"])
        queues[instance_id].append(snapshot)
        await dispatch_next(instance_id)

    async def preflight(payload: SubmitRequest) -> None:
        instance_id = payload.instance_id
        registration = registrations.get(instance_id)
        if registration is not None and registration.status == "draining":
            raise HTTPException(status_code=409, detail="instance_draining")
        if registration is None or registration.status != "online":
            raise HTTPException(status_code=409, detail="instance_offline")
        if len(queues[instance_id]) + int(instance_id in in_flight) >= 32:
            raise HTTPException(status_code=409, detail="instance_busy")

    async def dispatch_next(instance_id: str) -> None:
        registration = registrations.get(instance_id)
        if (
            registration is None
            or registration.status != "online"
            or instance_id in in_flight
            or not queues[instance_id]
        ):
            return
        snapshot = queues[instance_id].popleft()
        in_flight[instance_id] = snapshot
        management.state.request_store.update(
            str(snapshot["request_id"]), status="dispatched", dispatched_at=_now()
        )
        await sio.emit("request_dispatch", snapshot, to=registration.sid)

    def instance_snapshots() -> list[dict[str, object]]:
        ids = sorted(registrations)
        return [
            {
                "instance_id": item.instance_id,
                "connection_id": item.connection_id,
                "selector": _selector(item.instance_id, ids),
                "status": item.status,
                "agent_version": item.agent_version,
                "protocol_version": 1,
                "capabilities": sorted(item.capabilities),
            }
            for item in (registrations[instance_id] for instance_id in ids)
        ]

    management = create_app(
        root,
        token=token,
        preflight=preflight,
        dispatch=dispatch,
        instances=instance_snapshots,
        shutdown=shutdown,
    )

    @sio.event
    async def connect(sid: str, environ: dict[str, object], auth: object) -> bool:
        del environ
        supplied = auth.get("token", "") if isinstance(auth, dict) else ""
        return secrets.compare_digest(str(supplied), token)

    @sio.event
    async def register(sid: str, data: object) -> None:
        if not isinstance(data, dict):
            await sio.disconnect(sid)
            return
        versions = data.get("protocol_versions")
        instance_id = data.get("instance_id")
        if not isinstance(instance_id, str) or not isinstance(versions, list) or 1 not in versions:
            await sio.emit("registration_error", {"code": "protocol_incompatible"}, to=sid)
            await sio.disconnect(sid)
            return
        connection_id = str(uuid.uuid4())
        previous = registrations.get(instance_id)
        registrations[instance_id] = Registration(
            instance_id=instance_id,
            connection_id=connection_id,
            sid=sid,
            status="online",
            agent_version=str(data.get("agent_version", "unknown")),
            capabilities=[str(value) for value in data.get("capabilities", [])],
            last_heartbeat_at=time.monotonic(),
        )
        await sio.emit(
            "registered",
            {"instance_id": instance_id, "connection_id": connection_id, "protocol_version": 1},
            to=sid,
        )
        if previous is not None and previous.sid is not None and previous.sid != sid:
            await sio.disconnect(previous.sid)
        await dispatch_next(instance_id)
        sio.start_background_task(monitor_heartbeat, instance_id, connection_id)

    @sio.event
    async def heartbeat(sid: str, data: object) -> None:
        if not isinstance(data, dict):
            return
        registration = registrations.get(str(data.get("instance_id", "")))
        if (
            registration
            and registration.sid == sid
            and registration.connection_id == data.get("connection_id")
        ):
            registration.last_heartbeat_at = time.monotonic()
            if data.get("status") == "draining":
                registration.status = "draining"
            await sio.emit(
                "heartbeat_recorded", {"connection_id": registration.connection_id}, to=sid
            )

    @sio.event
    async def disconnect(sid: str, reason: object = None) -> None:
        del reason
        for instance_id, registration in list(registrations.items()):
            if registration.sid != sid:
                continue
            registration.sid = None
            registration.status = "offline"
            registration.offline_expires_at = time.monotonic() + offline_retention
            current = in_flight.pop(instance_id, None)
            if current is not None:
                management.state.request_store.update(
                    str(current["request_id"]),
                    status="unknown",
                    error={
                        "code": "request_outcome_unknown",
                        "message": "request_outcome_unknown",
                        "details": {},
                        "retryable": False,
                    },
                    completed_at=_now(),
                )
            sio.start_background_task(expire_offline, instance_id, registration.connection_id)
            break

    async def expire_offline(instance_id: str, connection_id: str) -> None:
        await asyncio.sleep(offline_retention)
        registration = registrations.get(instance_id)
        if registration is None or registration.connection_id != connection_id:
            return
        if registration.status == "offline":
            registrations.pop(instance_id, None)
            while queues[instance_id]:
                queued = queues[instance_id].popleft()
                management.state.request_store.update(
                    str(queued["request_id"]),
                    status="instance_offline",
                    error={
                        "code": "instance_offline",
                        "message": "instance_offline",
                        "details": {},
                        "retryable": False,
                    },
                    completed_at=_now(),
                )

    async def monitor_heartbeat(instance_id: str, connection_id: str) -> None:
        while True:
            await asyncio.sleep(heartbeat_timeout)
            registration = registrations.get(instance_id)
            if registration is None or registration.connection_id != connection_id:
                return
            if registration.status != "online" or registration.sid is None:
                return
            if time.monotonic() - registration.last_heartbeat_at >= heartbeat_timeout:
                await sio.disconnect(registration.sid)
                return

    def current_registration(sid: str, data: dict[str, object]) -> Registration | None:
        registration = registrations.get(str(data.get("instance_id", "")))
        if registration is None:
            return None
        if registration.sid != sid or registration.connection_id != data.get("connection_id"):
            return None
        return registration

    @sio.event
    async def request_accepted(sid: str, data: object) -> None:
        if not isinstance(data, dict) or current_registration(sid, data) is None:
            return
        store = management.state.request_store
        store.update(str(data.get("request_id")), status="running", started_at=_now())

    @sio.event
    async def request_result(sid: str, data: object) -> None:
        if not isinstance(data, dict) or current_registration(sid, data) is None:
            return
        request_id = str(data.get("request_id", ""))
        store = management.state.request_store
        snapshot = store.update(
            request_id,
            status="succeeded",
            result=data.get("result"),
            error=None,
            completed_at=_now(),
        )
        if snapshot is not None:
            await sio.emit("result_recorded", {"request_id": request_id}, to=sid)
            in_flight.pop(str(data["instance_id"]), None)
            await dispatch_next(str(data["instance_id"]))

    @sio.event
    async def request_rejected(sid: str, data: object) -> None:
        if not isinstance(data, dict) or current_registration(sid, data) is None:
            return
        request_id = str(data.get("request_id", ""))
        instance_id = str(data["instance_id"])
        snapshot = management.state.request_store.update(
            request_id,
            status="failed",
            result=None,
            error={
                "code": str(data.get("code", "request_rejected")),
                "message": str(data.get("code", "request_rejected")),
                "details": {},
                "retryable": data.get("code") == "result_buffer_full",
            },
            completed_at=_now(),
        )
        if snapshot is not None:
            in_flight.pop(instance_id, None)
            await dispatch_next(instance_id)

    return socketio.ASGIApp(sio, other_asgi_app=management)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _selector(instance_id: str, all_ids: list[str]) -> str:
    for length in range(4, len(instance_id) + 1):
        candidate = instance_id[:length]
        if sum(value.startswith(candidate) for value in all_ids) == 1:
            return candidate
    return instance_id
