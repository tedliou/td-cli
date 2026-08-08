from __future__ import annotations

import secrets
import uuid
from collections import defaultdict, deque
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
    sid: str


def create_transport_app(root: Path, *, token: str) -> socketio.ASGIApp:
    """Create the combined authenticated HTTP and Socket.IO Protocol v1 interface."""
    sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins=[])
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
        if instance_id not in registrations:
            raise HTTPException(status_code=409, detail="instance_offline")
        if len(queues[instance_id]) + int(instance_id in in_flight) >= 32:
            raise HTTPException(status_code=409, detail="instance_busy")

    async def dispatch_next(instance_id: str) -> None:
        registration = registrations.get(instance_id)
        if registration is None or instance_id in in_flight or not queues[instance_id]:
            return
        snapshot = queues[instance_id].popleft()
        in_flight[instance_id] = snapshot
        management.state.request_store.update(
            str(snapshot["request_id"]), status="dispatched", dispatched_at=_now()
        )
        await sio.emit("request_dispatch", snapshot, to=registration.sid)

    management = create_app(root, token=token, preflight=preflight, dispatch=dispatch)

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
        registrations[instance_id] = Registration(instance_id, connection_id, sid)
        await sio.emit(
            "registered",
            {"instance_id": instance_id, "connection_id": connection_id, "protocol_version": 1},
            to=sid,
        )
        if previous is not None and previous.sid != sid:
            await sio.disconnect(previous.sid)

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
            await sio.emit(
                "heartbeat_recorded", {"connection_id": registration.connection_id}, to=sid
            )

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

    return socketio.ASGIApp(sio, other_asgi_app=management)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
