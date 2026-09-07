"""One-shot disposable-project probe; never load this into an artwork."""

# ruff: noqa: F821 - locked TouchDesigner injects runtime objects.
import hashlib
import json
import runpy
import time
import traceback
from pathlib import Path

REPO = Path(r"E:\td-cli")
RESULT = REPO / ".tmp-current-project-save.json"


def onStart():
    me.par.start = False
    run(probe, delayMilliSeconds=500, delayRef=op.TDResources)


def probe():
    try:
        source = REPO / "agent"
        revision = runpy.run_path(str(REPO / "tools/runtime_acceptance_common.py"))[
            "source_revision"
        ](source)
        builder = runpy.run_path(
            str(source / "build_td.py"),
            init_globals={key: value for key, value in globals().items() if key != "args"},
        )
        artifact = builder["build"](str(source), str(REPO / "td-agent.tox"), revision)
        op("/project1/td_agent").destroy()
        component = op("/project1").loadByteArray(
            bytearray((REPO / "td-agent.tox").read_bytes()), unwired=True, pattern=None
        )
        path = Path(project.folder) / project.name
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        started = time.perf_counter()
        saved = component.ext.Agent.execute_command(
            {
                "name": "project.save",
                "input": {"expected_path": str(path), "expected_sha256": expected},
            }
        )
        RESULT.write_text(
            json.dumps(
                {
                    "build": str(app.build),
                    "artifact": artifact,
                    "result": saved,
                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                    "disk_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "timeline_playing": bool(root.time.play),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 - preserve exact locked-runtime failure evidence.
        RESULT.write_text(json.dumps({"error": traceback.format_exc()}), encoding="utf-8")
