import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
MANUALS = ("README.md", "README.zh-TW.md", "README.zh-CN.md")
SECTIONS = (
    "overview",
    "requirements",
    "install",
    "development",
    "daemon",
    "agent-component",
    "operator-control",
    "parameter-control",
    "regular-connections",
    "hierarchy-connections",
    "structural-mutations",
    "trusted-tox-import",
    "operator-state",
    "dat-content",
    "operator-catalog",
)


def test_manuals_cross_link_and_share_stable_section_order() -> None:
    for manual in MANUALS:
        text = (ROOT / manual).read_text(encoding="utf-8")
        assert all(f"]({target})" in text for target in MANUALS)
        positions = [text.index(f"<!-- doc-section: {section} -->") for section in SECTIONS]
        assert positions == sorted(positions)


def test_runtime_reliability_skill_and_policy_owners_are_discoverable() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    skill = ROOT / ".agents" / "skills" / "td-runtime-reliability" / "SKILL.md"

    assert ".agents/skills/td-runtime-reliability/SKILL.md" in agents
    assert "主 session" in agents
    assert "## Pull requests" in contributing
    assert "README.zh-TW.md" in contributing
    assert skill.is_file()
    assert "name: td-runtime-reliability" in skill.read_text(encoding="utf-8")


def test_markdown_contains_no_control_characters() -> None:
    """Escapes such as a Windows path's backslash-t must not reach the documents as TAB or BS.

    Covers every tracked Markdown file, including the packaged skill, except the verbatim
    upstream license texts under LICENSES/.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.md"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout.decode("utf-8")
    documents = [name for name in listed.split(chr(0)) if name and not name.startswith("LICENSES/")]
    assert "src/td_cli/data/skill/td-cli/SKILL.md" in documents
    allowed = {chr(10), chr(13)}
    for name in documents:
        text = (ROOT / name).read_text(encoding="utf-8")
        found = sorted({f"U+{ord(c):04X}" for c in text if ord(c) < 32 and c not in allowed})
        assert not found, f"{name} contains {found}"
