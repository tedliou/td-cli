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
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from tools.locked_daemon_harness import TOUCHDESIGNER, Harness, run_locked

TOEEXPAND = TOUCHDESIGNER.with_name("toeexpand.exe")
PALETTE = Path(r"C:\Program Files\Derivative\TouchDesigner\Samples\Palette")
PARENT = "/project1/projection"


def _palette_listing(name: str) -> list[str]:
    """Relative paths of the same-named inner component, from the vendor TOX expansion.

    Expands in a short temporary directory: kantanMapper nests deeply enough for
    ``toeexpand`` to silently skip files beneath a long base path (MAX_PATH).
    """
    with tempfile.TemporaryDirectory(prefix="tdx") as directory:
        expanded = Path(directory)
        shutil.copyfile(PALETTE / "Mapping" / f"{name}.tox", expanded / f"{name}.tox")
        subprocess.run(
            [str(TOEEXPAND), f"{name}.tox"],
            cwd=expanded,
            capture_output=True,
            timeout=120,
            check=False,
        )
        toc = (expanded / f"{name}.tox.toc").read_text(encoding="utf-8").split()
        missing = [
            entry
            for entry in toc
            if entry.endswith(".n") and not (expanded / f"{name}.tox.dir" / entry).is_file()
        ]
        if missing:
            raise RuntimeError(f"incomplete toeexpand output: {len(missing)} missing files")
    prefix = f"{name}/{name}/"
    return sorted(
        ["."]
        + [
            entry[len(prefix) : -2]
            for entry in toc
            if entry.startswith(prefix) and entry.endswith(".n")
        ]
    )


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
    # A wrapper discarded by root_child is still checked, including expression-driven linkage.
    harness.run(
        "wrapper source",
        "ops.create",
        {"parent_path": "/project1", "op_type": "baseCOMP", "name": "tdcli_wrapped"},
    )
    harness.run(
        "wrapper inner",
        "ops.create",
        {"parent_path": "/project1/tdcli_wrapped", "op_type": "baseCOMP", "name": "inner"},
    )
    harness.run(
        "wrapper sibling",
        "ops.create",
        {"parent_path": "/project1/tdcli_wrapped", "op_type": "baseCOMP", "name": "sibling"},
    )
    # The root's own externaltox does not survive saveByteArray, so link a wrapper child.
    harness.run(
        "wrapper externaltox expression",
        "parameters.set",
        {
            "operator_path": "/project1/tdcli_wrapped/sibling",
            "parameter": "externaltox",
            "mode": "expression",
            "value": "''",
        },
    )
    wrapped = harness.run(
        "export wrapper",
        "binary.export",
        {"operator_path": "/project1/tdcli_wrapped", "format": "tox"},
    )
    wrapped_tox = work / "linked" / "wrapped.tox"
    wrapped_tox.write_bytes(base64.b64decode(wrapped["data_base64"]))
    harness.run(
        "destroy wrapper source",
        "ops.destroy",
        {"operator_path": "/project1/tdcli_wrapped", "recursive": True},
    )
    rejected["wrapper_expression_linkage"] = harness.tox(
        "wrapper expression linkage", wrapped_tox, "inner", root_child="inner"
    )
    if rejected["wrapper_expression_linkage"].get("status") == "succeeded":
        harness.run(
            "destroy unexpected inner",
            "ops.destroy",
            {"operator_path": f"{PARENT}/inner", "recursive": True},
        )
    rejected["full_inventory_over_outcome"] = harness.tox(
        "kantanMapper full inventory", kantan_tox, "kantanMapper", root_child="kantanMapper"
    )
    evidence["rejected"] = {
        key: value.get("error") or value.get("status") for key, value in rejected.items()
    }
    evidence["children_after_rejections"] = harness.run(
        "children after rejections", "ops.children", {"operator_path": PARENT}
    )

    # Palette-drag equivalent imports.
    blend = harness.tox(
        "import projectorBlend", blend_tox, "projectorBlend", root_child="projectorBlend"
    )["result"]
    expected_blend = _palette_listing("projectorBlend")
    kantan = harness.tox(
        "import kantanMapper",
        kantan_tox,
        "kantanMapper",
        root_child="kantanMapper",
        inventory="summary",
        max_operators=5000,
    )["result"]
    expected_kantan = _palette_listing("kantanMapper")
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

    def steps(harness: Harness, work: Path) -> dict[str, Any]:
        harness.run(
            "create parent",
            "ops.create",
            {"parent_path": "/project1", "op_type": "baseCOMP", "name": "projection"},
        )
        if not args.legacy:
            return _palette_steps(harness, work)
        evidence = {"legacy": _legacy_steps(harness)}
        harness.run("destroy parent", "ops.destroy", {"operator_path": PARENT, "recursive": True})
        return evidence

    run_locked(
        work=args.work,
        project=args.project,
        artifact=args.artifact,
        evidence_path=args.evidence,
        steps=steps,
    )


if __name__ == "__main__":
    main()
