"""Install a session-only localhost execution bridge in the current TD project."""

import builtins
import importlib.util
import json
import os
import secrets
import sys
from pathlib import Path

BRIDGE_PATH = "/project1/td_diagnostic_bridge"
BRIDGE_PORT = 9983
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CORE_PATH = REPOSITORY_ROOT / "tools" / "td_diagnostic_bridge" / "core.py"
SERVER_PATH = REPOSITORY_ROOT / "tools" / "td_diagnostic_bridge" / "server.py"
CONFIG_PATH = Path(os.environ["LOCALAPPDATA"]) / "touchdesigner-cli" / "diagnostic-bridge.json"

EXECUTE_SOURCE = r"""
import builtins
from pathlib import Path


def onFrameStart(frame):
    bridge_app = builtins._td_diagnostic_bridge_app
    if bridge_app.consume_shutdown_request():
        builtins._td_diagnostic_bridge_server.shutdown()
        Path(builtins._td_diagnostic_bridge_config_path).unlink(missing_ok=True)
        run("op('/project1/td_diagnostic_bridge').destroy()", endFrame=True)
        return
    bridge_app.execute_next(dict(globals()))
    return
"""


def _load_module(module_name, path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load diagnostic bridge module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def install():
    if str(app.build) != "2025.32050":  # type: ignore[name-defined]
        raise RuntimeError(f"TouchDesigner 2025.32050 required; got {app.build}")  # type: ignore[name-defined]
    project = op("/project1")  # type: ignore[name-defined]
    existing = op(BRIDGE_PATH)  # type: ignore[name-defined]
    if existing:
        existing.destroy()

    core = _load_module("td_diagnostic_bridge_core", CORE_PATH)
    server_module = _load_module("td_diagnostic_bridge_server", SERVER_PATH)
    token = secrets.token_urlsafe(32)
    bridge_app = core.BridgeApplication(token=token, touchdesigner_build=str(app.build))  # type: ignore[name-defined]
    builtins._td_diagnostic_bridge_app = bridge_app
    builtins._td_diagnostic_bridge_config_path = str(CONFIG_PATH)

    bridge = project.create(baseCOMP, "td_diagnostic_bridge")  # type: ignore[name-defined]
    executor = bridge.create(executeDAT, "job_executor")  # type: ignore[name-defined]
    executor.text = EXECUTE_SOURCE
    executor.par.start = True
    executor.par.framestart = True
    server = server_module.start_server(bridge_app, port=BRIDGE_PORT)
    builtins._td_diagnostic_bridge_server = server

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(
            {
                "base_url": server.base_url,
                "token": token,
                "bridge_path": BRIDGE_PATH,
                "touchdesigner_build": str(app.build),  # type: ignore[name-defined]
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    print(f"TD_DIAGNOSTIC_BRIDGE_READY {server.base_url}")
    return bridge


if "app" in globals() and "op" in globals():
    install()
