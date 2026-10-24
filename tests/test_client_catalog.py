import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def export():
    return runpy.run_path(str(ROOT / "scripts" / "export_client_catalog.py"))


def test_committed_client_catalog_matches_the_python_authority() -> None:
    exporter = export()
    committed = (ROOT / "native" / "td" / "catalog.json").read_text(encoding="utf-8")
    assert committed == exporter["render"](), (
        "native/td/catalog.json is stale; run uv run python scripts/export_client_catalog.py"
    )


def test_client_catalog_covers_every_td_command_and_command_schema() -> None:
    catalog = export()["build_catalog"]()

    def leaves(node, path=()):
        for name, child in node.get("commands", {}).items():
            yield from leaves(child, (*path, name)) if "commands" in child else [(*path, name)]

    commands = set(leaves(catalog["cli"]))
    assert len(commands) == 46
    assert {("ops", "copy"), ("commands", "execute"), ("version",)} <= commands
    global_options = {param["name"] for param in catalog["cli"]["params"]}
    assert global_options == {"version", "as_json", "timeout", "instance"}
    assert all("input_schema" in item for item in catalog["commands"].values())
    assert catalog["errors"]["exit_codes"]["invalid_arguments"] == 2
