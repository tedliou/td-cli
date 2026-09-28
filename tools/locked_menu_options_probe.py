"""Execute DAT probe for `parameters.menu.set`; loads no Agent and connects no Daemon.

The first start mutates real TouchDesigner custom menus through the public
``OperatorControl.execute`` handler, then saves the disposable project to
``SAVED``. Opening ``SAVED`` runs the same file again and verifies persistence.
Each phase writes its observation beside the repository and quits without saving.
"""

# ruff: noqa: F821 - TouchDesigner injects its Python API names at runtime.

import hashlib
import json
import os
import runpy
import traceback
from pathlib import Path

REPOSITORY = Path(r"E:\td-cli")
SOURCE = REPOSITORY / "agent" / "extension.py"
RESULTS = REPOSITORY / ".tmp-locked-runtime-menu-options-{}.json"
SAVED = REPOSITORY / ".codex" / "menu-set" / "menu-options-saved.toe"
OLD = [
    "D10", "D02", "D05", "D26", "D28", "D23", "D03", "D11", "D17", "D01",
    "D04", "D06", "D12", "D13", "D07", "D08", "D09", "D14", "D15", "D16",
    "D18", "D19", "D20", "D21", "D22", "D24", "D25", "D27", "D29", "D30",
]  # fmt: skip
TITLES = [f"場景{index:02}" for index in range(1, 31)]
OLD_LABELS = [f"{index:02} {name} {title}" for index, (name, title) in enumerate(
    zip(OLD, TITLES, strict=True), start=1)]  # fmt: skip
NEW = [f"D{index:02}" for index in range(1, 31)]
NEW_LABELS = [f"{index:02} {title}" for index, title in enumerate(TITLES, start=1)]


def onStart():
    run("args[0]()", _probe, delayFrames=5)


def _control():
    module = runpy.run_path(str(SOURCE))
    return module, module["OperatorControl"](op, None)


def _command(control, name, payload):
    return control.execute({"name": name, "input": payload})


def _menu_set(control, parameter, names, labels, preserve, path="/project1/controls"):
    return _command(
        control,
        "parameters.menu.set",
        {
            "operator_path": path,
            "parameter": parameter,
            "menu_names": names,
            "menu_labels": labels,
            "preserve": preserve,
        },
    )


def _state(parameter):
    index = parameter.menuIndex
    return {
        "names": [str(item) for item in parameter.menuNames],
        "labels": [str(item) for item in parameter.menuLabels],
        "value": str(parameter.eval()),
        "default": str(parameter.default),
        "index": None if index is None else int(index),
    }


def _rejected(module, control, parameter, code, *args, **kwargs):
    before = _state(parameter)
    try:
        _menu_set(control, *args, **kwargs)
    except module["AgentCommandError"] as error:
        observed = error.code
    else:
        observed = "accepted"
    return {"code": observed, "expected": code, "unchanged": _state(parameter) == before}


def _mutate(module, control):
    parent = op("/project1")
    comp = parent.create(baseCOMP, "controls")
    menu = {"kind": "menu", "default": "D10", "menu_names": OLD, "menu_labels": OLD_LABELS}
    small = ["a", "b", "c"]
    _command(
        control,
        "parameters.page.create",
        {
            "operator_path": comp.path,
            "page": "Preview",
            "parameters": [
                {"name": "Scene", "label": "Scene", **menu},
                {"name": "Reorder", "label": "Reorder", **menu},
                {
                    "name": "Small",
                    "label": "Small",
                    "kind": "menu",
                    "default": "a",
                    "menu_names": small,
                    "menu_labels": ["A", "B", "C"],
                },
            ],
        },
    )
    for name, value in (("Scene", "D28"), ("Reorder", "D28"), ("Small", "b")):
        _command(
            control,
            "parameters.set",
            {"operator_path": comp.path, "parameter": name, "mode": "constant", "value": value},
        )
    observations = {}
    observations["scene_index"] = _menu_set(control, "Scene", NEW, NEW_LABELS, "index")
    observations["scene_readback"] = _state(comp.par.Scene)
    listed = _command(control, "parameters.list", {"operator_path": comp.path})["parameters"]
    scene = next(item for item in listed if item["name"] == "Scene")
    observations["scene_listed"] = {
        "menu_names": scene["menu_names"],
        "menu_labels": scene["menu_labels"],
    }
    observations["scene_get"] = _command(
        control, "parameters.get", {"operator_path": comp.path, "parameter": "Scene"}
    )
    grown = [f"s{index:02}" for index in range(30)]
    observations["small_grow"] = _menu_set(
        control, "Small", grown, [f"S {index}" for index in range(30)], "index"
    )
    observations["small_grow_readback"] = _state(comp.par.Small)
    observations["small_shrink"] = _menu_set(control, "Small", ["x", "y"], ["X", "Y"], "index")
    observations["small_shrink_readback"] = _state(comp.par.Small)
    reversed_names = list(reversed(OLD))
    observations["reorder_name"] = _menu_set(
        control, "Reorder", reversed_names, list(reversed(OLD_LABELS)), "name"
    )
    observations["reorder_readback"] = _state(comp.par.Reorder)

    noise = parent.create(noiseTOP, "builtin_menu")
    table = parent.create(tableDAT, "menu_table")
    table.clear()
    table.appendRow(["q", "Q"])
    page = comp.customPages[0]
    sourced = page.appendMenu("Sourced", label="Sourced")[0]
    sourced.menuSource = "tdu.TableMenu(op('/project1/menu_table'))"
    driven = page.appendMenu("Driven", label="Driven")[0]
    driven.expr = "'name2'"
    free = page.appendStrMenu("Free", label="Free")[0]
    free.menuNames, free.menuLabels = ["f1", "f2"], ["F1", "F2"]
    observations["rejections"] = {
        "builtin": _rejected(
            module, control, noise.par.type, "parameter_menu_not_writable",
            "type", ["one", "two"], ["One", "Two"], "index", path=noise.path,
        ),
        "menu_source": _rejected(
            module, control, sourced, "parameter_menu_not_writable",
            "Sourced", ["m"], ["M"], "index",
        ),
        "expression_mode": _rejected(
            module, control, driven, "parameter_menu_not_writable",
            "Driven", ["m", "n"], ["M", "N"], "index",
        ),
        "str_menu": _rejected(
            module, control, free, "parameter_type_unsupported",
            "Free", ["m", "n"], ["M", "N"], "index",
        ),
        "index_out_of_range": _rejected(
            module, control, comp.par.Scene, "parameter_value_invalid",
            "Scene", ["D01", "D02"], ["01", "02"], "index",
        ),
        "name_missing": _rejected(
            module, control, comp.par.Scene, "parameter_value_invalid",
            "Scene", ["D01", "D02"], ["01", "02"], "name",
        ),
    }  # fmt: skip
    for name in ("Sourced", "Driven", "Free"):
        getattr(comp.par, name).destroy()
    noise.destroy()
    table.destroy()
    observations["saved_states"] = {
        name: _state(getattr(comp.par, name)) for name in ("Scene", "Reorder", "Small")
    }
    project.save(str(SAVED))
    observations["saved_sha256"] = hashlib.sha256(SAVED.read_bytes()).hexdigest()
    return observations


def _probe():
    comp = op("/project1/controls")
    phase = "reload" if comp is not None else "mutate"
    result = {
        "phase": phase,
        "pid": os.getpid(),
        "build": str(app.build),
        "project": str(project.folder) + "/" + str(project.name),
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
    }
    try:
        if phase == "mutate":
            module, control = _control()
            result.update(_mutate(module, control))
        else:
            result["reloaded_states"] = {
                name: _state(getattr(comp.par, name)) for name in ("Scene", "Reorder", "Small")
            }
    except Exception:  # noqa: BLE001 - persist any locked-runtime probe failure
        result["error"] = traceback.format_exc()
    RESULTS.with_name(RESULTS.name.format(phase)).write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    project.quit(force=True)
