"""Verify the Agent artifact's official TouchDesigner extension bootstrap."""

# ruff: noqa: F821 - TouchDesigner injects its Python API names at runtime.

import json
import time
import traceback
from pathlib import Path

REPOSITORY = Path(r"E:\td-cli")
SOURCE = REPOSITORY / "agent"
ARTIFACT = REPOSITORY / "td-agent.tox"
RESULT = REPOSITORY / ".tmp-locked-runtime-bootstrap.json"
source_revision = __import__("runpy").run_path(
    str(REPOSITORY / "tools" / "runtime_acceptance_common.py")
)["source_revision"]


def _table_has_content(table):
    return any(
        str(table[row, column])
        for row in range(int(table.numRows))
        for column in range(int(table.numCols))
    )


def _fail(error):
    RESULT.write_text(
        json.dumps(
            {
                "error": type(error).__name__,
                "message": str(error),
                "traceback": traceback.format_exc(),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _build_and_load():
    try:
        builder = __import__("runpy").run_path(str(SOURCE / "build_td.py"), init_globals=globals())
        artifact = builder["build"](str(SOURCE), str(ARTIFACT), source_revision(SOURCE))
        built = op("/project1/td_agent")
        built_auth = built.op("auth_table")
        saved_state = {
            "auth_has_content": _table_has_content(built_auth),
            "auth_rows": int(built_auth.numRows),
            "socket_active": bool(built.op("socketio1").par.active),
            "execute_create": bool(built.op("heartbeat_execute").par.create),
            "execute_start": bool(built.op("heartbeat_execute").par.start),
            "init_extensions_on_start": bool(built.par.initextonstart),
        }
        built.destroy()
        op("/project1").loadByteArray(bytearray(ARTIFACT.read_bytes()), unwired=True, pattern=None)
        run(
            "args[0](args[1], args[2])",
            _check_loaded,
            artifact,
            saved_state,
            delayMilliSeconds=2500,
            delayRef=op.TDResources,
        )
    except Exception as error:  # noqa: BLE001 - preserve exact locked-runtime evidence
        _fail(error)


def _check_loaded(artifact, saved_state):
    try:
        loaded = op("/project1/td_agent")
        socket_dat = loaded.op("socketio1")
        auth_table = loaded.op("auth_table")
        extension = loaded.ext.Agent
        evidence = {
            "artifact": artifact,
            "saved_state": saved_state,
            "loaded_state": {
                "auth_rows_after_open": int(auth_table.numRows),
                "auth_has_content_after_open": _table_has_content(auth_table),
                "connection_id": extension.connection_id,
                "heartbeat_generation_present": extension._heartbeat_generation is not None,
                "last_heartbeat_monotonic": extension.last_heartbeat_at,
                "extensions_ready": bool(loaded.extensionsReady),
                "runtime_active": bool(extension.runtime_active),
                "socket_active": bool(socket_dat.par.active),
            },
            "timeline_paused": not bool(root.time.play),
            "touchdesigner_build": str(app.build),
            "operator_errors": [str(value) for value in root.errors(recurse=True)],
            "checked_at_unix": time.time(),
        }
        run(
            "args[0](args[1])",
            _finish_heartbeat_check,
            evidence,
            delayMilliSeconds=5000,
            delayRef=op.TDResources,
        )
    except Exception as error:  # noqa: BLE001 - preserve exact locked-runtime evidence
        _fail(error)


def _finish_heartbeat_check(evidence):
    try:
        extension = op("/project1/td_agent").ext.Agent
        evidence["loaded_state"]["last_heartbeat_monotonic_after_5s"] = extension.last_heartbeat_at
        evidence["operator_errors_after_5s"] = [str(value) for value in root.errors(recurse=True)]
        evidence["finished_at_unix"] = time.time()
        RESULT.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    except Exception as error:  # noqa: BLE001 - preserve exact locked-runtime evidence
        _fail(error)


def start():
    if RESULT.exists():
        RESULT.unlink()
    root.time.play = False
    run(
        "args[0]()",
        _build_and_load,
        delayMilliSeconds=500,
        delayRef=op.TDResources,
    )
