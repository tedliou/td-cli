import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from td_cli.operator_catalog import OPERATOR_CATALOG, OperatorCatalogError
from td_cli.protocol import Command


def _agent_catalog_types():
    spec = importlib.util.spec_from_file_location("catalog_parity_agent", "agent/extension.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.OperatorCatalog, module.AgentCommandError


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


@pytest.mark.parametrize(
    ("op_type", "allow_conditional", "expected"),
    [
        ("noiseTOP", False, "supported"),
        ("videodeviceinTOP", False, "operator_type_conditional"),
        ("videodeviceinTOP", True, "conditional"),
        ("audioenvelopeCHOP", False, "operator_type_unsupported"),
        ("futureTOP", False, "operator_type_unsupported"),
    ],
)
def test_host_and_embedded_agent_catalogs_have_identical_policy(
    op_type: str, allow_conditional: bool, expected: str
) -> None:
    manifest = json.loads(Path("agent/touchdesigner-2025.32050-operators.json").read_text())
    agent_catalog_type, agent_error_type = _agent_catalog_types()
    agent_catalog = agent_catalog_type(manifest)

    outcomes = []
    for catalog, error_type in (
        (OPERATOR_CATALOG, OperatorCatalogError),
        (agent_catalog, agent_error_type),
    ):
        try:
            outcomes.append(catalog.require_creatable(op_type, allow_conditional=allow_conditional))
        except error_type as error:
            outcomes.append(error.code)
    assert outcomes == [expected, expected]


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
