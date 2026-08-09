"""Load and query the locked TouchDesigner Operator catalog."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any


class OperatorCatalogError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class OperatorCatalog:
    """Small interface over the probed create-support manifest."""

    def __init__(self, manifest: dict[str, Any]) -> None:
        self.touchdesigner_build = str(manifest["touchdesigner_build"])
        self._entries = {str(entry["op_type"]): entry for entry in manifest["operators"]}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    def entry(self, op_type: str) -> dict[str, Any] | None:
        return self._entries.get(op_type)

    def require_creatable(self, op_type: str, *, allow_conditional: bool = False) -> str:
        entry = self.entry(op_type)
        status = None if entry is None else entry.get("status")
        if status == "supported":
            return status
        if status == "conditional":
            if allow_conditional:
                return status
            raise OperatorCatalogError("operator_type_conditional")
        raise OperatorCatalogError("operator_type_unsupported")


def _load_catalog() -> OperatorCatalog:
    resource = files("td_cli.data").joinpath("touchdesigner-2025.32050-operators.json")
    return OperatorCatalog(json.loads(resource.read_text(encoding="utf-8")))


OPERATOR_CATALOG = _load_catalog()
