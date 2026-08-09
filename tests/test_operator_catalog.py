import hashlib
import json
from pathlib import Path

import pytest

from td_cli.operator_catalog import OPERATOR_CATALOG, OperatorCatalogError
from td_cli.protocol import Command


def test_locked_operator_catalog_is_complete_and_matches_agent_source() -> None:
    source = Path("agent/touchdesigner-2025.32050-operators.json").read_bytes()
    packaged = Path("src/td_cli/data/touchdesigner-2025.32050-operators.json").read_bytes()
    assert hashlib.sha256(packaged).digest() == hashlib.sha256(source).digest()

    manifest = json.loads(packaged)
    assert len(OPERATOR_CATALOG.names) == 680
    assert OPERATOR_CATALOG.touchdesigner_build == "2025.32050"
    assert {entry["family"] for entry in manifest["operators"]} == {
        "COMP",
        "TOP",
        "CHOP",
        "POP",
        "DAT",
        "MAT",
        "SOP",
    }


def test_operator_catalog_enforces_supported_conditional_and_unsupported_statuses() -> None:
    assert OPERATOR_CATALOG.require_creatable("noiseTOP") == "supported"
    with pytest.raises(OperatorCatalogError, match="conditional"):
        OPERATOR_CATALOG.require_creatable("videodeviceinTOP")
    assert (
        OPERATOR_CATALOG.require_creatable("videodeviceinTOP", allow_conditional=True)
        == "conditional"
    )
    with pytest.raises(OperatorCatalogError, match="unsupported"):
        OPERATOR_CATALOG.require_creatable("audioenvelopeCHOP")
    with pytest.raises(OperatorCatalogError, match="unsupported"):
        OPERATOR_CATALOG.require_creatable("futureTOP")


def test_create_command_uses_locked_catalog_and_explicit_conditional_opt_in() -> None:
    supported = Command.model_validate(
        {
            "name": "ops.create",
            "input": {"parent_path": "/project1", "op_type": "boxSOP", "name": "box"},
        }
    )
    assert supported.input.model_dump()["allow_conditional"] is False

    conditional = Command.model_validate(
        {
            "name": "ops.create",
            "input": {
                "parent_path": "/project1",
                "op_type": "videodeviceinTOP",
                "name": "camera",
                "allow_conditional": True,
            },
        }
    )
    assert conditional.input.model_dump()["allow_conditional"] is True
