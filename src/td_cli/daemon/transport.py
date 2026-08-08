from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path

import socketio

from td_cli.daemon.app import create_app


@dataclass
class Registration:
    instance_id: str
    connection_id: str
    sid: str


def create_transport_app(root: Path, *, token: str) -> socketio.ASGIApp:
    """Create the combined authenticated HTTP and Socket.IO Protocol v1 interface."""
    sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins=[])
    registrations: dict[str, Registration] = {}

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

    return socketio.ASGIApp(sio, other_asgi_app=create_app(root, token=token))
