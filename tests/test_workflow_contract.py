from pathlib import Path


def test_release_workflow_gates_publication_on_main_and_remote_digests() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "Release source must be the current main tip" in workflow
    assert "Release Version is not an approved Phase prerelease" in workflow
    assert "Remote asset digest mismatch" in workflow
    assert "SHA256SUMS does not match remote asset" in workflow
    assert workflow.index("Remote asset digest mismatch") < workflow.index("--draft=false")
    assert "contents: write" in workflow
    assert "environment: release" in workflow
    assert 'GH_TOKEN: "${{ github.token }}"' in workflow
    assert "create-github-app-token" not in workflow
    assert "RELEASE_APP_" not in workflow


def test_release_workflow_bootstraps_tag_identity_and_handles_missing_draft() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "function Get-ReleaseByTag" in workflow
    assert "HTTP 404" in workflow
    assert "git config user.name 'github-actions[bot]'" in workflow
    assert (
        "git config user.email '41898282+github-actions[bot]@users.noreply.github.com'" in workflow
    )
    assert "gh auth setup-git" in workflow
    assert workflow.index("git config user.name") < workflow.index("git tag -a")


def test_workflows_pin_actions_to_commit_shas() -> None:
    for path in Path(".github/workflows").glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "uses:" in line:
                assert "@" in line and len(line.rsplit("@", 1)[1].strip()) == 40


def test_agent_staging_uses_hosted_ci_without_touchdesigner_dependency() -> None:
    workflow = Path(".github/workflows/stage-agent-component.yml").read_text(encoding="utf-8")
    assert "runs-on: windows-2022" in workflow
    assert "self-hosted" not in workflow
    assert "stage_archive_base64" in workflow
    assert "Stage archive digest mismatch" in workflow
    assert workflow.index("Stage archive digest mismatch") < workflow.index(
        "Validate staged files supplied by authorized environment"
    )
    dispatch = Path("scripts/dispatch_agent_stage.ps1").read_text(encoding="utf-8")
    assert "stage_archive_base64=$payload" in dispatch
    assert "stage_archive_sha256=$digest" in dispatch
