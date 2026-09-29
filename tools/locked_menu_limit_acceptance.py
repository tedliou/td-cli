"""Locked acceptance for issue 151 through the real Daemon transport and SocketIO DAT.

Runs this checkout's Daemon transport on an isolated port with a scratch data root, launches
a disposable TouchDesigner project (see ``locked_menu_limit_probe.py``) with a scratch
``LOCALAPPDATA``, and drives ``parameters.menu.set`` at the new limits through the public
DaemonClient. The user's Daemon (port 9982), data root, and projects are never touched.
Every wait is bounded; the TouchDesigner process started here is the only one stopped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn

from td_cli.cli import _uuid7
from td_cli.client import DaemonClient
from td_cli.command_catalog import MAX_MENU_JSON_BYTES
from td_cli.daemon.runtime_files import load_or_create_token, secure_layout
from td_cli.daemon.transport import create_transport_app
from td_cli.protocol import Command

PORT = 19982
TOUCHDESIGNER = Path(r"C:\Program Files\Derivative\TouchDesigner\bin\TouchDesigner.exe")
COMP = "/project1/menu_limit"


def _wait_for(predicate, seconds: float, what: str) -> Any:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.5)
    raise TimeoutError(f"timed out waiting for {what}")


def _json_bytes(names: list[str], labels: list[str]) -> int:
    return len(json.dumps(names + labels, ensure_ascii=True))


def _at_budget(label: str) -> dict[str, list[str]]:
    names = [f"D{i:02}" for i in range(1, 257)]
    labels = [label] * 256
    shortfall = MAX_MENU_JSON_BYTES - _json_bytes(names, labels)
    if shortfall < 0:
        raise ValueError("label too large for the budget")
    names = [
        name + "x" * (shortfall // 256 + (index < shortfall % 256))
        for index, name in enumerate(names)
    ]
    if _json_bytes(names, labels) != MAX_MENU_JSON_BYTES:
        raise AssertionError("budget construction failed")
    return {"menu_names": names, "menu_labels": labels}


class Harness:
    def __init__(self, client: DaemonClient, instance_id: str) -> None:
        self.client = client
        self.instance_id = instance_id
        self.requests: list[dict[str, Any]] = []

    def run(self, label: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        command = Command.model_validate({"name": name, "input": payload}).model_dump(mode="json")
        request_id = _uuid7()
        started = time.monotonic()
        self.client.submit(request_id, self.instance_id, command)
        snapshot = self.client.wait(request_id)
        self.requests.append(
            {
                "step": label,
                "command": name,
                "request_id": request_id,
                "status": snapshot["status"],
                "error": snapshot.get("error"),
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
                "dispatch_command_bytes": len(
                    json.dumps(command, separators=(",", ":"), sort_keys=True)
                ),
                "result_bytes": len(
                    json.dumps(snapshot.get("result"), separators=(",", ":"), sort_keys=True)
                ),
            }
        )
        if snapshot["status"] != "succeeded":
            raise RuntimeError(f"{label} ended {snapshot['status']}: {snapshot.get('error')}")
        return snapshot["result"]

    def menu_set(self, label: str, menu: dict[str, list[str]], preserve: str) -> dict[str, Any]:
        payload = {"operator_path": COMP, "parameter": "Scene", **menu, "preserve": preserve}
        result = self.run(label, "parameters.menu.set", payload)
        value = self.run(
            f"{label} value", "parameters.get", {"operator_path": COMP, "parameter": "Scene"}
        )
        listing = self.run(f"{label} options", "parameters.list", {"operator_path": COMP})
        [scene] = [item for item in listing["parameters"] if item["name"] == "Scene"]
        return {
            "item_count": len(menu["menu_names"]),
            "menu_json_bytes": _json_bytes(menu["menu_names"], menu["menu_labels"]),
            "before": result["before"],
            "after": result["after"],
            "result_matches_input": result["menu_names"] == menu["menu_names"]
            and result["menu_labels"] == menu["menu_labels"],
            "listed_options_match_input": scene["menu_names"] == menu["menu_names"]
            and scene["menu_labels"] == menu["menu_labels"],
            "value": value["value"],
            "value_matches_after": value["value"] == result["after"]["value"],
        }


def _cli_rejection(argv: list[str], env: dict[str, str]) -> dict[str, Any]:
    # The checkout's own console script, with the scratch LOCALAPPDATA: even if validation
    # unexpectedly passed, the scratch token cannot authorize a Request on another Daemon.
    completed = subprocess.run(
        [str(Path(sys.executable).with_name("td.exe")), "--json", *argv],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    lines = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    return {"argv": argv, "exit_code": completed.returncode, "stdout": lines}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--menu38", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    work = args.work.resolve()
    for name in ("ready.json", "done", "td-final.json", "td-error.json"):
        (work / name).unlink(missing_ok=True)
    localappdata = work / "localappdata"
    root = localappdata / "touchdesigner-cli"
    if root.exists():
        shutil.rmtree(root)  # scratch data root created by a previous run of this harness
    localappdata.mkdir(parents=True, exist_ok=True)
    work38 = json.loads(args.menu38.read_text(encoding="utf-8"))
    evidence: dict[str, Any] = {
        "artifact": str(args.artifact),
        "artifact_sha256": hashlib.sha256(args.artifact.read_bytes()).hexdigest(),
        "project": str(args.project),
        "port": PORT,
    }
    env = {
        **os.environ,
        "LOCALAPPDATA": str(localappdata),
        "TDCLI_MENU_LIMIT_WORK": str(work),
        "TDCLI_MENU_LIMIT_ARTIFACT": str(args.artifact),
        "TDCLI_MENU_LIMIT_URL": f"http://127.0.0.1:{PORT}",
    }
    process = subprocess.Popen([str(TOUCHDESIGNER), str(args.project)], env=env)
    evidence["touchdesigner_pid"] = process.pid
    server: uvicorn.Server | None = None
    thread: threading.Thread | None = None
    try:
        _wait_for(
            lambda: (
                (work / "ready.json").exists()
                or (work / "td-error.json").exists()
                or process.poll() is not None
            ),
            120,
            "TouchDesigner readiness",
        )
        if not (work / "ready.json").exists():
            raise RuntimeError("TouchDesigner probe did not become ready")
        evidence["touchdesigner_ready"] = json.loads((work / "ready.json").read_text("utf-8"))
        secure_layout(root)
        token = load_or_create_token(root)
        server = uvicorn.Server(
            uvicorn.Config(
                create_transport_app(root, token=token, runtime_health=lambda: True),
                host="127.0.0.1",
                port=PORT,
                log_config=None,
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        client = DaemonClient(timeout=30, root=root, endpoint=f"http://127.0.0.1:{PORT}")
        _wait_for(lambda: server is not None and server.started, 30, "acceptance Daemon")
        instance = _wait_for(
            lambda: next((i for i in client.instances() if i["status"] == "online"), None),
            60,
            "online Instance",
        )
        evidence["instance"] = instance
        harness = Harness(client, instance["instance_id"])
        harness.run(
            "create test COMP",
            "ops.create",
            {"parent_path": "/project1", "op_type": "baseCOMP", "name": "menu_limit"},
        )
        titles = [f"{index:02} 場景{index:02}" for index in range(1, 31)]
        harness.run(
            "create 30-item Scene menu",
            "parameters.page.create",
            {
                "operator_path": COMP,
                "page": "Preview",
                "parameters": [
                    {
                        "name": "Scene",
                        "label": "Scene",
                        "kind": "menu",
                        "default": "D01",
                        "menu_names": [f"D{index:02}" for index in range(1, 31)],
                        "menu_labels": titles,
                    }
                ],
            },
        )
        harness.run(
            "select D05",
            "parameters.set",
            {"operator_path": COMP, "parameter": "Scene", "mode": "constant", "value": "D05"},
        )
        menu38 = {"menu_names": work38["menu_names"], "menu_labels": work38["menu_labels"]}
        numbered = {
            "menu_names": [f"D{i:02}" for i in range(1, 257)],
            "menu_labels": [f"{i:03} 靜水漂浮" for i in range(1, 257)],
        }
        evidence["menu_sets"] = {
            "30_to_38_index": harness.menu_set("30 to 38 items", menu38, "index"),
            "38_to_256_name": harness.menu_set("38 to 256 items", numbered, "name"),
            "bmp_at_budget_index": harness.menu_set(
                "256 CJK items at budget", _at_budget("霧" * 40), "index"
            ),
            "astral_at_budget_index": harness.menu_set(
                "256 astral items at budget", _at_budget("\U0001f319" * 20), "index"
            ),
            "256_to_38_index": harness.menu_set("256 back to 38 items", menu38, "index"),
        }
        harness.run("destroy test COMP", "ops.destroy", {"operator_path": COMP, "recursive": True})
        evidence["requests"] = harness.requests
        evidence["instance_after"] = next(
            i for i in client.instances() if i["instance_id"] == instance["instance_id"]
        )
        over = {
            "operator_path": COMP,
            "parameter": "Scene",
            "menu_names": [f"D{i:03}" for i in range(257)],
            "menu_labels": ["x"] * 257,
            "preserve": "index",
        }
        (work / "menu257.json").write_text(json.dumps(over), encoding="utf-8")
        (work / "plan257.json").write_text(
            json.dumps({"commands": [{"name": "parameters.menu.set", "input": over}]}),
            encoding="utf-8",
        )
        evidence["cli_rejections"] = [
            _cli_rejection(
                ["parameters", "menu-set", "--input-file", str(work / "menu257.json")], env
            ),
            _cli_rejection(
                ["commands", "execute", "--input-file", str(work / "plan257.json")], env
            ),
        ]
    except Exception as error:
        evidence["harness_error"] = repr(error)
        raise
    finally:
        (work / "done").write_text("done", encoding="utf-8")
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()  # the disposable TouchDesigner process started above
            evidence["touchdesigner_killed"] = True
        evidence["touchdesigner_exit_code"] = process.returncode
        for name in ("td-final.json", "td-error.json"):
            if (work / name).exists():
                evidence[name.removesuffix(".json")] = json.loads((work / name).read_text("utf-8"))
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=15)
            evidence["daemon_thread_stopped"] = not thread.is_alive()
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
