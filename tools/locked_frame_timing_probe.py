"""Execute DAT probe: frame and wall-clock timestamps of the Protocol v3 Agent handshake.

Loads the Agent artifact named by ``TDCLI_MENU_LIMIT_ARTIFACT`` like ``locked_menu_limit_probe``,
redirects it to the isolated acceptance Daemon, and wraps the Agent extension's handshake
methods so each call records ``absTime.frame`` and a precise system wall clock. ``onFrameStart``
records every frame start on the same clock, so the harness can place each SocketIO DAT
callback and each ``run()`` execution inside a frame. The probe never changes the Agent's
behaviour and quits without saving when the harness writes ``done`` or after a fixed deadline.

With ``TDCLI_FRAME_TIMING_BUILD`` set to ``source|output|revision`` the probe only builds the
Agent artifact from that source with ``agent/build_td.py`` and quits. With
``TDCLI_FRAME_TIMING_PAUSE=1`` it pauses the root timeline before connecting.
"""

# ruff: noqa: F821 - TouchDesigner injects its Python API names at runtime.

import ctypes
import json
import os
import runpy
import time
import traceback
from collections import deque
from pathlib import Path

WORK = Path(os.environ["TDCLI_MENU_LIMIT_WORK"])
ARTIFACT = Path(os.environ["TDCLI_MENU_LIMIT_ARTIFACT"])
URL = os.environ["TDCLI_MENU_LIMIT_URL"]
DEADLINE_SECONDS = 300
WRAPPED = ("reserve", "authorize", "execute_authorized", "outcome_chunks")
FRAMES = deque(maxlen=20000)
EVENTS = []


def wall_ns():
    """System time with sub-millisecond resolution, comparable with the harness process."""
    value = ctypes.c_ulonglong()
    ctypes.windll.kernel32.GetSystemTimePreciseAsFileTime(ctypes.byref(value))
    return (value.value - 116444736000000000) * 100


def onStart():
    run(
        "args[0]()", _build if os.environ.get("TDCLI_FRAME_TIMING_BUILD") else _load, delayFrames=10
    )


def onFrameStart(frame):
    del frame
    FRAMES.append((int(absTime.frame), wall_ns()))


def _request_id(arguments):
    first = arguments[0] if arguments else None
    return first.get("request_id") if isinstance(first, dict) else first


def _wrap(extension, name):
    # The SocketIO DAT callbacks reach the extension through a fresh lookup, so wrap the class.
    cls = type(extension)
    original = getattr(cls, name)

    def wrapped(self, *arguments, **keywords):
        start = wall_ns()
        frame = int(absTime.frame)
        try:
            return original(self, *arguments, **keywords)
        finally:
            EVENTS.append(
                {
                    "event": name,
                    "request_id": _request_id(arguments),
                    "frame": frame,
                    "start_ns": start,
                    "end_ns": wall_ns(),
                }
            )

    setattr(cls, name, wrapped)


def _facts(agent):
    return {
        "pid": os.getpid(),
        "build": str(app.build),
        "agent_version": agent.ext.Agent.agent_version,
        "connection_state": str(agent.par.Connectionstate.eval()),
        "socket_active": bool(agent.op("socketio1").par.active.eval()),
        "cook_rate": float(project.cookRate),
        "real_time": bool(project.realTime),
        "timeline_paused": not bool(root.time.play),
    }


def _write(name, data):
    (WORK / name).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _build():
    try:
        source, output, revision = os.environ["TDCLI_FRAME_TIMING_BUILD"].split("|")
        builder = runpy.run_path(str(Path(source) / "build_td.py"), init_globals=globals())
        evidence = builder["build"](source, output, revision)
        op("/project1/td_agent").destroy()  # the build copy; the probe run loads the saved artifact
        _write("build.json", evidence)
    except Exception:  # noqa: BLE001 - report any build failure, then quit the disposable TD
        _write("td-error.json", {"error": traceback.format_exc()})
    project.quit(force=True)


def _load():
    try:
        if os.environ.get("TDCLI_FRAME_TIMING_PAUSE") == "1":
            root.time.play = False
        agent = op("/project1").loadTox(str(ARTIFACT))
        before = _facts(agent)
        if before["socket_active"] or before["connection_state"] != "waiting_for_daemon":
            raise RuntimeError(f"Agent left the waiting state before redirection: {before}")
        for name in WRAPPED:
            _wrap(agent.ext.Agent, name)
        agent.op("socketio1").par.url = URL
        _write("ready.json", {"before_redirect": before, "clock_ns": wall_ns()})
        _poll(agent, time.monotonic())
    except Exception:  # noqa: BLE001 - report any probe failure, then quit the disposable TD
        _write("td-error.json", {"error": traceback.format_exc()})
        project.quit(force=True)


def _poll(agent, started):
    if (WORK / "done").exists() or time.monotonic() - started > DEADLINE_SECONDS:
        first = min((event["start_ns"] for event in EVENTS), default=0) - 1_000_000_000
        _write(
            "td-final.json",
            {
                **_facts(agent),
                "deadline_hit": not (WORK / "done").exists(),
                "events": EVENTS,
                "frames": [frame for frame in FRAMES if frame[1] >= first],
            },
        )
        project.quit(force=True)
        return
    run("args[0](args[1], args[2])", _poll, agent, started, delayFrames=30)
