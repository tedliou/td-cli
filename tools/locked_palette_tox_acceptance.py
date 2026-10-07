"""Locked acceptance for issue 157: importing official Palette TOX components.

Runs this checkout's Daemon transport on an isolated port with a scratch data root and launches
a disposable TouchDesigner project whose Execute DAT is ``locked_menu_limit_probe.py`` (it loads
the Agent artifact named by ``TDCLI_MENU_LIMIT_ARTIFACT`` and redirects it to this Daemon). Every
Command goes through the public DaemonClient. The user's Daemon, data root, and projects are never
touched; the TouchDesigner process started here is the only one stopped.

With a candidate Agent it imports ``projectorBlend`` and ``kantanMapper`` the way a Palette drag
does, compares their inventories with the vendor ``toeexpand`` listing, exercises Parameters and
wiring, and records the diagnostic details of rejected imports. With ``--legacy`` it records how
an Agent without the new capabilities behaves.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn

from td_cli.cli import _uuid7
from td_cli.client import ClientError, DaemonClient
from td_cli.daemon.runtime_files import load_or_create_token, secure_layout
from td_cli.daemon.transport import create_transport_app
from td_cli.protocol import Command

PORT = 19982
TOUCHDESIGNER = Path(r"C:\Program Files\Derivative\TouchDesigner\bin\TouchDesigner.exe")
TOEEXPAND = TOUCHDESIGNER.with_name("toeexpand.exe")
PALETTE = Path(r"C:\Program Files\Derivative\TouchDesigner\Samples\Palette")
PARENT = "/project1/projection"


def _wait_for(predicate, seconds: float, what: str) -> Any:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.5)
    raise TimeoutError(f"timed out waiting for {what}")


def _palette_listing(work: Path, name: str) -> list[str]:
    """Relative paths of the same-named inner component, from the vendor TOX expansion."""
    expanded = work / "expanded"
    expanded.mkdir(parents=True, exist_ok=True)
    copy = expanded / f"{name}.tox"
    shutil.copyfile(PALETTE / "Mapping" / f"{name}.tox", copy)
    subprocess.run(
        [str(TOEEXPAND), copy.name], cwd=expanded, capture_output=True, timeout=120, check=False
    )
    toc = (expanded / f"{name}.tox.toc").read_text(encoding="utf-8").split()
    prefix = f"{name}/{name}/"
    return sorted(
        ["."]
        + [
            entry[len(prefix) : -2]
            for entry in toc
            if entry.startswith(prefix) and entry.endswith(".n")
        ]
    )


class Harness:
    def __init__(self, client: DaemonClient, instance_id: str) -> None:
        self.client = client
        self.instance_id = instance_id
        self.requests: list[dict[str, Any]] = []

    def submit(self, label: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        command = Command.model_validate({"name": name, "input": payload}).model_dump(mode="json")
        request_id = _uuid7()
        started = time.monotonic()
        try:
            self.client.submit(request_id, self.instance_id, command)
        except ClientError as error:
            row = {
                "step": label,
                "command": name,
                "request_id": request_id,
                "admission_error": error.code,
            }
            self.requests.append(row)
            return row
        snapshot = self.client.wait(request_id)
        self.requests.append(
            {
                "step": label,
                "command": name,
                "request_id": request_id,
                "status": snapshot["status"],
                "error": snapshot.get("error"),
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
                "result_bytes": len(json.dumps(snapshot.get("result"), separators=(",", ":"))),
            }
        )
        return snapshot

    def run(self, label: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.submit(label, name, payload)
        if snapshot.get("status") != "succeeded":
            raise RuntimeError(f"{label}: {snapshot}")
        return snapshot["result"]

    def tox(self, label: str, tox: Path, target: str, **extra: Any) -> dict[str, Any]:
        payload = {
            "parent_path": PARENT,
            "tox_path": str(tox),
            "allowlist_root": str(tox.parent),
            "target_name": target,
            "trusted": True,
            **extra,
        }
        return self.submit(label, "ops.tox.import", payload)


def _palette_steps(harness: Harness, work: Path) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    blend_tox = PALETTE / "Mapping" / "projectorBlend.tox"
    kantan_tox = PALETTE / "Mapping" / "kantanMapper.tox"

    # Rejections name the failed check and Operator, and leave no residue.
    rejected = {
        "operator_limit": harness.tox(
            "projectorBlend over bound",
            blend_tox,
            "projectorBlend",
            root_child="projectorBlend",
            max_operators=10,
        ),
        "root_child_missing": harness.tox(
            "missing root child", blend_tox, "projectorBlend", root_child="missingChild"
        ),
        "full_inventory_over_outcome": None,
    }
    harness.run(
        "linked source",
        "ops.create",
        {"parent_path": "/project1", "op_type": "baseCOMP", "name": "tdcli_linked_src"},
    )
    harness.run(
        "linked child",
        "ops.create",
        {"parent_path": "/project1/tdcli_linked_src", "op_type": "baseCOMP", "name": "linked"},
    )
    harness.run(
        "set externaltox",
        "parameters.set",
        {
            "operator_path": "/project1/tdcli_linked_src/linked",
            "parameter": "externaltox",
            "mode": "constant",
            "value": "C:/tdcli_missing/linked.tox",
        },
    )
    exported = harness.run(
        "export linked",
        "binary.export",
        {"operator_path": "/project1/tdcli_linked_src", "format": "tox"},
    )
    linked_tox = work / "linked" / "linked_src.tox"
    linked_tox.parent.mkdir(parents=True, exist_ok=True)
    linked_tox.write_bytes(base64.b64decode(exported["data_base64"]))
    harness.run(
        "destroy linked source",
        "ops.destroy",
        {"operator_path": "/project1/tdcli_linked_src", "recursive": True},
    )
    rejected["external_tox"] = harness.tox("external linkage", linked_tox, "linked")
    rejected["full_inventory_over_outcome"] = harness.tox(
        "kantanMapper full inventory", kantan_tox, "kantanMapper", root_child="kantanMapper"
    )
    evidence["rejected"] = {key: value.get("error") for key, value in rejected.items()}
    evidence["children_after_rejections"] = harness.run(
        "children after rejections", "ops.children", {"operator_path": PARENT}
    )

    # Palette-drag equivalent imports.
    blend = harness.tox(
        "import projectorBlend", blend_tox, "projectorBlend", root_child="projectorBlend"
    )["result"]
    expected_blend = _palette_listing(work, "projectorBlend")
    kantan = harness.tox(
        "import kantanMapper",
        kantan_tox,
        "kantanMapper",
        root_child="kantanMapper",
        inventory="summary",
        max_operators=5000,
    )["result"]
    expected_kantan = _palette_listing(work, "kantanMapper")
    evidence["projectorBlend"] = {
        **{
            key: blend[key] for key in ("path", "op_type", "operator_count", "sha256", "root_child")
        },
        "inventory_matches_palette_listing": sorted(
            row["relative_path"] for row in blend["inventory"]
        )
        == expected_blend,
        "palette_listing_count": len(expected_blend),
    }
    evidence["kantanMapper"] = {
        **{
            key: kantan[key]
            for key in (
                "path",
                "op_type",
                "operator_count",
                "sha256",
                "root_child",
                "inventory_sha256",
                "type_counts",
            )
        },
        "count_matches_palette_listing": kantan["operator_count"] == len(expected_kantan),
        "palette_listing_count": len(expected_kantan),
    }

    # Parameters, children, wiring, and the internal out TOPs.
    blend_path, kantan_path = blend["path"], kantan["path"]
    listings = {}
    for path in (blend_path, kantan_path):
        listing = harness.run(f"list {path}", "parameters.list", {"operator_path": path})
        listings[path] = sorted(
            item["name"] for item in listing["parameters"] if item.get("custom")
        )
    evidence["custom_parameters"] = listings
    sets = {}
    for path, name, value in (
        (blend_path, "Projector1overlap2", 240),
        (kantan_path, "w", 1920),
        (kantan_path, "h", 1080),
    ):
        harness.run(
            f"set {name}",
            "parameters.set",
            {"operator_path": path, "parameter": name, "mode": "constant", "value": value},
        )
        sets[f"{path}.{name}"] = harness.run(
            f"get {name}", "parameters.get", {"operator_path": path, "parameter": name}
        )
    evidence["parameter_round_trips"] = sets
    evidence["children"] = {
        path: [
            row["name"]
            for row in harness.run(
                f"children {path}", "ops.children", {"operator_path": path, "op_type": "outTOP"}
            )
        ]
        for path in (blend_path, kantan_path)
    }
    harness.run(
        "create source",
        "ops.create",
        {"parent_path": PARENT, "op_type": "constantTOP", "name": "source"},
    )
    harness.run(
        "create sink", "ops.create", {"parent_path": PARENT, "op_type": "nullTOP", "name": "sink"}
    )
    harness.run(
        "kantan out1 -> blend",
        "ops.connect",
        {"source_path": kantan_path, "target_path": blend_path},
    )
    harness.run(
        "blend -> sink", "ops.connect", {"source_path": blend_path, "target_path": f"{PARENT}/sink"}
    )
    harness.run(
        "disconnect kantan",
        "ops.disconnect",
        {"source_path": kantan_path, "target_path": blend_path},
    )
    harness.run(
        "source -> blend",
        "ops.connect",
        {"source_path": f"{PARENT}/source", "target_path": blend_path},
    )
    evidence["connections"] = harness.run(
        "connections", "ops.connections", {"operator_path": blend_path}
    )
    evidence["blend_out_top"] = harness.run(
        "inspect blend out1", "ops.inspect", {"operator_path": f"{blend_path}/out1"}
    )
    evidence["kantan_out_top"] = harness.run(
        "inspect kantan out1", "ops.inspect", {"operator_path": f"{kantan_path}/out1"}
    )
    # ops.destroy keeps its 1000-Operator bound; the disposable project is discarded unsaved.
    evidence["destroy_projectorBlend"] = harness.submit(
        "destroy projectorBlend",
        "ops.destroy",
        {"operator_path": blend_path, "recursive": True, "allow_connected": True},
    ).get("status")
    evidence["destroy_kantanMapper"] = harness.submit(
        "destroy kantanMapper",
        "ops.destroy",
        {
            "operator_path": kantan_path,
            "recursive": True,
            "allow_connected": True,
            "max_operators": 1000,
        },
    ).get("error")
    return evidence


def _legacy_steps(harness: Harness) -> dict[str, Any]:
    blend_tox = PALETTE / "Mapping" / "projectorBlend.tox"
    plain = harness.tox("legacy plain import", blend_tox, "projectorBlend")
    child = harness.tox(
        "legacy root_child", blend_tox, "projectorBlend", root_child="projectorBlend"
    )
    return {
        "plain_import": plain.get("error") or plain.get("status"),
        "root_child": child.get("admission_error") or child.get("status"),
        "children_after": harness.run("children", "ops.children", {"operator_path": PARENT}),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--legacy", action="store_true")
    args = parser.parse_args()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    for name in ("ready.json", "done", "td-final.json", "td-error.json"):
        (work / name).unlink(missing_ok=True)
    localappdata = work / "localappdata"
    root = localappdata / "touchdesigner-cli"
    if root.exists():
        shutil.rmtree(root)  # scratch data root created by a previous run of this harness
    localappdata.mkdir(parents=True, exist_ok=True)
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
    harness: Harness | None = None
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
        client = DaemonClient(timeout=60, root=root, endpoint=f"http://127.0.0.1:{PORT}")
        _wait_for(lambda: server is not None and server.started, 30, "acceptance Daemon")
        instance = _wait_for(
            lambda: next((i for i in client.instances() if i["status"] == "online"), None),
            60,
            "online Instance",
        )
        evidence["instance"] = instance
        harness = Harness(client, instance["instance_id"])
        harness.run(
            "create parent",
            "ops.create",
            {"parent_path": "/project1", "op_type": "baseCOMP", "name": "projection"},
        )
        if args.legacy:
            evidence["legacy"] = _legacy_steps(harness)
            harness.run(
                "destroy parent", "ops.destroy", {"operator_path": PARENT, "recursive": True}
            )
        else:
            evidence.update(_palette_steps(harness, work))
    except Exception as error:
        evidence["harness_error"] = repr(error)
        raise
    finally:
        if harness is not None:
            evidence["requests"] = harness.requests
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
