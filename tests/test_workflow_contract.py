from pathlib import Path

import pytest

from td_cli.release import is_approved_release_version


def test_release_workflow_gates_publication_on_main_and_remote_digests() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "Release source must be the current main tip" in workflow
    assert "Release Version must be an approved pre-1.0 SemVer" in workflow
    assert "is_approved_release_version" in workflow
    assert "Remote asset digest mismatch" in workflow
    assert "SHA256SUMS does not match remote asset" in workflow
    assert workflow.index("Remote asset digest mismatch") < workflow.index("--draft=false")
    assert "contents: write" in workflow
    assert "environment: release" in workflow
    assert 'GH_TOKEN: "${{ github.token }}"' in workflow
    assert "create-github-app-token" not in workflow
    assert "RELEASE_APP_" not in workflow


@pytest.mark.parametrize(
    "value",
    ["0.1.2", "0.2.0", "0.2.1", "0.2.0-alpha.0", "0.2.0-beta.1", "0.2.0-rc.2"],
)
def test_release_version_policy_accepts_supported_pre_1_semver(value: str) -> None:
    assert is_approved_release_version(value)


@pytest.mark.parametrize(
    "value",
    [
        "0.0.1",
        "0.02.0",
        "0.2.00",
        "0.2.0-rc.01",
        "0.2.0-preview.1",
        "0.2.0+build.1",
        "1.0.0",
    ],
)
def test_release_version_policy_rejects_unapproved_or_noncanonical_versions(value: str) -> None:
    assert not is_approved_release_version(value)


def test_release_workflow_bootstraps_tag_identity_and_handles_missing_draft() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "function Get-ReleaseByTag" in workflow
    assert "gh release list --limit 100 --json tagName" in workflow
    assert "releases/tags/$Tag" not in workflow
    assert "gh release view $Tag --json databaseId" in workflow
    assert "releases/$($summary.databaseId)" in workflow
    assert "git config user.name 'github-actions[bot]'" in workflow
    assert (
        "git config user.email '41898282+github-actions[bot]@users.noreply.github.com'" in workflow
    )
    assert "persist-credentials: true" in workflow
    assert "gh auth setup-git" not in workflow
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
    assert "stage_archive_base64 = $payload" in dispatch
    assert "stage_archive_sha256 = $digest" in dispatch
    assert "ConvertTo-Json -Compress" in dispatch
    assert "workflow run stage-agent-component.yml --ref main --json" in dispatch
    assert '-f "stage_archive_base64=$payload"' not in dispatch


def test_hosted_executable_smoke_runs_the_daemon_lifecycle() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert 'td-daemon.exe" start' in workflow
    assert 'td-daemon.exe" status --json' in workflow
    assert 'td-daemon.exe" stop' in workflow
