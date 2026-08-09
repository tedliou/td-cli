from pathlib import Path


def test_release_workflow_gates_publication_on_main_and_remote_digests() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "Release source must be the current main tip" in workflow
    assert "Release Version is not an approved Phase prerelease" in workflow
    assert "Remote asset digest mismatch" in workflow
    assert "SHA256SUMS does not match remote asset" in workflow
    assert workflow.index("Remote asset digest mismatch") < workflow.index("--draft=false")


def test_workflows_pin_actions_to_commit_shas() -> None:
    for path in Path(".github/workflows").glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "uses:" in line:
                assert "@" in line and len(line.rsplit("@", 1)[1].strip()) == 40
