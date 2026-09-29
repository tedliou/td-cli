"""Execute DAT probe for issue 151 in a disposable project.

Loads the Agent artifact named by ``TDCLI_MENU_LIMIT_ARTIFACT`` while the scratch
``LOCALAPPDATA`` has no auth token, so the Agent waits and never reaches the user's
Daemon. It then points the SocketIO DAT at the isolated acceptance Daemon, reports
readiness, and quits without saving when the harness writes ``done`` or after a
fixed deadline. The harness drives every Command through the Daemon transport.
"""

# ruff: noqa: F821 - TouchDesigner injects its Python API names at runtime.

import json
import os
import time
import traceback
from pathlib import Path

WORK = Path(os.environ["TDCLI_MENU_LIMIT_WORK"])
ARTIFACT = Path(os.environ["TDCLI_MENU_LIMIT_ARTIFACT"])
URL = os.environ["TDCLI_MENU_LIMIT_URL"]
DEADLINE_SECONDS = 300


def onStart():
    run("args[0]()", _load, delayFrames=10)


def _facts(agent):
    return {
        "pid": os.getpid(),
        "build": str(app.build),
        "localappdata": os.environ.get("LOCALAPPDATA"),
        "agent_path": agent.path,
        "agent_version": agent.ext.Agent.agent_version,
        "connection_state": str(agent.par.Connectionstate.eval()),
        "socket_active": bool(agent.op("socketio1").par.active.eval()),
        "socket_url": str(agent.op("socketio1").par.url.eval()),
    }


def _write(name, data):
    (WORK / name).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _load():
    try:
        agent = op("/project1").loadTox(str(ARTIFACT))
        before = _facts(agent)
        if before["socket_active"] or before["connection_state"] != "waiting_for_daemon":
            raise RuntimeError(f"Agent left the waiting state before redirection: {before}")
        agent.op("socketio1").par.url = URL
        _write("ready.json", {"before_redirect": before, "after_redirect": _facts(agent)})
        _poll(agent, time.monotonic())
    except Exception:  # noqa: BLE001 - report any probe failure, then quit the disposable TD
        _write("td-error.json", {"error": traceback.format_exc()})
        project.quit(force=True)


def _poll(agent, started):
    if (WORK / "done").exists() or time.monotonic() - started > DEADLINE_SECONDS:
        _write("td-final.json", {**_facts(agent), "deadline_hit": not (WORK / "done").exists()})
        project.quit(force=True)
        return
    run("args[0](args[1], args[2])", _poll, agent, started, delayFrames=30)
