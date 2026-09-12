"""Windows launches owned by the desktop user, not the invoking shell's job."""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path


class LaunchError(RuntimeError):
    pass


def launch_detached(command: list[str], *, cwd: Path, hidden: bool, timeout: float = 10) -> int:
    """Use local Win32_Process.Create; never retry through another launcher.

    The WMI provider creates the process outside the caller job, including when
    the caller is in a restrictive nested job. Its return value is launch status,
    not application readiness. Only the fixed daemon and project-open callers
    select executable/arguments; this is not a public arbitrary-command interface.
    """
    if os.name != "nt":
        raise LaunchError("detached launch requires Windows")
    payload = base64.b64encode(
        json.dumps(
            {
                "command": subprocess.list2cmdline(command),
                "cwd": str(cwd),
                "show": 0 if hidden else 1,
                "flags": 0x09000000 if hidden else 0x01000008,
            }
        ).encode("utf-8")
    ).decode("ascii")
    script = (
        """$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"""
        + payload
        + """'))|ConvertFrom-Json
$s=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{
    ShowWindow=[uint16]$p.show;CreateFlags=[uint32]$p.flags}
$r=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine=[string]$p.command;CurrentDirectory=[string]$p.cwd;ProcessStartupInformation=$s}
$r|Select-Object ReturnValue,ProcessId|ConvertTo-Json -Compress
"""
    )
    try:
        result = subprocess.run(
            [
                str(
                    Path(os.environ["SystemRoot"])
                    / "System32/WindowsPowerShell/v1.0/powershell.exe"
                ),
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-EncodedCommand",
                base64.b64encode(script.encode("utf-16-le")).decode("ascii"),
            ],
            capture_output=True,
            encoding="utf-8-sig",
            errors="replace",
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise LaunchError(
            "launch outcome unknown after WMI timeout; inspect processes before retrying"
        ) from error
    if result.returncode:
        raise LaunchError("Windows process service rejected launch: " + result.stderr.strip())
    try:
        response = json.loads(result.stdout)
        code, pid = int(response["ReturnValue"]), int(response["ProcessId"] or 0)
    except (ValueError, KeyError, TypeError) as error:
        raise LaunchError(
            "unverifiable WMI launch response; inspect processes before retrying"
        ) from error
    if code != 0 or pid <= 0:
        raise LaunchError(f"Windows process service rejected launch (Win32_Process {code})")
    return pid
