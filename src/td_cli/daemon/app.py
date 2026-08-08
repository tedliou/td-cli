from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException

from td_cli import __version__
from td_cli.daemon.storage import RequestStore
from td_cli.protocol import Command, RequestSnapshot, StrictModel


class SubmitRequest(StrictModel):
    request_id: str
    instance_id: str
    command: Command


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def create_app(root: Path, *, token: str) -> FastAPI:
    state = root / "state"
    store: RequestStore | None = None

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal store
        store = RequestStore(state / "daemon.db")
        yield
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

    @app.post("/v1/requests", status_code=201, dependencies=[Depends(authenticate)])
    def submit(payload: SubmitRequest) -> dict[str, object]:
        assert store is not None
        snapshot = RequestSnapshot.pending(
            request_id=payload.request_id,
            instance_id=payload.instance_id,
            command=payload.command,
            submitted_at=_now(),
        ).model_dump(mode="json")
        store.insert(snapshot)
        return snapshot

    @app.get("/v1/requests/{request_id}", dependencies=[Depends(authenticate)])
    def get_request(request_id: str) -> dict[str, object]:
        assert store is not None
        snapshot = store.get(request_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="request_not_found")
        return snapshot

    return app
