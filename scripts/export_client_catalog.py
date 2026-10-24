"""Export what the native td client embeds: its Command schemas, defaults and error codes.

Python stays the authority for Commands and errors (ADR 0003); ``tests/test_client_catalog.py``
fails when ``native/td/catalog.json`` no longer matches this export. The ``cli`` command tree is
the native td's own: it is kept from the committed file, and changing a ``td`` command means
editing it there. The operator catalog is embedded from ``src/td_cli/data`` directly.

Run ``uv run python scripts/export_client_catalog.py`` after changing a Command or an error.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from td_cli import __version__
from td_cli.command_catalog import (
    COMMAND_CATALOG,
    MAX_DAT_CONTENT_BYTES,
    MAX_INSPECTION_ITEMS,
    MAX_TOX_FILE_BYTES,
)
from td_cli.error_catalog import DEFAULT_EXIT_CODE, ERROR_CATALOG, EXIT_CODES
from td_cli.protocol import PROTOCOL_VERSION, TERMINAL_REQUEST_STATUSES, RequestStatus

OUTPUT = Path(__file__).resolve().parents[1] / "native" / "td" / "catalog.json"


def _command_tree() -> dict[str, Any]:
    return json.loads(OUTPUT.read_text(encoding="utf-8"))["cli"]


def build_catalog() -> dict[str, Any]:
    return {
        "release_version": __version__,
        "protocol_version": PROTOCOL_VERSION,
        "cli": _command_tree(),
        "commands": {
            name: {
                "effect": COMMAND_CATALOG.effect(name).value,
                "batchable": name in COMMAND_CATALOG.batch_names,
                "input_schema": COMMAND_CATALOG.input_model(name).model_json_schema(),
            }
            for name in COMMAND_CATALOG.names
        },
        # Defaults the td commands put into a Command input when an option is left out.
        "input_defaults": {
            "max_dat_content_bytes": MAX_DAT_CONTENT_BYTES,
            "max_inspection_items": MAX_INSPECTION_ITEMS,
            "max_tox_file_bytes": MAX_TOX_FILE_BYTES,
        },
        "request_statuses": [status.value for status in RequestStatus],
        "terminal_request_statuses": sorted(status.value for status in TERMINAL_REQUEST_STATUSES),
        "errors": {
            "codes": sorted(ERROR_CATALOG.codes),
            "default_exit_code": DEFAULT_EXIT_CODE,
            "exit_codes": dict(sorted(EXIT_CODES.items())),
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
