import json
import logging
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from td_cli.daemon import runtime_files
from td_cli.daemon.runtime_files import configure_logging, load_or_create_token, load_token


def test_local_token_is_persistent_and_malformed_token_blocks_startup(tmp_path: Path) -> None:
    (tmp_path / "state").mkdir()
    first = load_or_create_token(tmp_path)
    assert len(first) == 64
    assert load_or_create_token(tmp_path) == first

    (tmp_path / "state" / "auth.token").write_text("broken", encoding="ascii")
    with pytest.raises(RuntimeError, match="malformed"):
        load_or_create_token(tmp_path)


def test_observing_a_missing_token_does_not_create_it(tmp_path: Path) -> None:
    (tmp_path / "state").mkdir()
    assert load_token(tmp_path) is None
    assert not (tmp_path / "state" / "auth.token").exists()


def test_daemon_log_is_bounded_single_line_json_and_redacts_tokens(tmp_path: Path) -> None:
    (tmp_path / "logs").mkdir()
    logger = configure_logging(tmp_path)
    token = "a" * 64
    logger.info("connected token=%s\nnext", token)
    for handler in logger.handlers:
        handler.flush()

    line = (tmp_path / "logs" / "daemon.log").read_text(encoding="utf-8").splitlines()
    assert len(line) == 1
    payload = json.loads(line[0])
    assert token not in payload["event"]
    assert "[REDACTED]" in payload["event"]
    rotating = logger.handlers[0]
    assert isinstance(rotating, logging.handlers.RotatingFileHandler)
    assert rotating.maxBytes == 5 * 1024 * 1024
    assert rotating.backupCount == 4


def test_log_handler_records_runtime_failure_for_health_reporting(tmp_path: Path) -> None:
    (tmp_path / "logs").mkdir()
    logger = configure_logging(tmp_path)
    handler = logger.handlers[0]
    previous = logging.raiseExceptions
    logging.raiseExceptions = False
    try:
        handler.handleError(logging.LogRecord("test", logging.ERROR, "", 0, "failed", (), None))
    finally:
        logging.raiseExceptions = previous
    assert handler.healthy is False


@pytest.mark.parametrize("existing_marker", [False, True])
def test_secure_layout_removes_explicit_administrators_acl(
    monkeypatch, tmp_path: Path, existing_marker: bool
) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], **_: object) -> CompletedProcess[str]:
        calls.append(command)
        if command[0] == "powershell":
            return CompletedProcess(
                command,
                0,
                '{"current":"S-1-5-21-1","rules":['
                '{"sid":"S-1-5-21-1","type":"Allow","rights":"FullControl","inherited":false},'
                '{"sid":"S-1-5-18","type":"Allow","rights":"FullControl","inherited":false}]}',
                "",
            )
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(runtime_files.os, "name", "nt")
    monkeypatch.setenv("USERNAME", "operator")
    monkeypatch.setattr(runtime_files.subprocess, "run", run)

    root = tmp_path / "touchdesigner-cli"
    if existing_marker:
        root.mkdir()
        (root / ".acl-applied").write_text("restricted\n", encoding="ascii")

    runtime_files.secure_layout(root)

    remove_call = calls[0 if existing_marker else 1]
    assert remove_call == [
        "icacls",
        str(root),
        "/remove:g",
        "*S-1-5-32-544",
        "/T",
        "/C",
    ]
    assert (root / ".acl-applied").read_text(encoding="ascii") == "restricted\n"
