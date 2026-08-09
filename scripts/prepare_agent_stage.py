from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from td_cli import __version__
from td_cli.agent_tool import source_revision


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path("agent"))
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source_manifest = json.loads((args.source / "manifest.json").read_text(encoding="utf-8"))
    evidence_path = args.artifact.with_suffix(args.artifact.suffix + ".manifest.json")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    verification = json.loads(args.verification.read_text(encoding="utf-8"))
    expected_revision = source_revision(args.source, source_manifest["required_files"])
    if evidence.get("source_revision") != expected_revision:
        raise SystemExit("artifact is stale relative to canonical Agent Component source")
    if evidence.get("touchdesigner_version") != source_manifest["locked_touchdesigner_version"]:
        raise SystemExit("artifact was built with an unlocked TouchDesigner version")
    if verification.get("validated") is not True:
        raise SystemExit("locked TouchDesigner verification did not pass")
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(args.artifact, args.output / "td-agent.tox")
    manifest = {
        **evidence,
        "agent_version": __version__,
        "locked_touchdesigner_version": source_manifest["locked_touchdesigner_version"],
        "source_commit": args.source_commit,
    }
    verification["source_commit"] = args.source_commit
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    (args.output / "verification.json").write_text(
        json.dumps(verification, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
