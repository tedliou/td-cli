from __future__ import annotations

import asyncio
import codecs
import contextlib
import inspect
import secrets
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError, field_validator, model_validator

from td_cli import __version__
from td_cli.daemon.storage import RequestIdentityConflict, RequestStore
from td_cli.error_catalog import ERROR_CATALOG
from td_cli.instance_selection import SelectionError, select_instance
from td_cli.protocol import (
    MAX_PLAN_BYTES,
    MAX_REQUEST_WAIT_MS,
    PROTOCOL_VERSIONS,
    Command,
    CommandPlan,
    RequestSnapshot,
    StrictModel,
)
from td_cli.validation_details import validation_details


class SubmitRequest(StrictModel):
    request_id: str
    instance_id: str | None = None
    instance_selector: str | None = None
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
    def instance_id_is_uuid(cls, value: str | None) -> str | None:
        return None if value is None else str(uuid.UUID(value))

    @model_validator(mode="after")
    def one_instance_reference(self) -> SubmitRequest:
        if self.instance_id is not None and self.instance_selector is not None:
            raise ValueError("pass instance_id or instance_selector, not both")
        return self


def _invalid_arguments(error: ValidationError | None = None) -> JSONResponse:
    """Answer like an HTTP error, adding the Command validation details when there are any."""
    content: dict[str, object] = {"detail": "invalid_arguments"}
    if error is not None:
        content["details"] = validation_details(error)
    return JSONResponse(status_code=422, content=content)


def _selection_failed(error: SelectionError) -> HTTPException:
    return HTTPException(
        status_code=404 if error.code == "instance_not_found" else 409, detail=error.code
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def create_app(
    root: Path,
    *,
    token: str,
    admit: Callable[[dict[str, object]], Awaitable[tuple[dict[str, object], bool]]] | None = None,
    instances: Callable[[], list[dict[str, object]] | Awaitable[list[dict[str, object]]]]
    | None = None,
    shutdown: Callable[[], Awaitable[None] | None] | None = None,
    runtime_health: Callable[[], bool] | None = None,
    startup: Callable[[RequestStore], Awaitable[None]] | None = None,
    teardown: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    state = root / "state"
    store: RequestStore | None = None
    admission_tasks: set[asyncio.Task[tuple[dict[str, object], bool]]] = set()

    def admission_done(task: asyncio.Task[tuple[dict[str, object], bool]]) -> None:
        admission_tasks.discard(task)
        with contextlib.suppress(asyncio.CancelledError):
            task.exception()

    async def durable_admission(
        operation: Awaitable[tuple[dict[str, object], bool]],
    ) -> tuple[dict[str, object], bool]:
        async def invoke() -> tuple[dict[str, object], bool]:
            return await operation

        task: asyncio.Task[tuple[dict[str, object], bool]] = asyncio.create_task(
            invoke(), name="request-admission"
        )
        admission_tasks.add(task)
        task.add_done_callback(admission_done)
        return await asyncio.shield(task)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal store
        store = await RequestStore.open(state / "daemon.db")
        try:
            if startup is None:
                await _recover_requests(store)
            else:
                await startup(store)
            app.state.request_store = store
            cleanup_task = asyncio.create_task(_hourly_cleanup(store))
            try:
                yield
            finally:
                cleanup_task.cancel()
                try:
                    if teardown is not None:
                        await teardown()
                finally:
                    if admission_tasks:
                        await asyncio.gather(*admission_tasks, return_exceptions=True)
                    with contextlib.suppress(asyncio.CancelledError):
                        await cleanup_task
        finally:
            await store.close()

    app = FastAPI(lifespan=lifespan)

    def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        supplied = authorization.removeprefix("Bearer ") if authorization else ""
        if not secrets.compare_digest(supplied, token):
            raise HTTPException(status_code=404, detail="Not Found")

    @app.get("/v3/health", dependencies=[Depends(authenticate)])
    def health() -> dict[str, object]:
        logging_healthy = runtime_health() if runtime_health is not None else True
        return {
            "ready": logging_healthy,
            "logging_healthy": logging_healthy,
            "release_version": __version__,
            "protocol_versions": list(PROTOCOL_VERSIONS),
            "schema_version": 2,
        }

    @app.get("/v3/instances", dependencies=[Depends(authenticate)])
    async def list_instances() -> list[dict[str, object]]:
        if instances is None:
            return []
        result = instances()
        return await result if inspect.isawaitable(result) else result

    @app.get("/v3/instances/resolve", dependencies=[Depends(authenticate)])
    async def resolve_instance(
        selector: str | None = None, online_only: bool = False
    ) -> dict[str, object]:
        try:
            return select_instance(await list_instances(), selector, online_only=online_only)
        except SelectionError as error:
            raise _selection_failed(error) from error

    @app.post("/v3/command-plans/validate", dependencies=[Depends(authenticate)])
    async def validate_plan(request: Request) -> object:
        """Check a whole ``commands execute`` plan before its first Command is sent."""
        document = await request.body()
        if len(document) > MAX_PLAN_BYTES or document.startswith(codecs.BOM_UTF8):
            return _invalid_arguments()
        try:
            plan = CommandPlan.model_validate_json(document)
        except ValidationError as error:
            return _invalid_arguments(error)
        return plan.model_dump(mode="json")

    @app.post("/v3/shutdown", status_code=202, dependencies=[Depends(authenticate)])
    async def request_shutdown() -> dict[str, bool]:
        if shutdown is not None:
            result = shutdown()
            if inspect.isawaitable(result):
                await result
        return {"draining": True}

    @app.post("/v3/requests", status_code=201, dependencies=[Depends(authenticate)])
    async def submit(request: Request, response: Response) -> object:
        """Validate the Command, then the submission, then resolve the Instance, then admit."""
        assert store is not None
        try:
            body = await request.json()
        except ValueError:
            return _invalid_arguments()
        if not isinstance(body, dict):
            return _invalid_arguments()
        try:
            command = Command.model_validate(body.get("command"))
        except ValidationError as error:
            return _invalid_arguments(error)
        try:
            payload = SubmitRequest.model_validate({**body, "command": command})
        except ValidationError:
            return _invalid_arguments()
        instance_id = payload.instance_id
        if instance_id is None:
            try:
                selected = select_instance(
                    await list_instances(), payload.instance_selector, online_only=True
                )
            except SelectionError as error:
                raise _selection_failed(error) from error
            instance_id = str(selected["instance_id"])
        snapshot = RequestSnapshot.pending(
            request_id=payload.request_id,
            instance_id=instance_id,
            command=payload.command,
            submitted_at=_now(),
        ).model_dump(mode="json")
        try:
            if admit is None:
                persisted, created = await durable_admission(store.create_or_get(snapshot))
            else:
                persisted, created = await durable_admission(admit(snapshot))
        except RequestIdentityConflict as error:
            raise HTTPException(status_code=409, detail="request_id_conflict") from error
        if not created:
            response.status_code = 200
            return persisted
        return persisted

    @app.get("/v3/requests/{request_id}", dependencies=[Depends(authenticate)])
    async def get_request(
        request_id: str,
        wait_ms: Annotated[int, Query(ge=0, le=MAX_REQUEST_WAIT_MS)] = 0,
    ) -> dict[str, object]:
        """Answer at once, or once the Request settles when ``wait_ms`` asks for a long poll."""
        assert store is not None
        snapshot = (
            await store.wait_until_terminal(request_id, timeout=wait_ms / 1000)
            if wait_ms
            else await store.get(request_id)
        )
        if snapshot is None:
            raise HTTPException(status_code=404, detail="request_not_found")
        return snapshot

    return app


async def _hourly_cleanup(store: RequestStore) -> None:
    while True:
        while await store.cleanup() == 1000:
            await asyncio.sleep(0)
        await asyncio.sleep(3600)


async def _recover_requests(store: RequestStore) -> None:
    policies = (
        ({"queued", "dispatched", "accepted"}, "daemon_shutdown", "daemon_shutdown"),
        ({"running"}, "unknown", "request_outcome_unknown"),
    )
    for statuses, target, code in policies:
        for snapshot in await store.find_by_statuses(statuses):
            await store.compare_and_set(
                str(snapshot["request_id"]),
                expected_statuses={str(snapshot["status"])},
                changes={
                    "status": target,
                    "error": ERROR_CATALOG.error(code),
                    "completed_at": _now(),
                },
            )
