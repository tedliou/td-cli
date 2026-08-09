from __future__ import annotations

import json
import secrets
import sys
import time
import uuid
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError
from typer._click.exceptions import ClickException, UsageError

from td_cli import __version__
from td_cli.client import ClientError, DaemonClient
from td_cli.protocol import Command

app = typer.Typer(no_args_is_help=True)
instances_app = typer.Typer()
requests_app = typer.Typer()
ops_app = typer.Typer()
parameters_app = typer.Typer()
project_app = typer.Typer()
binary_app = typer.Typer()
batch_app = typer.Typer()
events_app = typer.Typer()
app.add_typer(instances_app, name="instances")
app.add_typer(requests_app, name="requests")
app.add_typer(ops_app, name="ops")
app.add_typer(parameters_app, name="parameters")
app.add_typer(project_app, name="project")
app.add_typer(binary_app, name="binary")
app.add_typer(batch_app, name="batch")
app.add_typer(events_app, name="events")


def _print_td_version(value: bool) -> None:
    if value:
        typer.echo(f"td {__version__} (protocol 1)")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool | None, typer.Option("--version", callback=_print_td_version, is_eager=True)
    ] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
    timeout: Annotated[float, typer.Option("--timeout", min=0.1, max=3600)] = 30.0,
    instance: Annotated[str | None, typer.Option("--instance")] = None,
) -> None:
    """Control supported TouchDesigner Instances through the local Daemon."""
    ctx.obj = {"json": as_json, "timeout": timeout, "instance": instance}


def _client(ctx: typer.Context) -> DaemonClient:
    return DaemonClient(timeout=float(ctx.obj["timeout"]))


def _reject_instance_on_query(ctx: typer.Context) -> None:
    if ctx.obj["instance"] is not None:
        raise ClientError("invalid_arguments")


def _emit(ctx: typer.Context, data: object, *, request: dict[str, Any] | None = None) -> None:
    if ctx.obj["json"]:
        envelope: dict[str, object] = {"protocol_version": 1, "data": data}
        if request is not None:
            envelope["request"] = {"request_id": request["request_id"], "status": request["status"]}
        typer.echo(json.dumps(envelope, separators=(",", ":"), ensure_ascii=False))
    else:
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False))


def _fail(ctx: typer.Context, error: ClientError) -> None:
    exits = {
        "invalid_arguments": 2,
        "daemon_unavailable": 3,
        "transport_error": 3,
        "instance_not_found": 4,
        "instance_selector_ambiguous": 4,
        "instance_offline": 4,
        "instance_draining": 4,
        "wait_timeout": 6,
    }
    code = exits.get(error.code, 5)
    if ctx.obj["json"]:
        envelope: dict[str, object] = {
            "protocol_version": 1,
            "error": {
                "code": error.code,
                "message": error.code,
                "details": error.details,
                "retryable": error.code
                in {
                    "daemon_unavailable",
                    "transport_error",
                    "instance_busy",
                    "result_buffer_full",
                    "wait_timeout",
                },
            },
        }
        snapshot = error.details.get("request")
        if isinstance(snapshot, dict):
            envelope["request"] = {
                "request_id": snapshot["request_id"],
                "status": snapshot["status"],
            }
        typer.echo(json.dumps(envelope, separators=(",", ":")), err=False)
    typer.echo(error.code, err=True)
    raise typer.Exit(code)


def _run(ctx: typer.Context, operation) -> None:
    try:
        operation()
    except ClientError as error:
        _fail(ctx, error)


@instances_app.command("list")
def instances_list(
    ctx: typer.Context, status: Annotated[str | None, typer.Option("--status")] = None
) -> None:
    def operation() -> None:
        _reject_instance_on_query(ctx)
        items = _client(ctx).instances()
        if status is not None:
            if status not in {"online", "offline", "draining"}:
                raise ClientError("invalid_arguments")
            items = [item for item in items if item["status"] == status]
        _emit(ctx, items)

    _run(ctx, operation)


@instances_app.command("get")
def instances_get(
    ctx: typer.Context, selector: Annotated[str | None, typer.Argument()] = None
) -> None:
    def operation() -> None:
        _reject_instance_on_query(ctx)
        _emit(ctx, _client(ctx).select_instance(selector, online_only=False))

    _run(ctx, operation)


@requests_app.command("get")
def requests_get(ctx: typer.Context, request_id: str) -> None:
    def operation() -> None:
        _reject_instance_on_query(ctx)
        _emit(ctx, _client(ctx).get_request(request_id))

    _run(ctx, operation)


@requests_app.command("wait")
def requests_wait(ctx: typer.Context, request_id: str) -> None:
    def operation() -> None:
        _reject_instance_on_query(ctx)
        snapshot = _client(ctx).wait(request_id)
        if snapshot["status"] != "succeeded":
            error = snapshot.get("error") or {"code": "internal_error"}
            raise ClientError(str(error["code"]), details={"request": snapshot})
        _emit(ctx, snapshot)

    _run(ctx, operation)


@app.command("version")
def version_info(
    ctx: typer.Context,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    def operation() -> None:
        _reject_instance_on_query(ctx)
        daemon_version = None
        try:
            daemon_version = _client(ctx).health()["release_version"]
        except ClientError as error:
            if error.code not in {"daemon_unavailable", "transport_error"}:
                raise
        data = {
            "release_version": __version__,
            "protocol_versions": [1],
            "daemon_release_version": daemon_version,
        }
        original = ctx.obj["json"]
        ctx.obj["json"] = original or as_json
        try:
            _emit(ctx, data)
        finally:
            ctx.obj["json"] = original

    _run(ctx, operation)


def _uuid7() -> str:
    milliseconds = int(time.time() * 1000)
    value = (milliseconds << 80) | (0x7 << 76) | (secrets.randbits(12) << 64)
    value |= (0b10 << 62) | secrets.randbits(62)
    return str(uuid.UUID(int=value))


def _json_document(text: str) -> dict[str, Any]:
    if not text or text.startswith("\ufeff"):
        raise ClientError("invalid_arguments")
    decoder = json.JSONDecoder()
    try:
        value, end = decoder.raw_decode(text)
    except json.JSONDecodeError as error:
        raise ClientError("invalid_arguments") from error
    if text[end:].strip() or not isinstance(value, dict):
        raise ClientError("invalid_arguments")
    return value


def _input(
    dedicated: dict[str, Any] | None, inline: str | None, input_file: str | None
) -> dict[str, Any]:
    modes = sum(value is not None for value in (dedicated, inline, input_file))
    if modes != 1:
        raise ClientError("invalid_arguments")
    if dedicated is not None:
        return dedicated
    if inline is not None:
        return _json_document(inline)
    assert input_file is not None
    try:
        text = (
            sys.stdin.read() if input_file == "-" else Path(input_file).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError) as error:
        raise ClientError("invalid_arguments") from error
    return _json_document(text)


def _submit(
    ctx: typer.Context, name: str, payload: dict[str, Any], no_wait: bool, request_id: str | None
) -> None:
    try:
        command = Command.model_validate({"name": name, "input": payload}).model_dump(mode="json")
    except ValidationError as error:
        raise ClientError("invalid_arguments") from error
    client = _client(ctx)
    instance = client.select_instance(ctx.obj["instance"])
    snapshot = client.submit(request_id or _uuid7(), instance["instance_id"], command)
    if not no_wait:
        snapshot = client.wait(snapshot["request_id"])
    if snapshot["status"] != "succeeded" and not no_wait:
        terminal_error = snapshot.get("error") or {"code": "internal_error"}
        raise ClientError(str(terminal_error["code"]), details={"request": snapshot})
    _emit(ctx, snapshot if no_wait else snapshot["result"], request=snapshot)


def _command(
    ctx: typer.Context,
    name: str,
    dedicated: dict[str, Any] | None,
    inline: str | None,
    input_file: str | None,
    no_wait: bool,
    request_id: str | None,
) -> None:
    _run(
        ctx, lambda: _submit(ctx, name, _input(dedicated, inline, input_file), no_wait, request_id)
    )


@ops_app.command("get")
def ops_get(
    ctx: typer.Context,
    operator_path: Annotated[str | None, typer.Argument()] = None,
    input: Annotated[str | None, typer.Option("--input")] = None,
    input_file: Annotated[str | None, typer.Option("--input-file")] = None,
    no_wait: Annotated[bool, typer.Option("--no-wait")] = False,
    request_id: Annotated[str | None, typer.Option("--request-id")] = None,
) -> None:
    _command(
        ctx,
        "ops.get",
        {"operator_path": operator_path} if operator_path is not None else None,
        input,
        input_file,
        no_wait,
        request_id,
    )


@ops_app.command("children")
def ops_children(
    ctx: typer.Context,
    operator_path: Annotated[str | None, typer.Argument()] = None,
    op_type: Annotated[str | None, typer.Option("--op-type")] = None,
    input: Annotated[str | None, typer.Option("--input")] = None,
    input_file: Annotated[str | None, typer.Option("--input-file")] = None,
    no_wait: Annotated[bool, typer.Option("--no-wait")] = False,
    request_id: Annotated[str | None, typer.Option("--request-id")] = None,
) -> None:
    if op_type is not None and operator_path is None:
        _fail(ctx, ClientError("invalid_arguments"))
    dedicated = (
        {"operator_path": operator_path, "op_type": op_type} if operator_path is not None else None
    )
    _command(ctx, "ops.children", dedicated, input, input_file, no_wait, request_id)


@ops_app.command("create")
def ops_create(
    ctx: typer.Context,
    parent_path: Annotated[str | None, typer.Argument()] = None,
    op_type: Annotated[str | None, typer.Argument()] = None,
    name: Annotated[str | None, typer.Argument()] = None,
    node_x: Annotated[int | None, typer.Option("--node-x")] = None,
    node_y: Annotated[int | None, typer.Option("--node-y")] = None,
    allow_conditional: Annotated[bool, typer.Option("--allow-conditional")] = False,
    input: Annotated[str | None, typer.Option("--input")] = None,
    input_file: Annotated[str | None, typer.Option("--input-file")] = None,
    no_wait: Annotated[bool, typer.Option("--no-wait")] = False,
    request_id: Annotated[str | None, typer.Option("--request-id")] = None,
) -> None:
    identity = (parent_path, op_type, name)
    if any(value is not None for value in identity) and not all(
        value is not None for value in identity
    ):
        _fail(ctx, ClientError("invalid_arguments"))
    if (node_x is not None or node_y is not None or allow_conditional) and parent_path is None:
        _fail(ctx, ClientError("invalid_arguments"))
    dedicated = None
    if parent_path is not None and op_type is not None and name is not None:
        dedicated = {
            "parent_path": parent_path,
            "op_type": op_type,
            "name": name,
            "node_x": node_x or 0,
            "node_y": node_y or 0,
            "allow_conditional": allow_conditional,
        }
    _command(ctx, "ops.create", dedicated, input, input_file, no_wait, request_id)


@ops_app.command("connect")
def ops_connect(
    ctx: typer.Context,
    source_path: Annotated[str | None, typer.Argument()] = None,
    target_path: Annotated[str | None, typer.Argument()] = None,
    output_index: Annotated[int | None, typer.Option("--output-index")] = None,
    input_index: Annotated[int | None, typer.Option("--input-index")] = None,
    replace: Annotated[bool, typer.Option("--replace")] = False,
    input: Annotated[str | None, typer.Option("--input")] = None,
    input_file: Annotated[str | None, typer.Option("--input-file")] = None,
    no_wait: Annotated[bool, typer.Option("--no-wait")] = False,
    request_id: Annotated[str | None, typer.Option("--request-id")] = None,
) -> None:
    if (source_path is None) != (target_path is None):
        _fail(ctx, ClientError("invalid_arguments"))
    if (output_index is not None or input_index is not None or replace) and source_path is None:
        _fail(ctx, ClientError("invalid_arguments"))
    dedicated = None
    if source_path is not None and target_path is not None:
        dedicated = {
            "source_path": source_path,
            "target_path": target_path,
            "output_index": output_index or 0,
            "input_index": input_index or 0,
            "replace": replace,
        }
    _command(ctx, "ops.connect", dedicated, input, input_file, no_wait, request_id)


@ops_app.command("rename")
def ops_rename(
    ctx: typer.Context,
    operator_path: Annotated[str | None, typer.Argument()] = None,
    new_name: Annotated[str | None, typer.Argument()] = None,
    input: Annotated[str | None, typer.Option("--input")] = None,
    input_file: Annotated[str | None, typer.Option("--input-file")] = None,
    no_wait: Annotated[bool, typer.Option("--no-wait")] = False,
    request_id: Annotated[str | None, typer.Option("--request-id")] = None,
) -> None:
    if (operator_path is None) != (new_name is None):
        _fail(ctx, ClientError("invalid_arguments"))
    dedicated = (
        {"operator_path": operator_path, "new_name": new_name}
        if operator_path is not None and new_name is not None
        else None
    )
    _command(ctx, "ops.rename", dedicated, input, input_file, no_wait, request_id)


@ops_app.command("disconnect")
def ops_disconnect(
    ctx: typer.Context,
    source_path: Annotated[str | None, typer.Argument()] = None,
    target_path: Annotated[str | None, typer.Argument()] = None,
    output_index: Annotated[int | None, typer.Option("--output-index")] = None,
    input_index: Annotated[int | None, typer.Option("--input-index")] = None,
    input: Annotated[str | None, typer.Option("--input")] = None,
    input_file: Annotated[str | None, typer.Option("--input-file")] = None,
    no_wait: Annotated[bool, typer.Option("--no-wait")] = False,
    request_id: Annotated[str | None, typer.Option("--request-id")] = None,
) -> None:
    if (source_path is None) != (target_path is None):
        _fail(ctx, ClientError("invalid_arguments"))
    if (output_index is not None or input_index is not None) and source_path is None:
        _fail(ctx, ClientError("invalid_arguments"))
    dedicated = None
    if source_path is not None and target_path is not None:
        dedicated = {
            "source_path": source_path,
            "target_path": target_path,
            "output_index": output_index or 0,
            "input_index": input_index or 0,
        }
    _command(ctx, "ops.disconnect", dedicated, input, input_file, no_wait, request_id)


@parameters_app.command("get")
def parameters_get(
    ctx: typer.Context,
    operator_path: Annotated[str | None, typer.Argument()] = None,
    parameter: Annotated[str | None, typer.Argument()] = None,
    input: Annotated[str | None, typer.Option("--input")] = None,
    input_file: Annotated[str | None, typer.Option("--input-file")] = None,
    no_wait: Annotated[bool, typer.Option("--no-wait")] = False,
    request_id: Annotated[str | None, typer.Option("--request-id")] = None,
) -> None:
    if (operator_path is None) != (parameter is None):
        _fail(ctx, ClientError("invalid_arguments"))
    dedicated = (
        {"operator_path": operator_path, "parameter": parameter}
        if operator_path is not None and parameter is not None
        else None
    )
    _command(ctx, "parameters.get", dedicated, input, input_file, no_wait, request_id)


@parameters_app.command("list")
def parameters_list(
    ctx: typer.Context,
    operator_path: Annotated[str | None, typer.Argument()] = None,
    input: Annotated[str | None, typer.Option("--input")] = None,
    input_file: Annotated[str | None, typer.Option("--input-file")] = None,
    no_wait: Annotated[bool, typer.Option("--no-wait")] = False,
    request_id: Annotated[str | None, typer.Option("--request-id")] = None,
) -> None:
    _command(
        ctx,
        "parameters.list",
        {"operator_path": operator_path} if operator_path is not None else None,
        input,
        input_file,
        no_wait,
        request_id,
    )


@parameters_app.command("pulse")
def parameters_pulse(
    ctx: typer.Context,
    operator_path: Annotated[str | None, typer.Argument()] = None,
    parameter: Annotated[str | None, typer.Argument()] = None,
    input: Annotated[str | None, typer.Option("--input")] = None,
    input_file: Annotated[str | None, typer.Option("--input-file")] = None,
    no_wait: Annotated[bool, typer.Option("--no-wait")] = False,
    request_id: Annotated[str | None, typer.Option("--request-id")] = None,
) -> None:
    if (operator_path is None) != (parameter is None):
        _fail(ctx, ClientError("invalid_arguments"))
    dedicated = (
        {"operator_path": operator_path, "parameter": parameter}
        if operator_path is not None and parameter is not None
        else None
    )
    _command(ctx, "parameters.pulse", dedicated, input, input_file, no_wait, request_id)


@parameters_app.command("set")
def parameters_set(
    ctx: typer.Context,
    operator_path: Annotated[str | None, typer.Argument()] = None,
    parameter: Annotated[str | None, typer.Argument()] = None,
    bool_value: Annotated[str | None, typer.Option("--bool")] = None,
    integer: Annotated[int | None, typer.Option("--integer")] = None,
    number: Annotated[float | None, typer.Option("--number")] = None,
    string: Annotated[str | None, typer.Option("--string")] = None,
    expression: Annotated[str | None, typer.Option("--expression")] = None,
    input: Annotated[str | None, typer.Option("--input")] = None,
    input_file: Annotated[str | None, typer.Option("--input-file")] = None,
    no_wait: Annotated[bool, typer.Option("--no-wait")] = False,
    request_id: Annotated[str | None, typer.Option("--request-id")] = None,
) -> None:
    any_value_option = any(
        value is not None for value in (bool_value, integer, number, string, expression)
    )
    any_dedicated = operator_path is not None or parameter is not None or any_value_option
    values = [("constant", value) for value in (integer, number, string) if value is not None]
    if bool_value is not None:
        if bool_value not in {"true", "false"}:
            _fail(ctx, ClientError("invalid_arguments"))
        values.append(("constant", bool_value == "true"))
    if expression is not None:
        values.append(("expression", expression))
    if any_dedicated and (operator_path is None or parameter is None or len(values) != 1):
        _fail(ctx, ClientError("invalid_arguments"))
    dedicated = None
    if operator_path is not None and parameter is not None and len(values) == 1:
        mode, value = values[0]
        dedicated = {
            "operator_path": operator_path,
            "parameter": parameter,
            "mode": mode,
            "value": value,
        }
    _command(ctx, "parameters.set", dedicated, input, input_file, no_wait, request_id)


@project_app.command("metadata")
def project_metadata(
    ctx: typer.Context, no_wait: bool = False, request_id: str | None = None
) -> None:
    _command(ctx, "project.metadata", {}, None, None, no_wait, request_id)


@project_app.command("snapshot")
def project_snapshot(
    ctx: typer.Context,
    operator_path: Annotated[str | None, typer.Argument()] = None,
    max_depth: Annotated[int, typer.Option("--max-depth")] = 4,
    max_operators: Annotated[int, typer.Option("--max-operators")] = 256,
    input: Annotated[str | None, typer.Option("--input")] = None,
    input_file: Annotated[str | None, typer.Option("--input-file")] = None,
    no_wait: bool = False,
    request_id: str | None = None,
) -> None:
    dedicated = (
        {"operator_path": operator_path, "max_depth": max_depth, "max_operators": max_operators}
        if operator_path is not None
        else None
    )
    _command(ctx, "project.snapshot", dedicated, input, input_file, no_wait, request_id)


@binary_app.command("export")
def binary_export(
    ctx: typer.Context,
    operator_path: Annotated[str | None, typer.Argument()] = None,
    format: Annotated[str | None, typer.Option("--format")] = None,
    max_bytes: Annotated[int, typer.Option("--max-bytes")] = 194_560,
    input: Annotated[str | None, typer.Option("--input")] = None,
    input_file: Annotated[str | None, typer.Option("--input-file")] = None,
    no_wait: bool = False,
    request_id: str | None = None,
) -> None:
    dedicated = (
        {"operator_path": operator_path, "format": format, "max_bytes": max_bytes}
        if operator_path is not None and format is not None
        else None
    )
    _command(ctx, "binary.export", dedicated, input, input_file, no_wait, request_id)


@batch_app.command("execute")
def batch_execute(
    ctx: typer.Context,
    input: Annotated[str | None, typer.Option("--input")] = None,
    input_file: Annotated[str | None, typer.Option("--input-file")] = None,
    no_wait: bool = False,
    request_id: str | None = None,
) -> None:
    _command(ctx, "batch.execute", None, input, input_file, no_wait, request_id)


@events_app.command("read")
def events_read(
    ctx: typer.Context,
    after: int = 0,
    limit: int = 100,
    include_errors: bool = True,
    no_wait: bool = False,
    request_id: str | None = None,
) -> None:
    _command(
        ctx,
        "events.read",
        {"after": after, "limit": limit, "include_errors": include_errors},
        None,
        None,
        no_wait,
        request_id,
    )


def run() -> None:
    """Run the CLI while preserving Protocol v1 JSON for parser failures."""
    try:
        exit_code = app(standalone_mode=False)
        if isinstance(exit_code, int) and exit_code:
            raise SystemExit(exit_code)
    except UsageError as error:
        if "--json" in sys.argv[1:]:
            typer.echo(
                json.dumps(
                    {
                        "protocol_version": 1,
                        "error": {
                            "code": "invalid_arguments",
                            "message": "invalid_arguments",
                            "details": {},
                            "retryable": False,
                        },
                    },
                    separators=(",", ":"),
                )
            )
            typer.echo(error.format_message(), err=True)
        else:
            error.show()
        raise SystemExit(2) from None
    except ClickException as error:
        error.show()
        raise SystemExit(error.exit_code) from None


if __name__ == "__main__":
    run()
