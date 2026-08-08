from __future__ import annotations

import asyncio
import inspect
import secrets
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import field_validator

from td_cli import __version__
from td_cli.daemon.storage import RequestStore
from td_cli.protocol import Command, RequestSnapshot, StrictModel


class SubmitRequest(StrictModel):
    request_id: str
    instance_id: str
    command: Command

    @field_validator("request_id")
    @classmethod
    def request_id_is_uuid7(cls, value: str) -> str:
        parsed = uuid.UUID(value)
        if parsed.version != 7:
            raise ValueError("request_id must be UUIDv7")
        return str(parsed)

    @field_validator("instance_id")
    @classmethod
    def instance_id_is_uuid(cls, value: str) -> str:
        return str(uuid.UUID(value))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def create_app(
    root: Path,
    *,
    token: str,
    preflight: Callable[[SubmitRequest], Awaitable[None]] | None = None,
    dispatch: Callable[[dict[str, object]], Awaitable[None]] | None = None,
    instances: Callable[[], list[dict[str, object]]] | None = None,
    shutdown: Callable[[], Awaitable[None] | None] | None = None,
) -> FastAPI:
    state = root / "state"
    store: RequestStore | None = None

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal store
        store = RequestStore(state / "daemon.db")
        app.state.request_store = store
        cleanup_task = asyncio.create_task(_hourly_cleanup(store))
        try:
            yield
        finally:
            cleanup_task.cancel()
            store.close()

    app = FastAPI(lifespan=lifespan)

    def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        supplied = authorization.removeprefix("Bearer ") if authorization else ""
        if not secrets.compare_digest(supplied, token):
            raise HTTPException(status_code=404, detail="Not Found")

    @app.get("/v1/health", dependencies=[Depends(authenticate)])
    def health() -> dict[str, object]:
        return {
            "ready": True,
            "release_version": __version__,
            "protocol_versions": [1],
            "schema_version": 1,
        }

    @app.get("/v1/instances", dependencies=[Depends(authenticate)])
    def list_instances() -> list[dict[str, object]]:
        return instances() if instances is not None else []

    @app.post("/v1/shutdown", status_code=202, dependencies=[Depends(authenticate)])
    async def request_shutdown() -> dict[str, bool]:
        if shutdown is not None:
            result = shutdown()
            if inspect.isawaitable(result):
                await result
        return {"draining": True}

    @app.post("/v1/requests", status_code=201, dependencies=[Depends(authenticate)])
    async def submit(payload: SubmitRequest, response: Response) -> dict[str, object]:
        assert store is not None
        existing = store.get(payload.request_id)
        if existing is not None:
            existing_command = Command.model_validate(existing["command"])
            if existing_command.canonical_json() != payload.command.canonical_json():
                raise HTTPException(status_code=409, detail="request_id_conflict")
            response.status_code = 200
            return existing
        if preflight is not None:
            await preflight(payload)
        snapshot = RequestSnapshot.pending(
            request_id=payload.request_id,
            instance_id=payload.instance_id,
            command=payload.command,
            submitted_at=_now(),
        ).model_dump(mode="json")
        store.insert(snapshot)
        if dispatch is not None:
            await dispatch(snapshot)
        return snapshot

    @app.get("/v1/requests/{request_id}", dependencies=[Depends(authenticate)])
    def get_request(request_id: str) -> dict[str, object]:
        assert store is not None
        snapshot = store.get(request_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="request_not_found")
        return snapshot

    return app


async def _hourly_cleanup(store: RequestStore) -> None:
    while True:
        while store.cleanup() == 1000:
            await asyncio.sleep(0)
        await asyncio.sleep(3600)
