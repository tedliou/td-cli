"""The public error vocabulary is exactly what the CLI, Daemon and Agent can produce."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from td_cli import cli
from td_cli.error_catalog import ERROR_CATALOG

ROOT = Path(__file__).parents[1]
_CODE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
_CODE_CONSTRUCTORS = {"ClientError", "AdmissionRejected", "AgentCommandError"}
_AGENT_OUTCOME_CONSTRUCTORS = {"AgentCommandError", "_outcome_error"}


def _producer_sources() -> list[Path]:
    sources = [
        path for path in (ROOT / "src" / "td_cli").rglob("*.py") if path.name != "error_catalog.py"
    ]
    return [*sources, ROOT / "agent" / "extension.py", ROOT / "agent" / "socket_callbacks.py"]


class _Module:
    """Resolve the constant error codes an expression can evaluate to within one module."""

    def __init__(self, path: Path) -> None:
        self.tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        self.functions = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        ]

    def enclosing(self, target: ast.AST) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        owners = [
            function
            for function in self.functions
            if any(node is target for node in ast.walk(function))
        ]
        return min(owners, key=lambda function: len(list(ast.walk(function))), default=None)

    def values(self, node: ast.AST, scope: ast.AST | None, depth: int = 0) -> set[str]:
        if depth > 8:
            return set()
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.IfExp):
            return self.values(node.body, scope, depth + 1) | self.values(
                node.orelse, scope, depth + 1
            )
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Dict):
            return set().union(
                *(self.values(value, scope, depth + 1) for value in node.value.values)
            )
        if isinstance(node, ast.Name) and scope is not None:
            return set().union(
                *(
                    self.values(assignment.value, scope, depth + 1)
                    for assignment in ast.walk(scope)
                    if isinstance(assignment, ast.Assign)
                    and any(
                        isinstance(target, ast.Name) and target.id == node.id
                        for target in assignment.targets
                    )
                )
            )
        if isinstance(node, ast.JoinedStr):
            return self._template_values(node, scope, depth)
        return set()

    def _template_values(self, node: ast.JoinedStr, scope: ast.AST | None, depth: int) -> set[str]:
        results = {""}
        for part in node.values:
            if isinstance(part, ast.Constant):
                pieces = {str(part.value)}
            elif (
                isinstance(part, ast.FormattedValue)
                and isinstance(part.value, ast.Name)
                and isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef)
            ):
                pieces = self._parameter_values(scope, part.value.id, depth + 1)
            else:
                return set()
            results = {prefix + piece for prefix in results for piece in pieces}
        return results

    def _parameter_values(
        self, function: ast.FunctionDef | ast.AsyncFunctionDef, name: str, depth: int
    ) -> set[str]:
        parameters = [argument.arg for argument in function.args.args]
        if name not in parameters:
            return set()
        position = parameters.index(name) - int(parameters[:1] == ["self"])
        values: set[str] = set()
        for call in ast.walk(self.tree):
            if not isinstance(call, ast.Call) or _callee(call) != function.name:
                continue
            caller = self.enclosing(call)
            arguments = [keyword.value for keyword in call.keywords if keyword.arg == name]
            if not arguments and 0 <= position < len(call.args):
                arguments = [call.args[position]]
            for argument in arguments:
                if (
                    isinstance(argument, ast.Name)
                    and caller is not None
                    and argument.id in [item.arg for item in caller.args.args]
                ):
                    values |= self._parameter_values(caller, argument.id, depth + 1)
                else:
                    values |= self.values(argument, caller, depth + 1)
        return values


def _callee(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _public_code_expressions(node: ast.AST) -> list[ast.AST]:
    """Expressions that become a public error code at a producer site."""
    if isinstance(node, ast.Dict):
        return [
            value
            for key, value in zip(node.keys, node.values, strict=True)
            if isinstance(key, ast.Constant) and key.value == "code"
        ]
    if not isinstance(node, ast.Call):
        return []
    callee = _callee(node)
    catalog_error = (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "ERROR_CATALOG"
        and callee == "error"
    )
    if (callee in _CODE_CONSTRUCTORS or callee == "_outcome_error" or catalog_error) and node.args:
        return [node.args[0]]
    if callee == "HTTPException":
        return [keyword.value for keyword in node.keywords if keyword.arg == "detail"]
    if (
        callee == "get"
        and len(node.args) == 2
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value in {"code", "detail"}
    ):
        return [node.args[1]]
    return []


def _agent_outcome_expressions(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.Call) and _callee(node) in _AGENT_OUTCOME_CONSTRUCTORS and node.args:
        return [node.args[0]]
    return []


def _produced(path: Path, select=_public_code_expressions) -> set[str]:
    module = _Module(path)
    codes: set[str] = set()
    for node in ast.walk(module.tree):
        expressions = select(node)
        scope = module.enclosing(node) if expressions else None
        for expression in expressions:
            codes |= {code for code in module.values(expression, scope) if _CODE.match(code)}
    return codes


def test_catalog_matches_every_produced_error_code() -> None:
    produced = set().union(*(_produced(path) for path in _producer_sources()))

    assert sorted(produced - ERROR_CATALOG.codes) == [], "produced but not catalogued"
    assert sorted(ERROR_CATALOG.codes - produced) == [], "catalogued but never produced"


def test_only_codes_proving_the_command_never_started_are_retryable() -> None:
    retryable = {code for code in ERROR_CATALOG.codes if ERROR_CATALOG.retryable(code)}

    assert retryable == {
        "daemon_shutdown",
        "execution_capacity_full",
        "instance_busy",
        "instance_draining",
        "instance_offline",
        "instance_synchronizing",
    }
    assert not ERROR_CATALOG.retryable("future_error")


def test_agent_outcome_codes_are_never_retryable() -> None:
    agent = ROOT / "agent" / "extension.py"
    outcome_codes = _produced(agent, _agent_outcome_expressions)

    assert "outcome_capacity_exceeded" in outcome_codes
    assert {"text_dat_write_failed", "table_dat_outcome_unknown"} <= outcome_codes
    assert all(not ERROR_CATALOG.retryable(code) for code in outcome_codes)


def test_persisted_error_object_carries_catalogued_retry_safety() -> None:
    assert ERROR_CATALOG.error("daemon_shutdown") == {
        "code": "daemon_shutdown",
        "message": "daemon_shutdown",
        "details": {},
        "retryable": True,
    }
    assert ERROR_CATALOG.error("request_outcome_unknown")["retryable"] is False
    assert ERROR_CATALOG.error("future_error")["retryable"] is False


def test_cli_exit_codes_name_catalogued_errors() -> None:
    assert set(cli.EXIT_CODES) <= ERROR_CATALOG.codes
