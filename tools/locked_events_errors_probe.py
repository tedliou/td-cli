"""Execute DAT file for a disposable locked-runtime project; loads no Agent and no Daemon.

It creates erroring Operators, calls the real ``AgentExt._events`` handler against TouchDesigner's
root Operator, writes the observation beside this file, and quits without saving.
"""

# ruff: noqa: F821 - TouchDesigner injects its Python API names at runtime.

import hashlib
import json
import os
import runpy
import traceback
from pathlib import Path
from types import SimpleNamespace

REPOSITORY = Path(r"E:\td-cli")
SOURCE = REPOSITORY / "agent" / "extension.py"
RESULT = REPOSITORY / ".tmp-locked-runtime-events-errors.json"
BULK_ERRORS = 200


def onStart():
    run("args[0]()", _create, delayFrames=5)


def _create():
    try:
        parent = op("/project1")
        callbacks = parent.create(textDAT, "probe_callbacks")
        callbacks.text = "def onCook(scriptOp):\n    raise ValueError('unterminated\n"
        script = parent.create(scriptDAT, "probe_script")
        script.par.callbacks = callbacks
        expression = parent.create(constantCHOP, "probe_expression")
        expression.par.value0.expr = "undefined_name_xyz + 1"
        run("args[0]()", _observe, delayFrames=30)
    except Exception:  # noqa: BLE001 - persist any locked-runtime probe failure
        _finish({"stage": "create", "traceback": traceback.format_exc()})


def _events(agent_ext):
    handler = SimpleNamespace(
        events=[],
        operator_lookup=op,
        MAX_ERROR_TEXT_BYTES=agent_ext.MAX_ERROR_TEXT_BYTES,
    )
    return agent_ext._events(handler, {"after": 0, "limit": 1, "include_errors": True})


def _observe():
    try:
        agent_ext = runpy.run_path(str(SOURCE))["AgentExt"]
        for name in ("probe_script", "probe_expression"):
            try:
                op("/project1/" + name).cook(force=True)
            except Exception:  # noqa: BLE001, S110 - a failing cook is the error under test.
                pass
        raw = op("/").errors(recurse=True)
        readable = _events(agent_ext)
        bulk = op("/project1").create(baseCOMP, "probe_bulk")
        for index in range(BULK_ERRORS):
            node = bulk.create(constantCHOP, f"err{index}")
            node.par.value0.expr = f"undefined_bulk_name_{index} + 1"
            node.cook(force=True)
        bounded = _events(agent_ext)
        _finish(
            {
                "pid": os.getpid(),
                "build": str(app.build),
                "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                "raw_type": type(raw).__name__,
                "readable": {
                    "verbatim": readable["errors"] == raw,
                    "errors_truncated": readable["errors_truncated"],
                    "errors": readable["errors"],
                },
                "bounded": {
                    "raw_bytes": len(op("/").errors(recurse=True).encode("utf-8")),
                    "result_bytes": len(bounded["errors"].encode("utf-8")),
                    "errors_truncated": bounded["errors_truncated"],
                    "is_prefix": op("/").errors(recurse=True).startswith(bounded["errors"]),
                },
            }
        )
    except Exception:  # noqa: BLE001 - persist any locked-runtime probe failure
        _finish({"stage": "observe", "traceback": traceback.format_exc()})


def _finish(data):
    RESULT.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    project.quit(force=True)
