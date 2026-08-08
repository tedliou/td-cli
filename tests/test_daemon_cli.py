import json
from pathlib import Path

from td_cli.daemon import cli


def test_status_reports_authenticated_unhealthy_daemon(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "daemon.json").write_text(
        json.dumps({"pid": 42, "release_version": "test", "protocol_versions": [1]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli,
        "_probe",
        lambda _: {"ready": False, "release_version": "test", "protocol_versions": [1]},
    )

    assert cli._status_payload(tmp_path)["status"] == "starting/unhealthy"


def test_status_on_fresh_layout_does_not_create_auth_token(tmp_path: Path) -> None:
    assert cli._status_payload(tmp_path)["status"] == "stopped"
    assert not (tmp_path / "state" / "auth.token").exists()
