from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

import socketio
from fastapi import HTTPException

from td_cli.command_catalog import OPERATOR_STATE_BOOLEAN_FIELDS
from td_cli.daemon.app import SubmitRequest, create_app
from td_cli.release import LOCKED_TOUCHDESIGNER_VERSION


@dataclass
class Registration:
    instance_id: str
    connection_id: str
    sid: str | None
    status: InstanceStatus
    agent_version: str
    capabilities: list[str]
    last_heartbeat_monotonic: float
    last_heartbeat_at: str
    offline_expires_at: str | None = None


class InstanceStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    DRAINING = "draining"


def create_transport_app(
    root: Path,
    *,
    token: str,
    heartbeat_timeout: float = 6,
    offline_retention: float = 30,
    shutdown: Callable[[], None] | None = None,
    drain_timeout: float = 5,
    runtime_health: Callable[[], bool] | None = None,
) -> socketio.ASGIApp:
    """Create the combined authenticated HTTP and Socket.IO Protocol v1 interface."""
    sio = socketio.AsyncServer(
        async_mode="asgi", cors_allowed_origins=[], max_http_buffer_size=256 * 1024
    )
    registrations: dict[str, Registration] = {}
    connected: set[str] = set()
    queues: dict[str, deque[dict[str, object]]] = defaultdict(deque)
    in_flight: dict[str, dict[str, object]] = {}
    shutting_down = False
    management = None

    async def dispatch(snapshot: dict[str, object]) -> None:
        instance_id = str(snapshot["instance_id"])
        queues[instance_id].append(snapshot)
        await dispatch_next(instance_id)

    async def preflight(payload: SubmitRequest) -> None:
        if shutting_down:
            raise HTTPException(status_code=503, detail="daemon_shutdown")
        instance_id = payload.instance_id
        registration = registrations.get(instance_id)
        if registration is not None and registration.status == InstanceStatus.DRAINING:
            raise HTTPException(status_code=409, detail="instance_draining")
        if registration is None or registration.status != InstanceStatus.ONLINE:
            raise HTTPException(status_code=409, detail="instance_offline")
        if payload.command.name not in registration.capabilities:
            raise HTTPException(status_code=409, detail="command_unsupported")
        if len(queues[instance_id]) + int(instance_id in in_flight) >= 32:
            raise HTTPException(status_code=409, detail="instance_busy")

    async def dispatch_next(instance_id: str) -> None:
        registration = registrations.get(instance_id)
        if (
            registration is None
            or registration.status != InstanceStatus.ONLINE
            or instance_id in in_flight
            or not queues[instance_id]
        ):
            return
        snapshot = queues[instance_id].popleft()
        in_flight[instance_id] = snapshot
        assert management is not None
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
                "last_heartbeat_at": item.last_heartbeat_at,
                "offline_expires_at": item.offline_expires_at,
            }
            for item in (registrations[instance_id] for instance_id in ids)
        ]

    async def orderly_shutdown() -> None:
        nonlocal shutting_down
        shutting_down = True
        for registration in registrations.values():
            if registration.status == InstanceStatus.ONLINE:
                registration.status = InstanceStatus.DRAINING
                if registration.sid is not None:
                    await sio.emit(
                        "daemon_draining", {"deadline_seconds": drain_timeout}, to=registration.sid
                    )
        deadline = time.monotonic() + drain_timeout
        while (
            in_flight
            or any(registration.sid is not None for registration in registrations.values())
        ) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        for instance_id, queue in queues.items():
            while queue:
                terminal_request(queue.popleft(), "daemon_shutdown", "daemon_shutdown")
        for instance_id, snapshot in list(in_flight.items()):
            terminal_request(snapshot, "unknown", "request_outcome_unknown")
            in_flight.pop(instance_id, None)
        if shutdown is not None:
            shutdown()

    management = create_app(
        root,
        token=token,
        preflight=preflight,
        dispatch=dispatch,
        instances=instance_snapshots,
        shutdown=orderly_shutdown,
        runtime_health=runtime_health,
    )

    @sio.event
    async def connect(sid: str, environ: dict[str, object], auth: object) -> bool:
        del environ
        supplied = auth.get("token", "") if isinstance(auth, dict) else ""
        authenticated = secrets.compare_digest(str(supplied), token)
        if authenticated:
            connected.add(sid)
            sio.start_background_task(enforce_registration_deadline, sid)
        return authenticated

    @sio.event
    async def register(sid: str, data: object) -> None:
        if not isinstance(data, dict):
            await sio.disconnect(sid)
            return
        versions = data.get("protocol_versions")
        instance_id = data.get("instance_id")
        try:
            normalized_instance_id = str(uuid.UUID(str(instance_id)))
        except ValueError:
            normalized_instance_id = ""
        valid_versions = (
            isinstance(versions, list)
            and all(type(version) is int for version in versions)
            and 1 in versions
        )
        if (
            not normalized_instance_id
            or not valid_versions
            or data.get("td_build") != LOCKED_TOUCHDESIGNER_VERSION
        ):
            await sio.emit("registration_error", {"code": "protocol_incompatible"}, to=sid)
            await sio.disconnect(sid)
            return
        instance_id = normalized_instance_id
        connection_id = str(uuid.uuid4())
        previous = registrations.get(instance_id)
        registrations[instance_id] = Registration(
            instance_id=instance_id,
            connection_id=connection_id,
            sid=sid,
            status=(
                InstanceStatus.DRAINING
                if shutting_down or data.get("status") == "draining"
                else InstanceStatus.ONLINE
            ),
            agent_version=str(data.get("agent_version", "unknown")),
            capabilities=[str(value) for value in data.get("capabilities", [])],
            last_heartbeat_monotonic=time.monotonic(),
            last_heartbeat_at=_now(),
        )
        connected.discard(sid)
        await sio.emit(
            "registered",
            {"instance_id": instance_id, "connection_id": connection_id, "protocol_version": 1},
            to=sid,
        )
        if shutting_down:
            await sio.emit("daemon_draining", {"deadline_seconds": drain_timeout}, to=sid)
        if previous is not None and previous.sid is not None and previous.sid != sid:
            await sio.disconnect(previous.sid)
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
            registration.last_heartbeat_monotonic = time.monotonic()
            registration.last_heartbeat_at = _now()
            if data.get("status") == "draining":
                registration.status = InstanceStatus.DRAINING
            await sio.emit(
                "heartbeat_recorded", {"connection_id": registration.connection_id}, to=sid
            )

    @sio.event
    async def disconnect(sid: str, reason: object = None) -> None:
        del reason
        connected.discard(sid)
        for instance_id, registration in list(registrations.items()):
            if registration.sid != sid:
                continue
            registration.sid = None
            registration.status = InstanceStatus.OFFLINE
            registration.offline_expires_at = (
                (datetime.now(UTC) + timedelta(seconds=offline_retention))
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
            current = in_flight.pop(instance_id, None)
            if current is not None:
                terminal_request(current, "unknown", "request_outcome_unknown")
            sio.start_background_task(expire_offline, instance_id, registration.connection_id)
            break

    async def expire_offline(instance_id: str, connection_id: str) -> None:
        await asyncio.sleep(offline_retention)
        registration = registrations.get(instance_id)
        if registration is None or registration.connection_id != connection_id:
            return
        if registration.status == InstanceStatus.OFFLINE:
            registrations.pop(instance_id, None)
            while queues[instance_id]:
                terminal_request(
                    queues[instance_id].popleft(), "instance_offline", "instance_offline"
                )

    async def monitor_heartbeat(instance_id: str, connection_id: str) -> None:
        while True:
            await asyncio.sleep(heartbeat_timeout)
            registration = registrations.get(instance_id)
            if registration is None or registration.connection_id != connection_id:
                return
            if registration.status != InstanceStatus.ONLINE or registration.sid is None:
                return
            if time.monotonic() - registration.last_heartbeat_monotonic >= heartbeat_timeout:
                await sio.disconnect(registration.sid)
                return

    async def enforce_registration_deadline(sid: str) -> None:
        await asyncio.sleep(5)
        if sid in connected:
            await sio.disconnect(sid)

    @sio.event
    async def results_replayed(sid: str, data: object) -> None:
        if not isinstance(data, dict) or current_registration(sid, data) is None:
            return
        await dispatch_next(str(data["instance_id"]))

    @sio.event
    async def unregister(sid: str, data: object) -> None:
        if not isinstance(data, dict) or current_registration(sid, data) is None:
            return
        registrations.pop(str(data["instance_id"]), None)
        await sio.disconnect(sid)

    def terminal_request(snapshot: dict[str, object], status: str, code: str) -> None:
        management.state.request_store.update(
            str(snapshot["request_id"]),
            status=status,
            error={"code": code, "message": code, "details": {}, "retryable": False},
            completed_at=_now(),
        )

    async def advance(instance_id: str) -> None:
        in_flight.pop(instance_id, None)
        await dispatch_next(instance_id)

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
        instance_id = str(data["instance_id"])
        current = in_flight.get(instance_id)
        if current is None or current["request_id"] != data.get("request_id"):
            return
        store = management.state.request_store
        store.update(str(data.get("request_id")), status="running", started_at=_now())

    @sio.event
    async def request_result(sid: str, data: object) -> None:
        if not isinstance(data, dict) or current_registration(sid, data) is None:
            return
        request_id = str(data.get("request_id", ""))
        store = management.state.request_store
        existing = store.get(request_id)
        if (
            existing is None
            or existing["instance_id"] != data["instance_id"]
            or existing["status"] not in {"dispatched", "running", "unknown"}
        ):
            return
        result = _normalize_command_result(existing.get("command"), data.get("result"))
        snapshot = store.update(
            request_id,
            status="succeeded",
            result=result,
            error=None,
            completed_at=_now(),
        )
        if snapshot is not None:
            await sio.emit("result_recorded", {"request_id": request_id}, to=sid)
            await advance(str(data["instance_id"]))

    @sio.event
    async def request_rejected(sid: str, data: object) -> None:
        if not isinstance(data, dict) or current_registration(sid, data) is None:
            return
        request_id = str(data.get("request_id", ""))
        instance_id = str(data["instance_id"])
        current = in_flight.get(instance_id)
        if current is None or current["request_id"] != request_id:
            return
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
            await advance(instance_id)

    return socketio.ASGIApp(sio, other_asgi_app=management)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalize_command_result(command: object, result: object) -> object:
    """Restore public nullable fields omitted by locked SocketIO DAT transport."""
    result = _decode_wire_value(result)
    if not isinstance(command, dict) or not isinstance(result, dict):
        return result
    normalized = dict(result)
    name = command.get("name")
    if name == "batch.execute":
        command_input = command.get("input")
        nested_commands = command_input.get("commands") if isinstance(command_input, dict) else None
        nested_results = normalized.get("results")
        if isinstance(nested_commands, list) and isinstance(nested_results, list):
            normalized["results"] = [
                _normalize_command_result(nested_command, nested_result)
                for nested_command, nested_result in zip(
                    nested_commands, nested_results, strict=False
                )
            ]
    elif name in {"ops.connect", "ops.hierarchy.connect"}:
        normalized.setdefault("previous_connection", None)
    elif name in {"ops.connections", "ops.hierarchy.connections"} and isinstance(
        normalized.get("inputs"), list
    ):
        normalized["inputs"] = [
            {**item, "connection": item.get("connection")} if isinstance(item, dict) else item
            for item in normalized["inputs"]
        ]
    elif name == "ops.copy" and "include_docked" in normalized:
        normalized["include_docked"] = bool(normalized["include_docked"])
    elif name in {"ops.state.get", "ops.state.set"} and isinstance(normalized.get("state"), dict):
        state = dict(normalized["state"])
        for field in OPERATOR_STATE_BOOLEAN_FIELDS:
            if field in state:
                state[field] = bool(state[field])
        normalized["state"] = state
    elif name == "parameters.list" and isinstance(normalized.get("parameters"), list):
        parameters = []
        for item in normalized["parameters"]:
            if not isinstance(item, dict):
                parameters.append(item)
                continue
            descriptor = dict(item)
            descriptor.setdefault("page", None)
            descriptor.setdefault("unsupported_reason", None)
            descriptor.setdefault("sequence", None)
            descriptor.setdefault("source", None)
            descriptor.setdefault("bounds", None)
            descriptor.setdefault("max_operator_paths", None)
            expression = descriptor.get("expression")
            if isinstance(expression, dict):
                descriptor["expression"] = {**expression, "source": expression.get("source")}
            if descriptor.get("value_kind") == "menu":
                descriptor.setdefault("menu_names", [])
                descriptor.setdefault("menu_labels", [])
            else:
                descriptor.setdefault("menu_names", None)
                descriptor.setdefault("menu_labels", None)
            parameters.append(descriptor)
        normalized["parameters"] = parameters
    elif name in {"parameters.get", "parameters.set"}:
        normalized.setdefault("source", None)
        normalized.setdefault("unsupported_reason", None)
        if normalized.get("value_type") in {"operator", "python", "sequence", "unknown"}:
            normalized.setdefault("value", None)
    elif name in {"parameters.sequence.get", "parameters.sequence.replace"}:
        normalized.setdefault("max_blocks", None)
        for block in normalized.get("blocks", []):
            if not isinstance(block, dict):
                continue
            block.setdefault("name", None)
            for parameter in block.get("parameters", []):
                if isinstance(parameter, dict):
                    parameter.setdefault("value", None)
    return normalized


def _decode_wire_value(value: object) -> object:
    if value == {"__td_cli_null__": True}:
        return None
    if isinstance(value, dict):
        return {key: _decode_wire_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_wire_value(item) for item in value]
    return value


def _selector(instance_id: str, all_ids: list[str]) -> str:
    for length in range(4, len(instance_id) + 1):
        candidate = instance_id[:length]
        if sum(value.startswith(candidate) for value in all_ids) == 1:
            return candidate
    return instance_id
