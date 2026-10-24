"""Export what the native td client embeds: its command tree, Command schemas and error codes.

Python stays the authority (ADR 0003). The native client is built from ``native/td/catalog.json``,
and ``tests/test_client_catalog.py`` fails when that file no longer matches this export.
The operator catalog is embedded from ``src/td_cli/data`` directly, so it is not copied here.

Run ``uv run python scripts/export_client_catalog.py`` after changing a ``td`` command.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer.main

from td_cli import __version__, cli
from td_cli.command_catalog import COMMAND_CATALOG
from td_cli.error_catalog import ERROR_CATALOG
from td_cli.protocol import PROTOCOL_VERSION, TERMINAL_REQUEST_STATUSES, RequestStatus

OUTPUT = Path(__file__).resolve().parents[1] / "native" / "td" / "catalog.json"
# Typer adds shell-completion installers to every app; they are not part of the td contract.
_TYPER_ONLY_OPTIONS = {"install_completion", "show_completion"}
_DEFAULT_EXIT_CODE = 5


def _type(param_type: Any) -> dict[str, Any]:
    name = type(param_type).__name__
    if name == "IntRange":
        return {"name": "int", "min": param_type.min, "max": param_type.max}
    if name == "FloatRange":
        return {"name": "float", "min": param_type.min, "max": param_type.max}
    if name == "Tuple":
        return {"name": "tuple", "items": [_type(item) for item in param_type.types]}
    if name == "TyperPath":
        return {
            "name": "path",
            "exists": param_type.exists,
            "file_okay": param_type.file_okay,
            "dir_okay": param_type.dir_okay,
        }
    simple = {
        "StringParamType": "string",
        "BoolParamType": "bool",
        "IntParamType": "int",
        "FloatParamType": "float",
    }
    return {"name": simple[name]}


def _param(param: Any) -> dict[str, Any]:
    default = param.default
    if isinstance(default, tuple):
        default = list(default)
    if isinstance(default, Path):
        default = str(default)
    return {
        "kind": "argument" if type(param).__name__ == "TyperArgument" else "option",
        "name": param.name,
        "opts": list(param.opts),
        "secondary_opts": list(param.secondary_opts),
        "type": _type(param.type),
        "default": default,
        "required": param.required,
        "is_flag": bool(getattr(param, "is_flag", False)),
        "help": getattr(param, "help", None),
    }


def _node(command: Any) -> dict[str, Any]:
    params = [_param(param) for param in command.params if param.name not in _TYPER_ONLY_OPTIONS]
    node: dict[str, Any] = {"help": command.help, "params": params}
    if hasattr(command, "commands"):
        node["commands"] = {name: _node(sub) for name, sub in command.commands.items()}
    return node


def build_catalog() -> dict[str, Any]:
    return {
        "release_version": __version__,
        "protocol_version": PROTOCOL_VERSION,
        "cli": _node(typer.main.get_command(cli.app)),
        "commands": {
            name: {
                "effect": COMMAND_CATALOG.effect(name).value,
                "batchable": name in COMMAND_CATALOG.batch_names,
                "input_schema": COMMAND_CATALOG.input_model(name).model_json_schema(),
            }
            for name in COMMAND_CATALOG.names
        },
        "request_statuses": [status.value for status in RequestStatus],
        "terminal_request_statuses": sorted(status.value for status in TERMINAL_REQUEST_STATUSES),
        "errors": {
            "codes": sorted(ERROR_CATALOG.codes),
            "default_exit_code": _DEFAULT_EXIT_CODE,
            "exit_codes": dict(sorted(cli.EXIT_CODES.items())),
            "retryable": sorted(
                code for code in ERROR_CATALOG.codes if ERROR_CATALOG.retryable(code)
            ),
        },
    }


def render() -> str:
    return json.dumps(build_catalog(), indent=1, ensure_ascii=True) + "\n"


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render(), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
