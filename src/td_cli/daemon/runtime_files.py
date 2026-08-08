from __future__ import annotations

import json
import logging
import os
import re
import secrets
import subprocess
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


def data_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("LOCALAPPDATA is unavailable")
    return Path(local) / "touchdesigner-cli"


def secure_layout(root: Path) -> None:
    """Create the per-user layout and reject visibly broad Windows ACLs."""
    for child in (root / "state", root / "logs", root / "run"):
        child.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        return
    username = os.environ.get("USERNAME")
    if not username:
        raise RuntimeError("USERNAME is unavailable")
    if not (root / ".acl-applied").exists():
        completed = subprocess.run(
            [
                "icacls",
                str(root),
                "/inheritance:r",
                "/grant:r",
                f"{username}:(OI)(CI)F",
                "SYSTEM:(OI)(CI)F",
                "/T",
                "/C",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode:
            raise RuntimeError(f"cannot secure state ACL: {completed.stderr.strip()}")
        (root / ".acl-applied").write_text("restricted\n", encoding="ascii")
    _validate_acl(root)
    for sensitive in (root / "state", root / "state" / "auth.token", root / "state" / "daemon.db"):
        if sensitive.exists():
            _validate_acl(sensitive, allow_inherited=True)


def _validate_acl(root: Path, *, allow_inherited: bool = False) -> None:
    script = (
        "& { param([string]$p) "
        "$current=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
        "if ([IO.Directory]::Exists($p)) { "
        "$acl=(New-Object IO.DirectoryInfo($p)).GetAccessControl() "
        "} else { $acl=(New-Object IO.FileInfo($p)).GetAccessControl() }; "
        "$rules=@($acl.Access | ForEach-Object { "
        "$sid=$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value; "
        "[pscustomobject]@{sid=$sid;type=$_.AccessControlType.ToString();"
        "rights=$_.FileSystemRights.ToString();inherited=$_.IsInherited} }); "
        "[pscustomobject]@{current=$current;rules=$rules} | ConvertTo-Json -Depth 4 -Compress }"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script, str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode or completed.stderr.strip():
        raise RuntimeError("cannot validate state ACL")
    snapshot = json.loads(completed.stdout)
    rules = snapshot["rules"]
    if isinstance(rules, dict):
        rules = [rules]
    allowed_sids = {snapshot["current"], "S-1-5-18"}
    actual_sids = {rule["sid"] for rule in rules}
    invalid = (
        actual_sids != allowed_sids
        or any(rule["type"] != "Allow" or "FullControl" not in rule["rights"] for rule in rules)
        or (not allow_inherited and any(rule["inherited"] for rule in rules))
    )
    if invalid:
        raise RuntimeError(
            f'state ACL is overly broad; repair with: icacls "{root}" /inheritance:r'
        )


def load_or_create_token(root: Path) -> str:
    token_path = root / "state" / "auth.token"
    if token_path.exists():
        token = token_path.read_text(encoding="ascii").strip()
        if len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
            raise RuntimeError("auth.token is malformed")
        return token
    token = secrets.token_hex(32)
    temporary = token_path.with_suffix(".tmp")
    temporary.write_text(token, encoding="ascii")
    temporary.replace(token_path)
    return token


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = re.sub(r"(?i)\b[0-9a-f]{64}\b", "[REDACTED]", record.getMessage())
        payload = {
            "timestamp": datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "event": message.replace("\n", "\\n").replace("\r", "\\r"),
        }
        return json.dumps(payload, separators=(",", ":"))


class HealthTrackingRotatingHandler(RotatingFileHandler):
    healthy = True

    def handleError(self, record: logging.LogRecord) -> None:
        self.healthy = False
        super().handleError(record)


def configure_logging(root: Path) -> logging.Logger:
    logger = logging.getLogger("td_cli.daemon")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = HealthTrackingRotatingHandler(
        root / "logs" / "daemon.log", maxBytes=5 * 1024 * 1024, backupCount=4, encoding="utf-8"
    )
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger
