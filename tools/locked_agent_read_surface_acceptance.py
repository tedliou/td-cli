"""Locked acceptance for the Agent read surface and large structural mutations.

Covers issues 176, 171, 170, 166, 167, 168, 164/153, 169, and 159 on a candidate Agent through
this checkout's real Daemon transport (see ``locked_daemon_harness``). Each step records the
facts that the fake TouchDesigner graph in the unit tests cannot prove.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from tools.locked_daemon_harness import Harness, run_locked

PALETTE = Path(r"C:\Program Files\Derivative\TouchDesigner\Samples\Palette")
MAX_PAGES = 32  # every paged read here finishes in a handful of pages
TEXT = "td-cli 分段讀取：" + "繁體中文與 ASCII mixed 🎛 " * 6


def _create(harness: Harness, parent: str, op_type: str, name: str, **extra: Any) -> str:
    harness.run(
        f"create {name}",
        "ops.create",
        {"parent_path": parent, "op_type": op_type, "name": name, **extra},
    )
    return f"{parent.rstrip('/')}/{name}"


def _set(harness: Harness, path: str, parameter: str, value: Any) -> None:
    harness.run(
        f"set {path}.{parameter}",
        "parameters.set",
        {"operator_path": path, "parameter": parameter, "mode": "constant", "value": value},
    )


def _batch(harness: Harness) -> dict[str, Any]:
    result = harness.run(
        "batch with one failing read",
        "batch.execute",
        {
            "commands": [
                {"name": "ops.get", "input": {"operator_path": "/project1"}},
                {"name": "ops.get", "input": {"operator_path": "/project1/tdcli_missing"}},
            ]
        },
    )
    return {
        "failed_count": result["failed_count"],
        "items": [
            {"ok": item["ok"], "error": item.get("error", {}).get("code")}
            for item in result["results"]
        ],
    }


def _comp_interface(harness: Harness) -> dict[str, Any]:
    comp = _create(harness, "/project1", "baseCOMP", "iface")
    _create(harness, comp, "inTOP", "in1")
    _create(harness, comp, "outTOP", "out1")
    _create(harness, comp, "outCHOP", "out2")
    harness.run(
        "custom page",
        "parameters.page.create",
        {
            "operator_path": comp,
            "page": "Controls",
            "parameters": [
                {
                    "name": "Gain",
                    "label": "Gain",
                    "kind": "float",
                    "default": 1,
                    "minimum": 0,
                    "maximum": 2,
                },
                {"name": "Enabled", "label": "Enabled", "kind": "toggle", "default": True},
            ],
        },
    )
    return harness.run("inspect COMP", "ops.inspect", {"operator_path": comp})


def _text_dats(harness: Harness) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for op_type in ("executeDAT", "chopexecuteDAT", "parameterexecuteDAT"):
        path = _create(harness, "/project1", op_type, f"cb_{op_type}", allow_conditional=True)
        result = harness.run(f"read {op_type}", "dat.text.get", {"operator_path": path})
        evidence[op_type] = {
            "op_type": result["op_type"],
            "utf8_bytes": result["utf8_bytes"],
            "has_callbacks": "def " in result["text"],
        }
    table = _create(harness, "/project1", "tableDAT", "table_read")
    evidence["tableDAT"] = harness.error("read table", "dat.text.get", {"operator_path": table})

    notes = _create(harness, "/project1", "textDAT", "notes")
    harness.run("set notes", "dat.text.set", {"operator_path": notes, "text": TEXT})
    chunks, offset = [], 0
    while offset is not None and len(chunks) < MAX_PAGES:
        chunk = harness.run(
            f"chunk at {offset}",
            "dat.text.get",
            {"operator_path": notes, "offset": offset, "max_bytes": 40},
        )
        chunks.append(chunk)
        offset = chunk["next_offset"]
    evidence["chunking"] = {
        "chunks": len(chunks),
        "total_utf8_bytes": chunks[0]["total_utf8_bytes"],
        "reassembled_equals_text": "".join(chunk["text"] for chunk in chunks) == TEXT,
        "same_sha256_on_every_chunk": len({chunk["text_sha256"] for chunk in chunks}) == 1,
        "max_chunk_bytes": max(chunk["utf8_bytes"] for chunk in chunks),
    }
    inside = TEXT.encode("utf-8").index("分".encode()) + 1
    evidence["offset_inside_character"] = harness.error(
        "offset inside a character",
        "dat.text.get",
        {"operator_path": notes, "offset": inside, "max_bytes": 40},
    )
    return evidence


def _chop_values(harness: Harness) -> dict[str, Any]:
    constant = _create(harness, "/project1", "constantCHOP", "const")
    _set(harness, constant, "name0", "tx")
    _set(harness, constant, "value0", 1.5)
    _set(harness, constant, "name1", "ty")
    _set(harness, constant, "value1", -2)
    pattern = _create(harness, "/project1", "patternCHOP", "pattern")
    current = harness.run("current values", "chop.values.get", {"operator_path": constant})
    selected = harness.run(
        "selected channel", "chop.values.get", {"operator_path": constant, "channels": ["ty"]}
    )
    ranged = harness.run(
        "sample window",
        "chop.values.get",
        {"operator_path": pattern, "sample_offset": 0, "sample_count": 8},
    )
    beyond = harness.run(
        "window past the end",
        "chop.values.get",
        {"operator_path": pattern, "sample_offset": 100_000, "sample_count": 8},
    )
    return {
        "current": current,
        "selected": selected,
        "ranged": ranged,
        "beyond_end": beyond,
        "unknown_channel": harness.error(
            "unknown channel",
            "chop.values.get",
            {"operator_path": constant, "channels": ["missing"]},
        ),
        "not_a_chop": harness.error(
            "not a CHOP", "chop.values.get", {"operator_path": "/project1/notes"}
        ),
    }


def _parameter_lists(harness: Harness) -> dict[str, Any]:
    constant = "/project1/const"
    full = harness.run("full list", "parameters.list", {"operator_path": constant})
    non_default = harness.run(
        "non-default list",
        "parameters.list",
        {"operator_path": constant, "non_default_only": True},
    )
    # TouchDesigner reports the sequence names (const0name, ...) that parameters.set aliases.
    names = sorted(item["name"] for item in non_default["parameters"])[:2]
    named = harness.run(
        "named values",
        "parameters.list",
        {"operator_path": constant, "names": names, "include_values": True},
    )
    without_menus = harness.run(
        "without menu options",
        "parameters.list",
        {"operator_path": constant, "include_menu_options": False},
    )
    menus = [item for item in full["parameters"] if item["value_kind"] == "menu"]
    return {
        "full_count": len(full["parameters"]),
        "full_menu_parameters": len(menus),
        "non_default": {item["name"]: item["value"]["value"] for item in non_default["parameters"]},
        "named": {item["name"]: item["value"] for item in named["parameters"]},
        "menus_null_without_options": all(
            item["menu_names"] is None and item["menu_labels"] is None
            for item in without_menus["parameters"]
            if item["value_kind"] == "menu"
        ),
        "unknown_name": harness.error(
            "unknown parameter name",
            "parameters.list",
            {"operator_path": constant, "names": [names[0], "missing"]},
        ),
    }


def _snapshots(harness: Harness) -> dict[str, Any]:
    graph = _create(harness, "/project1", "baseCOMP", "graph")
    paths = [_create(harness, graph, "constantTOP", f"src{index}") for index in range(3)]
    sink = _create(harness, graph, "nullTOP", "sink")
    harness.run("wire src0 -> sink", "ops.connect", {"source_path": paths[0], "target_path": sink})
    base = {"operator_path": graph, "max_depth": 1, "max_operators": 2}
    pages, cursor = [], None
    while len(pages) < MAX_PAGES:
        page = harness.run(
            f"page {len(pages)}",
            "project.snapshot",
            {
                **base,
                "include_connections": True,
                **({"cursor": cursor} if cursor else {"paginate": True}),
            },
        )
        pages.append(page)
        cursor = page["next_cursor"]
        if cursor is None:
            break
    rows = [row for page in pages for row in page["operators"]]
    first = harness.run("stale first page", "project.snapshot", {**base, "paginate": True})
    harness.run(
        "destroy next operator",
        "ops.destroy",
        {"operator_path": rows[2]["path"], "allow_connected": True},
    )
    return {
        "pages": [[row["path"] for row in page["operators"]] for page in pages],
        "connections": {row["path"]: row["connections"] for row in rows},
        "over_limit_without_paginate": harness.error("over limit", "project.snapshot", base),
        "stale_cursor": harness.error(
            "stale cursor", "project.snapshot", {**base, "cursor": first["next_cursor"]}
        ),
    }


def _large_structures(harness: Harness) -> dict[str, Any]:
    parent = _create(harness, "/project1", "baseCOMP", "projection")
    target = _create(harness, "/project1", "baseCOMP", "moved_parent")
    tox = PALETTE / "Mapping" / "kantanMapper.tox"
    installed = harness.run(
        "import kantanMapper",
        "ops.tox.import",
        {
            "parent_path": parent,
            "tox_path": str(tox),
            "allowlist_root": str(tox.parent),
            "target_name": "kantanMapper",
            "trusted": True,
            "root_child": "kantanMapper",
            "inventory": "summary",
            "max_operators": 5000,
        },
    )
    kantan = installed["path"]
    summary = {"inventory": "summary", "max_operators": 5000}
    copied = harness.run(
        "summary copy",
        "ops.copy",
        {"source_path": kantan, "target_parent_path": parent, "new_name": "kantanCopy", **summary},
    )
    moved = harness.run(
        "summary move",
        "ops.move",
        {
            "source_path": copied["path"],
            "target_parent_path": target,
            "new_name": "kantanMapper",
            **summary,
        },
    )
    full_destroy = harness.error(
        "full destroy over 1000",
        "ops.destroy",
        {
            "operator_path": kantan,
            "recursive": True,
            "allow_connected": True,
            "max_operators": 1000,
        },
    )
    destroyed_moved = harness.run(
        "summary destroy moved",
        "ops.destroy",
        {"operator_path": moved["path"], "recursive": True, "allow_connected": True, **summary},
    )
    destroyed = harness.run(
        "summary destroy original",
        "ops.destroy",
        {"operator_path": kantan, "recursive": True, "allow_connected": True, **summary},
    )
    keys = ("operator_count", "inventory_sha256", "type_counts")
    return {
        "import": {key: installed[key] for key in keys},
        "copy": {key: copied[key] for key in keys}
        | {"unreplicated_connections": copied["unreplicated_connections"]},
        "move": {key: moved[key] for key in keys}
        | {"detached_connections": moved["detached_connections"]},
        "full_destroy_over_1000": full_destroy,
        "destroy_moved": {key: destroyed_moved[key] for key in keys}
        | {"detached_connections": destroyed_moved["detached_connections"]},
        "destroy_original": {key: destroyed[key] for key in keys},
        "move_and_destroy_digest_match": moved["inventory_sha256"]
        == destroyed_moved["inventory_sha256"],
        "copy_counts_match_import": copied["type_counts"] == installed["type_counts"],
        "children_after": harness.run("children after", "ops.children", {"operator_path": parent}),
    }


def _steps(harness: Harness, _work: Path) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for name, step in (
        ("batch", _batch),
        ("comp_inspect", _comp_interface),
        ("text_dats", _text_dats),
        ("chop_values", _chop_values),
        ("parameters_list", _parameter_lists),
        ("snapshot", _snapshots),
        ("large_structures", _large_structures),
    ):
        try:
            evidence[name] = step(harness)
        except Exception as error:  # noqa: BLE001 - record the failed step, keep the others
            evidence[name] = {"step_error": repr(error)}
    evidence["failed_steps"] = [
        name
        for name, value in evidence.items()
        if isinstance(value, dict) and "step_error" in value
    ]
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    run_locked(
        work=args.work,
        project=args.project,
        artifact=args.artifact,
        evidence_path=args.evidence,
        steps=_steps,
    )


if __name__ == "__main__":
    main()
