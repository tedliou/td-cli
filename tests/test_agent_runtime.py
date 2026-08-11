import builtins
import importlib.util
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from td_cli.command_catalog import (
    COMMAND_CATALOG,
    MAX_DAT_CONTENT_BYTES,
    MAX_TABLE_CELL_BYTES,
    MAX_TABLE_CELLS,
    MAX_TABLE_COLUMNS,
    MAX_TABLE_ROWS,
    OPERATOR_STATE_FIELDS,
    SetOperatorStateInput,
)

spec = importlib.util.spec_from_file_location("td_agent_extension", Path("agent/extension.py"))
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
AgentExt = module.AgentExt
OperatorControl = module.OperatorControl
OperatorCatalog = module.OperatorCatalog
RUNTIME_OPERATOR_CATALOG = OperatorCatalog(
    json.loads(Path("agent/touchdesigner-2025.32050-operators.json").read_text(encoding="utf-8"))
)


def make_control(operator_lookup):
    return OperatorControl(operator_lookup, RUNTIME_OPERATOR_CATALOG)


def test_operator_state_fields_match_between_canonical_and_agent_contracts() -> None:
    model_fields = tuple(
        field for field in SetOperatorStateInput.model_fields if field != "operator_path"
    )
    agent_fields = tuple(field for field, _, _ in OperatorControl.STATE_FIELDS)

    assert model_fields == OPERATOR_STATE_FIELDS
    assert agent_fields == OPERATOR_STATE_FIELDS


def test_dat_bounds_match_between_canonical_and_agent_contracts() -> None:
    assert OperatorControl.MAX_DAT_CONTENT_BYTES == MAX_DAT_CONTENT_BYTES
    assert OperatorControl.MAX_TABLE_ROWS == MAX_TABLE_ROWS
    assert OperatorControl.MAX_TABLE_COLUMNS == MAX_TABLE_COLUMNS
    assert OperatorControl.MAX_TABLE_CELLS == MAX_TABLE_CELLS
    assert OperatorControl.MAX_TABLE_CELL_BYTES == MAX_TABLE_CELL_BYTES


@pytest.fixture(autouse=True)
def isolated_touchdesigner_runtime():
    original_session = builtins._td_cli_runtime_session_id
    original_state = getattr(builtins, "_td_cli_agent_state", None)
    original_app = getattr(builtins, "app", None)
    builtins._td_cli_runtime_session_id = str(uuid.uuid4())
    builtins.app = SimpleNamespace(build="2025.32050")
    if hasattr(builtins, "_td_cli_agent_state"):
        del builtins._td_cli_agent_state
    try:
        yield
    finally:
        builtins._td_cli_runtime_session_id = original_session
        if original_state is None:
            if hasattr(builtins, "_td_cli_agent_state"):
                del builtins._td_cli_agent_state
        else:
            builtins._td_cli_agent_state = original_state
        if original_app is None:
            if hasattr(builtins, "app"):
                del builtins.app
        else:
            builtins.app = original_app


class FakeOwner:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.path = "/project1/td_agent"

    def fetch(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)

    def store(self, key: str, value: object) -> None:
        self.values[key] = value

    def op(self, name: str):
        if name == "operator_catalog":
            return SimpleNamespace(
                text=Path("agent/touchdesigner-2025.32050-operators.json").read_text(
                    encoding="utf-8"
                )
            )
        if name == "agent_manifest":
            return SimpleNamespace(text=Path("agent/manifest.json").read_text(encoding="utf-8"))
        return None


class FakeParameter:
    def __init__(self, value=None, *, mode="constant", read_only=False, pulseable=False) -> None:
        self.val = value
        self.expr = value if mode == "expression" else ""
        self.mode = mode
        self.readOnly = read_only
        self.isPulse = pulseable
        self.isPython = False
        self.isSequence = False
        self.isOP = False
        self.isMenu = False
        self.isToggle = type(value) is bool
        self.isInt = type(value) is int
        self.isFloat = type(value) is float
        self.isNumber = type(value) in {int, float}
        self.isString = type(value) is str
        self.style = "Pulse" if pulseable else type(value).__name__
        self.enable = True
        self.hidden = False
        self.pulses = 0

    def eval(self):
        return self.val

    def pulse(self) -> None:
        self.pulses += 1


class FakeConnector:
    def __init__(self, owner, index: int, *, is_input: bool) -> None:
        self.owner = owner
        self.index = index
        self.isInput = is_input
        self.isOutput = not is_input
        self.connections = []
        self.description = f"connector {index}"

    def connect(self, target) -> None:
        if self.isInput:
            self.connections.clear()
            self.connections.append(FakeConnector(target.owner, target.index, is_input=False))
            return
        self.connections.append(target)
        target.connections.append(FakeConnector(self.owner, self.index, is_input=False))

    def disconnect(self) -> None:
        self.connections.clear()


class FakeOperator:
    def __init__(
        self, path: str, *, op_type="base", family="COMP", inputs: int = 0, outputs: int = 0
    ) -> None:
        self.path = path
        self.name = path.rsplit("/", 1)[-1]
        self.OPType = op_type
        self.family = family
        self.children = []
        self.docked = []
        self.par = SimpleNamespace()
        self.nodeX = 0
        self.nodeY = 0
        self.nodeWidth = 130
        self.nodeHeight = 90
        self.color = (0.67, 0.67, 0.67)
        self.comment = ""
        self.bypass = False
        self.lock = False
        self.viewer = False
        self.expose = True
        self.inputConnectors = [
            FakeConnector(self, index, is_input=True) for index in range(inputs)
        ]
        self.outputConnectors = [
            FakeConnector(self, index, is_input=False) for index in range(outputs)
        ]
        self.destroyed = False

    def create(self, op_type: str, name: str):
        created = FakeOperator(f"{self.path}/{name}", op_type=op_type, family="TOP", outputs=1)
        if op_type != "constantTOP":
            created.inputConnectors = [FakeConnector(created, 0, is_input=True)]
        self.children.append(created)
        return created

    def saveByteArray(self, *_):
        return bytearray(b"TD-BINARY")

    def errors(self, recurse=False):
        assert recurse is True
        return ["sample error"]

    def destroy(self) -> None:
        self.destroyed = True
        for child in self.children:
            child.destroy()


class FakeDatParameter:
    def __init__(self, value) -> None:
        self.value = value

    def eval(self):
        return self.value


class FakeCell:
    def __init__(self, table, row: int, column: int) -> None:
        self.table = table
        self.row = row
        self.column = column

    @property
    def val(self):
        return self.table._rows[self.row][self.column]

    @val.setter
    def val(self, value):
        self.table._rows[self.row][self.column] = str(value)


class FakeTextDat(FakeOperator):
    def __init__(self, path: str, text: str = "", *, file="", syncfile=False) -> None:
        super().__init__(path, op_type="textDAT", family="DAT")
        self.text = text
        self.par = SimpleNamespace(file=FakeDatParameter(file), syncfile=FakeDatParameter(syncfile))


class FakeTableDat(FakeOperator):
    def __init__(self, path: str, rows=None, *, file="", syncfile=False) -> None:
        super().__init__(path, op_type="tableDAT", family="DAT")
        self._rows = [list(row) for row in (rows or [])]
        self.par = SimpleNamespace(file=FakeDatParameter(file), syncfile=FakeDatParameter(syncfile))

    @property
    def numRows(self):
        return len(self._rows)

    @property
    def numCols(self):
        return len(self._rows[0]) if self._rows else 0

    def __getitem__(self, indexes):
        row, column = indexes
        return FakeCell(self, row, column)

    def clear(self):
        self._rows = []

    def setSize(self, rows: int, columns: int):
        self._rows = [["" for _ in range(columns)] for _ in range(rows)]


def test_operator_control_is_the_touchdesigner_graph_interface() -> None:
    root = FakeOperator("/project1")
    control = make_control({root.path: root}.get)

    assert control.execute({"name": "ops.get", "input": {"operator_path": root.path}}) == {
        "path": "/project1",
        "name": "project1",
        "op_type": "base",
        "family": "COMP",
    }
    with pytest.raises(module.AgentCommandError, match="operator_not_found"):
        control.execute({"name": "ops.get", "input": {"operator_path": "/missing"}})


def test_operator_state_get_returns_only_the_locked_common_state_contract() -> None:
    operator = FakeOperator("/project1/source", family="TOP")
    operator.nodeX = -10
    operator.nodeY = 20
    operator.nodeWidth = 140
    operator.nodeHeight = 80
    operator.color = (0.1, 0.2, 0.3)
    operator.comment = "source node"
    operator.bypass = True
    operator.viewer = True

    assert make_control({operator.path: operator}.get).execute(
        {"name": "ops.state.get", "input": {"operator_path": operator.path}}
    ) == {
        "operator_path": "/project1/source",
        "state": {
            "node_x": -10,
            "node_y": 20,
            "node_width": 140,
            "node_height": 80,
            "color": {"red": 0.1, "green": 0.2, "blue": 0.3},
            "comment": "source node",
            "bypass": True,
            "lock": False,
            "viewer": True,
            "expose": True,
        },
    }


def test_operator_state_commands_distinguish_temporarily_unavailable_state() -> None:
    class UnavailableStateOperator(FakeOperator):
        @property
        def color(self):
            raise RuntimeError("state is temporarily unavailable")

        @color.setter
        def color(self, value):
            del value

    operator = UnavailableStateOperator("/project1/source", family="TOP")
    control = make_control({operator.path: operator}.get)

    with pytest.raises(module.AgentCommandError, match="operator_state_unavailable"):
        control.execute({"name": "ops.state.get", "input": {"operator_path": operator.path}})
    with pytest.raises(module.AgentCommandError, match="operator_state_unavailable"):
        control.execute(
            {
                "name": "ops.state.set",
                "input": {"operator_path": operator.path, "comment": "safe patch"},
            }
        )


def test_operator_state_set_applies_and_returns_the_exact_verified_patch() -> None:
    operator = FakeOperator("/project1/source", family="TOP")
    control = make_control({operator.path: operator}.get)

    result = control.execute(
        {
            "name": "ops.state.set",
            "input": {
                "operator_path": operator.path,
                "node_x": -10,
                "node_y": 20,
                "node_width": 140,
                "node_height": 80,
                "color": {"red": 0.1, "green": 0.2, "blue": 0.3},
                "comment": "source node",
                "bypass": True,
                "lock": True,
                "viewer": True,
                "expose": False,
            },
        }
    )

    assert result == {
        "operator_path": "/project1/source",
        "applied_fields": [
            "node_x",
            "node_y",
            "node_width",
            "node_height",
            "color",
            "comment",
            "bypass",
            "viewer",
            "expose",
            "lock",
        ],
        "state": {
            "node_x": -10,
            "node_y": 20,
            "node_width": 140,
            "node_height": 80,
            "color": {"red": 0.1, "green": 0.2, "blue": 0.3},
            "comment": "source node",
            "bypass": True,
            "lock": True,
            "viewer": True,
            "expose": False,
        },
    }


def test_operator_state_set_rolls_back_the_whole_patch_when_touchdesigner_clamps() -> None:
    class WidthClampingOperator(FakeOperator):
        @property
        def nodeWidth(self):
            return self._node_width

        @nodeWidth.setter
        def nodeWidth(self, value):
            self._node_width = max(54, value)

    operator = WidthClampingOperator("/project1/source", family="TOP")
    control = make_control({operator.path: operator}.get)

    with pytest.raises(module.AgentCommandError, match="operator_state_failed"):
        control.execute(
            {
                "name": "ops.state.set",
                "input": {
                    "operator_path": operator.path,
                    "node_x": 123,
                    "node_width": 1,
                },
            }
        )

    assert operator.nodeX == 0
    assert operator.nodeWidth == 130


def test_operator_state_set_reports_a_distinct_rollback_failure() -> None:
    class RollbackRejectingOperator(FakeOperator):
        reject_comment = False

        @property
        def comment(self):
            return self._comment

        @comment.setter
        def comment(self, value):
            if self.reject_comment:
                raise RuntimeError("comment write rejected")
            self._comment = value

    operator = RollbackRejectingOperator("/project1/source", family="TOP")
    operator.reject_comment = True

    with pytest.raises(module.AgentCommandError, match="operator_state_rollback_failed"):
        make_control({operator.path: operator}.get).execute(
            {
                "name": "ops.state.set",
                "input": {
                    "operator_path": operator.path,
                    "node_x": 123,
                    "comment": "cannot apply",
                },
            }
        )


def test_operator_state_set_reports_unknown_when_the_operator_disappears() -> None:
    class DisappearingOperator(FakeOperator):
        disappear_on_node_x = False

        @property
        def nodeX(self):
            return self._node_x

        @nodeX.setter
        def nodeX(self, value):
            if self.disappear_on_node_x:
                self.destroyed = True
                raise RuntimeError("operator disappeared")
            self._node_x = value

    operator = DisappearingOperator("/project1/source", family="TOP")
    operator.disappear_on_node_x = True
    lookup = lambda path: operator if path == operator.path and not operator.destroyed else None

    with pytest.raises(module.AgentCommandError, match="operator_state_outcome_unknown"):
        make_control(lookup).execute(
            {
                "name": "ops.state.set",
                "input": {"operator_path": operator.path, "node_x": 123},
            }
        )


@pytest.mark.parametrize(
    "path", ["/", "/project1", "/project1/td_agent", "/project1/td_agent/internal"]
)
def test_operator_state_set_protects_root_and_the_whole_agent_component_tree(path: str) -> None:
    operator = FakeOperator(path)
    control = OperatorControl(
        lambda candidate: operator if candidate == path else None,
        RUNTIME_OPERATOR_CATALOG,
        protected_path="/project1/td_agent",
    )

    with pytest.raises(module.AgentCommandError, match="operator_mutation_forbidden"):
        control.execute(
            {
                "name": "ops.state.set",
                "input": {"operator_path": path, "comment": "forbidden"},
            }
        )


def test_text_dat_get_is_lossless_bounded_and_exactly_typed() -> None:
    text_dat = FakeTextDat("/project1/notes", "繁體\n")
    result = make_control({text_dat.path: text_dat}.get).execute(
        {"name": "dat.text.get", "input": {"operator_path": text_dat.path, "max_bytes": 32}}
    )

    assert result == {
        "operator_path": text_dat.path,
        "dat_kind": "text",
        "text": "繁體\n",
        "utf8_bytes": 7,
    }

    with pytest.raises(module.AgentCommandError, match="result_too_large"):
        make_control({text_dat.path: text_dat}.get).execute(
            {"name": "dat.text.get", "input": {"operator_path": text_dat.path, "max_bytes": 6}}
        )
    wrong = FakeOperator("/project1/not_text", op_type="executeDAT", family="DAT")
    with pytest.raises(module.AgentCommandError, match="dat_type_mismatch"):
        make_control({wrong.path: wrong}.get).execute(
            {"name": "dat.text.get", "input": {"operator_path": wrong.path, "max_bytes": 32}}
        )


def test_text_dat_set_verifies_and_rolls_back_the_complete_content() -> None:
    class CorrectingTextDat(FakeTextDat):
        correct = False

        @property
        def text(self):
            return self._text

        @text.setter
        def text(self, value):
            self._text = value.upper() if self.correct and value != "before" else value

    text_dat = CorrectingTextDat("/project1/notes", "before")
    text_dat.correct = True

    with pytest.raises(module.AgentCommandError, match="text_dat_write_failed"):
        make_control({text_dat.path: text_dat}.get).execute(
            {"name": "dat.text.set", "input": {"operator_path": text_dat.path, "text": "after"}}
        )
    assert text_dat.text == "before"


@pytest.mark.parametrize("file,syncfile", [("notes.txt", False), ("", True)])
def test_text_dat_set_rejects_external_file_modes(file: str, syncfile: bool) -> None:
    text_dat = FakeTextDat("/project1/notes", "before", file=file, syncfile=syncfile)
    with pytest.raises(module.AgentCommandError, match="dat_content_not_writable"):
        make_control({text_dat.path: text_dat}.get).execute(
            {"name": "dat.text.set", "input": {"operator_path": text_dat.path, "text": "after"}}
        )
    assert text_dat.text == "before"


def test_text_dat_set_reports_distinct_rollback_and_unknown_outcomes() -> None:
    class RollbackRejectingTextDat(FakeTextDat):
        @property
        def text(self):
            return self._text

        @text.setter
        def text(self, value):
            if getattr(self, "reject_before", False) and value == "before":
                raise RuntimeError("rollback rejected")
            self._text = str(value).upper() if getattr(self, "correct", False) else value

    rollback = RollbackRejectingTextDat("/project1/rollback", "before")
    rollback.correct = True
    rollback.reject_before = True
    with pytest.raises(module.AgentCommandError, match="text_dat_rollback_failed"):
        make_control({rollback.path: rollback}.get).execute(
            {"name": "dat.text.set", "input": {"operator_path": rollback.path, "text": "after"}}
        )

    class DisappearingTextDat(FakeTextDat):
        @property
        def text(self):
            return self._text

        @text.setter
        def text(self, value):
            self._text = str(value).upper() if getattr(self, "correct", False) else value

    disappearing = DisappearingTextDat("/project1/disappearing", "before")
    disappearing.correct = True
    alive = True

    def lookup(path):
        nonlocal alive
        if path != disappearing.path or not alive:
            return None
        alive = False
        return disappearing

    with pytest.raises(module.AgentCommandError, match="text_dat_outcome_unknown"):
        make_control(lookup).execute(
            {
                "name": "dat.text.set",
                "input": {"operator_path": disappearing.path, "text": "after"},
            }
        )


def test_table_dat_get_returns_an_explicit_bounded_window_and_dimensions() -> None:
    table = FakeTableDat("/project1/grid", [["a", "b", "c"], ["甲", "", "丙"]])
    result = make_control({table.path: table}.get).execute(
        {
            "name": "dat.table.get",
            "input": {
                "operator_path": table.path,
                "row_offset": 1,
                "column_offset": 1,
                "row_count": 4,
                "column_count": 4,
                "max_bytes": 32,
            },
        }
    )

    assert result == {
        "operator_path": table.path,
        "dat_kind": "table",
        "total_rows": 2,
        "total_columns": 3,
        "row_offset": 1,
        "column_offset": 1,
        "rows": [["", "丙"]],
        "utf8_bytes": 3,
    }

    oversized_cell = FakeTableDat("/project1/large", [["x" * (MAX_TABLE_CELL_BYTES + 1)]])
    with pytest.raises(module.AgentCommandError, match="result_too_large"):
        make_control({oversized_cell.path: oversized_cell}.get).execute(
            {
                "name": "dat.table.get",
                "input": {
                    "operator_path": oversized_cell.path,
                    "row_offset": 0,
                    "column_offset": 0,
                    "row_count": 1,
                    "column_count": 1,
                    "max_bytes": MAX_DAT_CONTENT_BYTES,
                },
            }
        )


def test_table_dat_replace_and_patch_return_exact_verified_complete_state() -> None:
    table = FakeTableDat("/project1/grid", [["old"]])
    control = make_control({table.path: table}.get)

    replaced = control.execute(
        {
            "name": "dat.table.replace",
            "input": {"operator_path": table.path, "rows": [["a", "b"], ["c", "d"]]},
        }
    )
    patched = control.execute(
        {
            "name": "dat.table.patch",
            "input": {
                "operator_path": table.path,
                "row_offset": 0,
                "column_offset": 1,
                "rows": [["甲"], [""]],
            },
        }
    )

    assert replaced["rows"] == [["a", "b"], ["c", "d"]]
    assert patched["rows"] == [["a", "甲"], ["c", ""]]
    assert patched["total_rows"] == 2
    assert patched["total_columns"] == 2

    cleared = control.execute(
        {
            "name": "dat.table.replace",
            "input": {"operator_path": table.path, "rows": []},
        }
    )
    assert cleared["rows"] == []
    assert cleared["total_rows"] == 0
    assert cleared["total_columns"] == 0


@pytest.mark.parametrize(
    "path", ["/", "/project1", "/project1/td_agent", "/project1/td_agent/internal"]
)
def test_dat_mutations_protect_root_and_the_whole_agent_component_tree(path: str) -> None:
    text_dat = FakeTextDat(path, "before")
    control = OperatorControl(
        lambda candidate: text_dat if candidate == path else None,
        RUNTIME_OPERATOR_CATALOG,
        protected_path="/project1/td_agent",
    )
    with pytest.raises(module.AgentCommandError, match="operator_mutation_forbidden"):
        control.execute({"name": "dat.text.set", "input": {"operator_path": path, "text": "after"}})


def test_table_dat_mutations_reject_external_file_modes() -> None:
    table = FakeTableDat("/project1/grid", [["before"]], file="grid.csv", syncfile=True)
    with pytest.raises(module.AgentCommandError, match="dat_content_not_writable"):
        make_control({table.path: table}.get).execute(
            {
                "name": "dat.table.replace",
                "input": {"operator_path": table.path, "rows": [["after"]]},
            }
        )
    assert table._rows == [["before"]]


@pytest.mark.parametrize("mode", ["locked", "replicated", "cloned"])
def test_dat_mutations_reject_read_only_and_generated_modes(mode: str) -> None:
    table = FakeTableDat("/project1/generated/grid", [["before"]])
    if mode == "locked":
        table.lock = True
    elif mode == "replicated":
        table.replicator = object()
    else:
        clone_parent = SimpleNamespace(
            par=SimpleNamespace(clone=FakeDatParameter("/project1/template")),
            parent=lambda: None,
        )
        table.parent = lambda: clone_parent
    with pytest.raises(module.AgentCommandError, match="dat_content_not_writable"):
        make_control({table.path: table}.get).execute(
            {
                "name": "dat.table.replace",
                "input": {"operator_path": table.path, "rows": [["after"]]},
            }
        )
    assert table._rows == [["before"]]


def test_dat_mutation_reports_unknown_if_operator_disappears_during_rollback() -> None:
    class VanishingRollbackTextDat(FakeTextDat):
        @property
        def text(self):
            return self._text

        @text.setter
        def text(self, value):
            if getattr(self, "restoring", False) and value == "before":
                self.alive = False
                raise RuntimeError("operator vanished")
            self._text = str(value).upper() if getattr(self, "correct", False) else value

    text = VanishingRollbackTextDat("/project1/notes", "before")
    text.alive = True
    text.correct = True
    text.restoring = True
    lookup = lambda path: text if path == text.path and text.alive else None
    with pytest.raises(module.AgentCommandError, match="text_dat_outcome_unknown"):
        make_control(lookup).execute(
            {"name": "dat.text.set", "input": {"operator_path": text.path, "text": "after"}}
        )


def test_dat_mutations_reject_unbounded_prior_content_before_writing() -> None:
    text = FakeTextDat("/project1/notes", "x" * (MAX_DAT_CONTENT_BYTES + 1))
    with pytest.raises(module.AgentCommandError, match="dat_content_too_large"):
        make_control({text.path: text}.get).execute(
            {"name": "dat.text.set", "input": {"operator_path": text.path, "text": "small"}}
        )
    assert len(text.text) == MAX_DAT_CONTENT_BYTES + 1

    table = FakeTableDat("/project1/grid", [[""] for _ in range(MAX_TABLE_ROWS + 1)])
    with pytest.raises(module.AgentCommandError, match="dat_content_too_large"):
        make_control({table.path: table}.get).execute(
            {
                "name": "dat.table.replace",
                "input": {"operator_path": table.path, "rows": [["small"]]},
            }
        )
    assert table.numRows == MAX_TABLE_ROWS + 1


def test_table_dat_patch_rejects_resize_and_replace_rolls_back_all_dimensions() -> None:
    class CorrectingTableDat(FakeTableDat):
        correct = False

        def __getitem__(self, indexes):
            cell = super().__getitem__(indexes)
            if not self.correct:
                return cell

            class CorrectingCell:
                @property
                def val(self):
                    return cell.val

                @val.setter
                def val(self, value):
                    value = str(value)
                    cell.val = value.upper() if value not in {"old", "state"} else value

            return CorrectingCell()

    table = CorrectingTableDat("/project1/grid", [["old", "state"]])
    control = make_control({table.path: table}.get)
    with pytest.raises(module.AgentCommandError, match="table_dat_patch_out_of_bounds"):
        control.execute(
            {
                "name": "dat.table.patch",
                "input": {
                    "operator_path": table.path,
                    "row_offset": 1,
                    "column_offset": 0,
                    "rows": [["x"]],
                },
            }
        )

    table.correct = True
    with pytest.raises(module.AgentCommandError, match="table_dat_write_failed"):
        control.execute(
            {
                "name": "dat.table.replace",
                "input": {"operator_path": table.path, "rows": [["new"], ["rows"]]},
            }
        )
    assert table._rows == [["old", "state"]]


def test_connections_inventory_reports_every_regular_connector_and_exact_endpoint() -> None:
    source = FakeOperator("/project1/source", family="TOP", outputs=2)
    target = FakeOperator("/project1/target", family="TOP", inputs=2)
    source.outputConnectors[1].connect(target.inputConnectors[0])
    operators = {item.path: item for item in (source, target)}

    result = make_control(operators.get).execute(
        {
            "name": "ops.connections",
            "input": {"operator_path": source.path, "max_connections": 8},
        }
    )

    assert result == {
        "operator_path": "/project1/source",
        "inputs": [],
        "outputs": [
            {"output_index": 0, "description": "connector 0", "connections": []},
            {
                "output_index": 1,
                "description": "connector 1",
                "connections": [{"target_path": "/project1/target", "input_index": 0}],
            },
        ],
        "connection_count": 1,
    }

    target_result = make_control(operators.get).execute(
        {
            "name": "ops.connections",
            "input": {"operator_path": target.path, "max_connections": 8},
        }
    )
    assert target_result["inputs"] == [
        {
            "input_index": 0,
            "description": "connector 0",
            "connection": {"source_path": "/project1/source", "output_index": 1},
        },
        {"input_index": 1, "description": "connector 1", "connection": None},
    ]


def test_connections_inventory_rejects_an_overflow_instead_of_truncating() -> None:
    source = FakeOperator("/project1/source", family="TOP", outputs=1)
    targets = [FakeOperator(f"/project1/target{i}", family="TOP", inputs=1) for i in range(2)]
    for target in targets:
        source.outputConnectors[0].connect(target.inputConnectors[0])

    with pytest.raises(module.AgentCommandError, match="result_too_large"):
        make_control({source.path: source}.get).execute(
            {
                "name": "ops.connections",
                "input": {"operator_path": source.path, "max_connections": 1},
            }
        )


def test_destroy_requires_explicit_subtree_and_connection_authorization() -> None:
    root = FakeOperator("/project1/group")
    child = FakeOperator("/project1/group/child", family="TOP", outputs=1)
    target = FakeOperator("/project1/group/target", family="TOP", inputs=1)
    child.outputConnectors[0].connect(target.inputConnectors[0])
    root.children = [child, target]
    operators = {item.path: item for item in (root, child, target)}
    lookup = lambda path: (
        operators.get(path)
        if operators.get(path) is not None and not operators[path].destroyed
        else None
    )
    control = OperatorControl(lookup, RUNTIME_OPERATOR_CATALOG, protected_path="/project1/td_agent")

    with pytest.raises(module.AgentCommandError, match="operator_not_empty"):
        control.execute(
            {
                "name": "ops.destroy",
                "input": {
                    "operator_path": root.path,
                    "recursive": False,
                    "allow_connected": False,
                    "max_operators": 10,
                },
            }
        )
    with pytest.raises(module.AgentCommandError, match="operator_connected"):
        control.execute(
            {
                "name": "ops.destroy",
                "input": {
                    "operator_path": root.path,
                    "recursive": True,
                    "allow_connected": False,
                    "max_operators": 10,
                },
            }
        )

    assert control.execute(
        {
            "name": "ops.destroy",
            "input": {
                "operator_path": root.path,
                "recursive": True,
                "allow_connected": True,
                "max_operators": 10,
            },
        }
    ) == {
        "operator_path": "/project1/group",
        "operator_count": 3,
        "detached_connections": [
            {
                "source_path": "/project1/group/child",
                "output_index": 0,
                "target_path": "/project1/group/target",
                "input_index": 0,
            }
        ],
        "destroyed": True,
    }


@pytest.mark.parametrize("path", ["/", "/project1", "/project1/td_agent"])
def test_destroy_protects_root_agent_component_and_its_ancestors(path: str) -> None:
    operator = FakeOperator(path)
    control = OperatorControl(
        lambda candidate: operator if candidate == path else None,
        RUNTIME_OPERATOR_CATALOG,
        protected_path="/project1/td_agent",
    )

    with pytest.raises(module.AgentCommandError, match="operator_mutation_forbidden"):
        control.execute(
            {
                "name": "ops.destroy",
                "input": {
                    "operator_path": path,
                    "recursive": True,
                    "allow_connected": True,
                    "max_operators": 10,
                },
            }
        )


def test_copy_uses_an_exact_name_and_reports_unreplicated_boundary_connections() -> None:
    source = FakeOperator("/project1/source", op_type="constant", family="TOP", outputs=1)
    downstream = FakeOperator("/project1/downstream", family="TOP", inputs=1)
    target_parent = FakeOperator("/project1/group")
    source.outputConnectors[0].connect(downstream.inputConnectors[0])
    operators = {item.path: item for item in (source, downstream, target_parent)}

    def copy(source_operator, *, name, includeDocked):
        assert includeDocked is False
        created = FakeOperator(
            f"{target_parent.path}/{name}",
            op_type=source_operator.OPType,
            family=source_operator.family,
            outputs=len(source_operator.outputConnectors),
        )
        operators[created.path] = created
        target_parent.children.append(created)
        return created

    target_parent.copy = copy
    lookup = lambda path: (
        operators.get(path)
        if operators.get(path) is not None and not operators[path].destroyed
        else None
    )
    control = OperatorControl(lookup, RUNTIME_OPERATOR_CATALOG, protected_path="/project1/td_agent")

    assert control.execute(
        {
            "name": "ops.copy",
            "input": {
                "source_path": source.path,
                "target_parent_path": target_parent.path,
                "new_name": "copy",
                "node_x": 10,
                "node_y": 20,
                "include_docked": False,
                "max_operators": 10,
            },
        }
    ) == {
        "source_path": "/project1/source",
        "path": "/project1/group/copy",
        "name": "copy",
        "op_type": "constant",
        "family": "TOP",
        "operator_count": 1,
        "include_docked": False,
        "unreplicated_connections": [
            {
                "source_path": "/project1/source",
                "output_index": 0,
                "target_path": "/project1/downstream",
                "input_index": 0,
            }
        ],
    }
    assert operators["/project1/group/copy"].nodeX == 10
    assert operators["/project1/group/copy"].nodeY == 20


def test_copy_rolls_back_a_child_created_before_touchdesigner_raises() -> None:
    source = FakeOperator("/project1/source", family="TOP")
    target_parent = FakeOperator("/project1/group")
    operators = {item.path: item for item in (source, target_parent)}

    def copy(*_, **__):
        partial = FakeOperator("/project1/group/copy", family="TOP")
        operators[partial.path] = partial
        target_parent.children.append(partial)
        raise RuntimeError("TouchDesigner raised after creating the copy")

    target_parent.copy = copy
    lookup = lambda path: (
        operators.get(path)
        if operators.get(path) is not None and not operators[path].destroyed
        else None
    )

    with pytest.raises(module.AgentCommandError, match="operator_copy_failed"):
        OperatorControl(
            lookup, RUNTIME_OPERATOR_CATALOG, protected_path="/project1/td_agent"
        ).execute(
            {
                "name": "ops.copy",
                "input": {
                    "source_path": source.path,
                    "target_parent_path": target_parent.path,
                    "new_name": "copy",
                    "node_x": None,
                    "node_y": None,
                    "include_docked": False,
                    "max_operators": 10,
                },
            }
        )

    assert lookup("/project1/group/copy") is None


def test_copy_rejects_unverified_placement_and_rolls_back() -> None:
    source = FakeOperator("/project1/source", family="TOP")
    target_parent = FakeOperator("/project1/group")
    operators = {item.path: item for item in (source, target_parent)}

    class PlacementIgnoringOperator(FakeOperator):
        @property
        def nodeX(self):
            return 0

        @nodeX.setter
        def nodeX(self, value):
            del value

    def copy(source_operator, *, name, includeDocked):
        del includeDocked
        created = PlacementIgnoringOperator(
            f"{target_parent.path}/{name}",
            op_type=source_operator.OPType,
            family=source_operator.family,
        )
        operators[created.path] = created
        target_parent.children.append(created)
        return created

    target_parent.copy = copy
    lookup = lambda path: (
        operators.get(path)
        if operators.get(path) is not None and not operators[path].destroyed
        else None
    )

    with pytest.raises(module.AgentCommandError, match="operator_copy_failed"):
        OperatorControl(
            lookup, RUNTIME_OPERATOR_CATALOG, protected_path="/project1/td_agent"
        ).execute(
            {
                "name": "ops.copy",
                "input": {
                    "source_path": source.path,
                    "target_parent_path": target_parent.path,
                    "new_name": "copy",
                    "node_x": 10,
                    "node_y": None,
                    "include_docked": False,
                    "max_operators": 10,
                },
            }
        )

    assert lookup("/project1/group/copy") is None


def test_copy_requires_explicit_authorization_for_externally_docked_operators() -> None:
    source = FakeOperator("/project1/source", family="TOP")
    source.docked = [FakeOperator("/project1/source_callbacks", family="DAT")]
    target_parent = FakeOperator("/project1/group")
    operators = {item.path: item for item in (source, target_parent)}

    def copy(source_operator, *, name, includeDocked):
        assert includeDocked is True
        created = FakeOperator(
            f"{target_parent.path}/{name}",
            op_type=source_operator.OPType,
            family=source_operator.family,
        )
        operators[created.path] = created
        target_parent.children.append(created)
        return created

    target_parent.copy = copy
    control = OperatorControl(
        operators.get, RUNTIME_OPERATOR_CATALOG, protected_path="/project1/td_agent"
    )
    payload = {
        "source_path": source.path,
        "target_parent_path": target_parent.path,
        "new_name": "copy",
        "node_x": None,
        "node_y": None,
        "include_docked": False,
        "max_operators": 10,
    }

    with pytest.raises(module.AgentCommandError, match="operator_docked"):
        control.execute({"name": "ops.copy", "input": payload})

    payload["include_docked"] = True
    assert control.execute({"name": "ops.copy", "input": payload})["path"] == (
        "/project1/group/copy"
    )


def test_move_copies_then_destroys_and_reports_detached_boundary_connections() -> None:
    source = FakeOperator("/project1/source", op_type="constant", family="TOP", outputs=1)
    downstream = FakeOperator("/project1/downstream", family="TOP", inputs=1)
    target_parent = FakeOperator("/project1/group")
    source.outputConnectors[0].connect(downstream.inputConnectors[0])
    operators = {item.path: item for item in (source, downstream, target_parent)}

    def copy(source_operator, *, name, includeDocked):
        assert includeDocked is False
        created = FakeOperator(
            f"{target_parent.path}/{name}",
            op_type=source_operator.OPType,
            family=source_operator.family,
            outputs=len(source_operator.outputConnectors),
        )
        operators[created.path] = created
        target_parent.children.append(created)
        return created

    target_parent.copy = copy
    lookup = lambda path: (
        operators.get(path)
        if operators.get(path) is not None and not operators[path].destroyed
        else None
    )
    control = OperatorControl(lookup, RUNTIME_OPERATOR_CATALOG, protected_path="/project1/td_agent")
    payload = {
        "source_path": source.path,
        "target_parent_path": target_parent.path,
        "new_name": "moved",
        "node_x": None,
        "node_y": None,
        "allow_connected": False,
        "max_operators": 10,
    }

    with pytest.raises(module.AgentCommandError, match="operator_connected"):
        control.execute({"name": "ops.move", "input": payload})

    result = control.execute({"name": "ops.move", "input": {**payload, "allow_connected": True}})
    assert result == {
        "old_path": "/project1/source",
        "path": "/project1/group/moved",
        "name": "moved",
        "op_type": "constant",
        "family": "TOP",
        "operator_count": 1,
        "detached_connections": [
            {
                "source_path": "/project1/source",
                "output_index": 0,
                "target_path": "/project1/downstream",
                "input_index": 0,
            }
        ],
        "identity_preserved": False,
        "moved": True,
    }
    assert lookup("/project1/source") is None
    assert lookup("/project1/group/moved") is not None


def test_copy_rolls_back_an_inexact_touchdesigner_result() -> None:
    source = FakeOperator("/project1/source", family="TOP")
    target_parent = FakeOperator("/project1/group")
    operators = {item.path: item for item in (source, target_parent)}

    def copy(source_operator, *, name, includeDocked):
        created = FakeOperator(
            f"{target_parent.path}/{name}1",
            op_type=source_operator.OPType,
            family=source_operator.family,
        )
        operators[created.path] = created
        target_parent.children.append(created)
        return created

    target_parent.copy = copy
    lookup = lambda path: (
        operators.get(path)
        if operators.get(path) is not None and not operators[path].destroyed
        else None
    )
    control = OperatorControl(lookup, RUNTIME_OPERATOR_CATALOG)

    with pytest.raises(module.AgentCommandError, match="operator_copy_failed"):
        control.execute(
            {
                "name": "ops.copy",
                "input": {
                    "source_path": source.path,
                    "target_parent_path": target_parent.path,
                    "new_name": "copy",
                    "node_x": None,
                    "node_y": None,
                    "include_docked": False,
                    "max_operators": 10,
                },
            }
        )
    assert lookup("/project1/group/copy1") is None


def test_move_rolls_back_the_copy_when_source_deletion_fails() -> None:
    source = FakeOperator("/project1/source", family="TOP")
    target_parent = FakeOperator("/project1/group")
    operators = {item.path: item for item in (source, target_parent)}

    def copy(source_operator, *, name, includeDocked):
        created = FakeOperator(
            f"{target_parent.path}/{name}",
            op_type=source_operator.OPType,
            family=source_operator.family,
        )
        operators[created.path] = created
        target_parent.children.append(created)
        return created

    def fail_destroy():
        raise RuntimeError("destroy failed")

    target_parent.copy = copy
    source.destroy = fail_destroy
    lookup = lambda path: (
        operators.get(path)
        if operators.get(path) is not None and not operators[path].destroyed
        else None
    )
    control = OperatorControl(lookup, RUNTIME_OPERATOR_CATALOG)

    with pytest.raises(module.AgentCommandError, match="operator_move_failed"):
        control.execute(
            {
                "name": "ops.move",
                "input": {
                    "source_path": source.path,
                    "target_parent_path": target_parent.path,
                    "new_name": "moved",
                    "node_x": None,
                    "node_y": None,
                    "allow_connected": False,
                    "max_operators": 10,
                },
            }
        )
    assert lookup("/project1/source") is source
    assert lookup("/project1/group/moved") is None


def test_extension_reload_preserves_instance_identity_and_unconfirmed_results() -> None:
    owner = FakeOwner()
    first = AgentExt(owner)
    first.pending_results["request"] = {"result": "pending"}

    reloaded = AgentExt(owner)
    assert reloaded.instance_id == first.instance_id
    assert reloaded.pending_results == {"request": {"result": "pending"}}


def test_extension_rejects_missing_touchdesigner_build() -> None:
    del builtins.app
    with pytest.raises(RuntimeError, match="app build"):
        AgentExt(FakeOwner())


def test_new_touchdesigner_runtime_session_creates_new_instance_identity() -> None:
    owner = FakeOwner()
    first = AgentExt(owner)
    original_session = builtins._td_cli_runtime_session_id
    try:
        builtins._td_cli_runtime_session_id = "new-runtime-session"
        restarted = AgentExt(owner)
        assert restarted.instance_id != first.instance_id
        assert restarted.pending_results == {}
    finally:
        builtins._td_cli_runtime_session_id = original_session


def test_replacing_agent_component_in_same_runtime_preserves_identity_and_results() -> None:
    first = AgentExt(FakeOwner())
    first.pending_results["request"] = {"result": "pending"}

    replacement = AgentExt(FakeOwner())
    assert replacement.instance_id == first.instance_id
    assert replacement.pending_results == {"request": {"result": "pending"}}


def test_phase_2_runtime_state_is_migrated_without_changing_instance_identity() -> None:
    instance_id = str(uuid.uuid4())
    builtins._td_cli_agent_state = {
        "runtime_session_id": builtins._td_cli_runtime_session_id,
        "instance_id": instance_id,
        "pending_results": {},
        "seen_commands": {},
    }

    upgraded = AgentExt(FakeOwner())
    upgraded._record_event("command.succeeded", "request-1")

    assert upgraded.instance_id == instance_id
    assert upgraded.events == [{"id": 1, "kind": "command.succeeded", "request_id": "request-1"}]


def test_agent_advertises_and_executes_all_typed_commands() -> None:
    root = FakeOperator("/project1")
    child_b = FakeOperator("/project1/z", op_type="null")
    child_a = FakeOperator("/project1/a", op_type="base")
    root.children = [child_b, child_a]
    root.par.display = FakeParameter(True)
    root.par.reset = FakeParameter(pulseable=True)
    operators = {item.path: item for item in (root, child_a, child_b)}
    agent = AgentExt(FakeOwner(), operator_lookup=operators.get)

    assert agent.registration_payload()["td_build"] == "2025.32050"
    assert set(agent.registration_payload()["capabilities"]) == set(COMMAND_CATALOG.names)
    assert agent.execute_command({"name": "ops.get", "input": {"operator_path": "/project1"}}) == {
        "path": "/project1",
        "name": "project1",
        "op_type": "base",
        "family": "COMP",
    }
    assert agent.execute_command(
        {"name": "ops.children", "input": {"operator_path": "/project1", "op_type": None}}
    ) == [
        {"path": "/project1/a", "name": "a", "op_type": "base", "family": "COMP"},
        {"path": "/project1/z", "name": "z", "op_type": "null", "family": "COMP"},
    ]
    assert (
        agent.execute_command(
            {
                "name": "parameters.set",
                "input": {
                    "operator_path": "/project1",
                    "parameter": "display",
                    "mode": "constant",
                    "value": False,
                },
            }
        )["value"]
        is False
    )
    assert (
        agent.execute_command(
            {
                "name": "parameters.get",
                "input": {"operator_path": "/project1", "parameter": "display"},
            }
        )["value_type"]
        == "boolean"
    )
    assert agent.execute_command(
        {
            "name": "parameters.pulse",
            "input": {"operator_path": "/project1", "parameter": "reset"},
        }
    ) == {"operator_path": "/project1", "parameter": "reset", "pulsed": True}
    assert root.par.reset.pulses == 1


def test_agent_creates_and_connects_a_bounded_basic_network() -> None:
    parent = FakeOperator("/project1")
    operators = {parent.path: parent}

    def lookup(path: str):
        for child in parent.children:
            operators[child.path] = child
        return operators.get(path)

    agent = AgentExt(FakeOwner(), operator_lookup=lookup)
    created = agent.execute_command(
        {
            "name": "ops.create",
            "input": {
                "parent_path": "/project1",
                "op_type": "constantTOP",
                "name": "source",
                "node_x": -100,
                "node_y": 25,
            },
        }
    )
    agent.execute_command(
        {
            "name": "ops.create",
            "input": {
                "parent_path": "/project1",
                "op_type": "nullTOP",
                "name": "output",
                "node_x": 100,
                "node_y": 25,
            },
        }
    )
    connected = agent.execute_command(
        {
            "name": "ops.connect",
            "input": {
                "source_path": "/project1/source",
                "target_path": "/project1/output",
                "output_index": 0,
                "input_index": 0,
            },
        }
    )

    assert created == {
        "path": "/project1/source",
        "name": "source",
        "op_type": "constantTOP",
        "family": "TOP",
        "catalog_status": "supported",
    }
    assert operators["/project1/source"].nodeX == -100
    assert operators["/project1/source"].nodeY == 25
    assert connected == {
        "source_path": "/project1/source",
        "target_path": "/project1/output",
        "output_index": 0,
        "input_index": 0,
        "connected": True,
        "replaced": False,
        "previous_connection": None,
    }
    connection = operators["/project1/output"].inputConnectors[0].connections[0]
    assert (connection.owner.path, connection.index, connection.isOutput) == (
        "/project1/source",
        0,
        True,
    )

    with pytest.raises(module.AgentCommandError, match="operator_already_exists"):
        agent.execute_command(
            {
                "name": "ops.create",
                "input": {
                    "parent_path": "/project1",
                    "op_type": "constantTOP",
                    "name": "source",
                    "node_x": 0,
                    "node_y": 0,
                },
            }
        )

    with pytest.raises(module.AgentCommandError, match="connector_occupied"):
        agent.execute_command(
            {
                "name": "ops.connect",
                "input": {
                    "source_path": "/project1/source",
                    "target_path": "/project1/output",
                    "output_index": 0,
                    "input_index": 0,
                },
            }
        )


def test_agent_create_enforces_embedded_operator_catalog() -> None:
    parent = FakeOperator("/project1")

    def lookup(path: str):
        return next(
            (item for item in [parent, *parent.children] if item.path == path),
            None,
        )

    agent = AgentExt(FakeOwner(), operator_lookup=lookup)
    conditional = {
        "parent_path": parent.path,
        "op_type": "videodeviceinTOP",
        "name": "camera",
        "node_x": 0,
        "node_y": 0,
        "allow_conditional": False,
    }
    with pytest.raises(module.AgentCommandError, match="operator_type_conditional"):
        agent.execute_command({"name": "ops.create", "input": conditional})

    created = agent.execute_command(
        {"name": "ops.create", "input": {**conditional, "allow_conditional": True}}
    )
    assert created["catalog_status"] == "conditional"

    with pytest.raises(module.AgentCommandError, match="operator_type_unsupported"):
        agent.execute_command(
            {
                "name": "ops.create",
                "input": {
                    **conditional,
                    "op_type": "futureTOP",
                    "name": "future",
                    "allow_conditional": True,
                },
            }
        )


def test_operator_control_renames_exactly_and_rejects_collision() -> None:
    class RenameOperator(FakeOperator):
        def __init__(self, path: str) -> None:
            self._name = path.rsplit("/", 1)[-1]
            super().__init__(path)

        @property
        def name(self):
            return self._name

        @name.setter
        def name(self, value):
            self._name = value
            self.path = self.path.rsplit("/", 1)[0] + "/" + value

    operator = RenameOperator("/project1/source")
    occupied = RenameOperator("/project1/occupied")

    def lookup(path: str):
        return next((item for item in (operator, occupied) if item.path == path), None)

    control = make_control(lookup)
    assert control.execute(
        {"name": "ops.rename", "input": {"operator_path": operator.path, "new_name": "renamed"}}
    ) == {
        "old_path": "/project1/source",
        "path": "/project1/renamed",
        "old_name": "source",
        "name": "renamed",
        "renamed": True,
    }

    with pytest.raises(module.AgentCommandError, match="operator_already_exists"):
        control.execute(
            {
                "name": "ops.rename",
                "input": {"operator_path": operator.path, "new_name": "occupied"},
            }
        )


def test_rename_failure_rolls_back_or_reports_uncertain_state() -> None:
    class CorrectingOperator:
        family = "TOP"
        OPType = "nullTOP"

        def __init__(self, *, rollback_fails: bool) -> None:
            self.path = "/project1/source"
            self._name = "source"
            self.rollback_fails = rollback_fails

        @property
        def name(self):
            return self._name

        @name.setter
        def name(self, value):
            if value == "source" and self.rollback_fails:
                raise RuntimeError("rollback rejected")
            actual = value if value == "source" else value + "1"
            self._name = actual
            self.path = "/project1/" + actual

    restored = CorrectingOperator(rollback_fails=False)
    with pytest.raises(module.AgentCommandError, match="operator_rename_failed"):
        make_control(lambda _: restored).execute(
            {
                "name": "ops.rename",
                "input": {"operator_path": restored.path, "new_name": "renamed"},
            }
        )
    assert (restored.path, restored.name) == ("/project1/source", "source")

    uncertain = CorrectingOperator(rollback_fails=True)
    with pytest.raises(module.AgentCommandError, match="operator_rename_rollback_failed"):
        make_control(lambda _: uncertain).execute(
            {
                "name": "ops.rename",
                "input": {"operator_path": uncertain.path, "new_name": "renamed"},
            }
        )


def test_operator_control_disconnects_exactly_and_replaces_input_connection() -> None:
    first = FakeOperator("/project1/first", family="TOP", outputs=1)
    second = FakeOperator("/project1/second", family="TOP", outputs=1)
    target = FakeOperator("/project1/target", family="TOP", inputs=1)
    operators = {item.path: item for item in (first, second, target)}
    control = make_control(operators.get)
    first.outputConnectors[0].connect(target.inputConnectors[0])

    with pytest.raises(module.AgentCommandError, match="connection_not_found"):
        control.execute(
            {
                "name": "ops.disconnect",
                "input": {
                    "source_path": second.path,
                    "target_path": target.path,
                    "output_index": 0,
                    "input_index": 0,
                },
            }
        )

    assert (
        control.execute(
            {
                "name": "ops.disconnect",
                "input": {
                    "source_path": first.path,
                    "target_path": target.path,
                    "output_index": 0,
                    "input_index": 0,
                },
            }
        )["disconnected"]
        is True
    )
    assert target.inputConnectors[0].connections == []

    first.outputConnectors[0].connect(target.inputConnectors[0])
    replaced = control.execute(
        {
            "name": "ops.connect",
            "input": {
                "source_path": second.path,
                "target_path": target.path,
                "output_index": 0,
                "input_index": 0,
                "replace": True,
            },
        }
    )
    assert replaced["replaced"] is True
    assert replaced["previous_connection"] == {
        "source_path": first.path,
        "output_index": 0,
    }
    connection = target.inputConnectors[0].connections[0]
    assert (connection.owner.path, connection.index) == (second.path, 0)


def test_replace_failure_restores_previous_connection_or_reports_uncertain_state() -> None:
    first = FakeOperator("/project1/first", family="TOP", outputs=1)
    second = FakeOperator("/project1/second", family="TOP", outputs=1)
    target = FakeOperator("/project1/target", family="TOP", inputs=1)
    operators = {item.path: item for item in (first, second, target)}
    control = make_control(operators.get)
    connector = target.inputConnectors[0]
    first.outputConnectors[0].connect(connector)
    input_connect = connector.connect

    def reject_second(source_connector) -> None:
        if source_connector.owner is second:
            connector.connections.clear()
            raise RuntimeError("replace rejected")
        input_connect(source_connector)

    connector.connect = reject_second
    payload = {
        "source_path": second.path,
        "target_path": target.path,
        "output_index": 0,
        "input_index": 0,
        "replace": True,
    }
    with pytest.raises(module.AgentCommandError, match="connector_replace_failed"):
        control.execute({"name": "ops.connect", "input": payload})
    assert connector.connections[0].owner is first

    first.outputConnectors[0].connect(connector)
    connector.connect = lambda _: (_ for _ in ()).throw(RuntimeError("all connects rejected"))
    with pytest.raises(module.AgentCommandError, match="connector_replace_rollback_failed"):
        control.execute({"name": "ops.connect", "input": payload})
    assert connector.connections == []


def test_network_mutation_failures_roll_back_partial_changes() -> None:
    parent = FakeOperator("/project1")

    class FailingCreated:
        path = "/project1/failing"
        name = "failing"
        family = "TOP"
        OPType = "constantTOP"
        destroyed = False

        @property
        def nodeX(self):
            return 0

        @nodeX.setter
        def nodeX(self, value):
            del value
            raise RuntimeError("position rejected")

        def destroy(self) -> None:
            self.destroyed = True

    partial = FailingCreated()
    parent.create = lambda op_type, name: partial
    operators = {parent.path: parent}
    agent = AgentExt(FakeOwner(), operator_lookup=operators.get)

    with pytest.raises(module.AgentCommandError, match="operator_create_failed"):
        agent.execute_command(
            {
                "name": "ops.create",
                "input": {
                    "parent_path": "/project1",
                    "op_type": "constantTOP",
                    "name": "failing",
                    "node_x": 1,
                    "node_y": 2,
                },
            }
        )
    assert partial.destroyed is True

    source = FakeOperator("/project1/source", family="TOP", outputs=1)
    target = FakeOperator("/project1/target", family="TOP", inputs=1)

    def connect_with_wrong_wrapper(target_connector) -> None:
        wrong_owner = FakeOperator("/project1/wrong", family="TOP")
        target_connector.connections.append(FakeConnector(wrong_owner, 0, is_input=False))

    source.outputConnectors[0].connect = connect_with_wrong_wrapper
    operators.update({source.path: source, target.path: target})

    with pytest.raises(module.AgentCommandError, match="connector_connect_failed"):
        agent.execute_command(
            {
                "name": "ops.connect",
                "input": {
                    "source_path": source.path,
                    "target_path": target.path,
                    "output_index": 0,
                    "input_index": 0,
                },
            }
        )
    assert target.inputConnectors[0].connections == []


def test_accept_omits_optional_nulls_from_locked_socketio_payload() -> None:
    agent = AgentExt(FakeOwner())
    agent.connection_id = "connection-1"
    agent.execute_command = lambda _: {
        "required": True,
        "optional": None,
        "nested": {"value": None, "items": [1, None, 2]},
    }

    event, payload = agent.accept(
        {"request_id": "wire-safe", "command": {"name": "ops.get", "input": {}}}
    )

    assert event == "request_result"
    assert payload["result"] == {
        "required": True,
        "nested": {"items": [1, {"__td_cli_null__": True}, 2]},
    }


def test_agent_rejects_invalid_expression_with_typed_error() -> None:
    root = FakeOperator("/project1")
    root.par.display = FakeParameter(True)
    agent = AgentExt(FakeOwner(), operator_lookup=lambda _: root)
    agent.connection_id = "connection-1"

    invalid_event, invalid = agent.accept(
        {
            "request_id": "invalid-expression",
            "command": {
                "name": "parameters.set",
                "input": {
                    "operator_path": "/project1",
                    "parameter": "display",
                    "mode": "expression",
                    "value": ")",
                },
            },
        }
    )
    assert invalid_event == "request_rejected"
    assert invalid["code"] == "expression_invalid"


def test_parameter_list_reports_runtime_names_types_and_expression_capabilities() -> None:
    root = FakeOperator("/project1")

    def parameter(name: str, style: str, **values):
        item = FakeParameter(values.pop("value", None), mode=values.pop("mode", "constant"))
        defaults = {
            "name": name,
            "label": name.title(),
            "style": style,
            "page": SimpleNamespace(name="Test"),
            "hidden": False,
            "isPulse": False,
            "isMenu": False,
            "isNumber": False,
            "isFloat": False,
            "isInt": False,
            "isOP": False,
            "isPython": False,
            "isSequence": False,
            "isString": False,
            "isToggle": False,
            "menuNames": [],
            "menuLabels": [],
        }
        for key, value in {**defaults, **values}.items():
            setattr(item, key, value)
        return item

    gain = parameter("gain", "Float", value=0.5, isNumber=True, isFloat=True)
    gain.expr = "me.time.seconds"
    menu = parameter(
        "operation",
        "Menu",
        isMenu=True,
        menuNames=["add", "multiply"],
        menuLabels=["Add", "Multiply"],
    )
    empty_menu = parameter("empty", "Menu", isMenu=True, menuNames=None, menuLabels=None)
    python_value = parameter("payload", "Python", isPython=True)
    multi_operator = parameter("targets", "OP", value=[root], isOP=True)
    pulse = parameter("reset", "Pulse", isPulse=True)
    hidden = parameter("legacy", "Int", isInt=True, hidden=True)
    custom = parameter("Customvalue", "Str", isString=True, mode="expression")
    custom.expr = "'hello'"
    root.builtinPars = [gain, menu, empty_menu, python_value, multi_operator, pulse, hidden]
    root.customPars = [custom]

    result = make_control(lambda _: root).execute(
        {"name": "parameters.list", "input": {"operator_path": root.path}}
    )
    items = {item["name"]: item for item in result["parameters"]}
    assert result["operator_path"] == root.path
    assert items["gain"]["value_kind"] == "number"
    assert items["gain"]["expression"] == {
        "supported": True,
        "source": "me.time.seconds",
    }
    assert items["operation"]["menu_names"] == ["add", "multiply"]
    assert items["operation"]["menu_labels"] == ["Add", "Multiply"]
    assert items["operation"]["expression"]["source"] is None
    assert items["empty"]["menu_names"] == []
    assert items["empty"]["menu_labels"] == []
    assert items["payload"]["constant_supported"] is False
    assert items["payload"]["expression_supported"] is False
    assert items["targets"]["value_kind"] == "operator"
    assert items["targets"]["constant_supported"] is True
    assert items["reset"]["pulse_supported"] is True
    assert items["legacy"]["hidden"] is True
    assert items["Customvalue"]["custom"] is True
    assert items["Customvalue"]["mode"] == "expression"

    batch = make_control(lambda _: root)
    batch.preflight({"name": "parameters.list", "input": {"operator_path": root.path}})


@pytest.mark.parametrize("mode", ["export", "bind"])
def test_parameter_get_reports_non_constant_runtime_modes(mode: str) -> None:
    root = FakeOperator("/project1")
    root.par.driven = FakeParameter(2.5, mode=mode)
    result = make_control(lambda _: root).execute(
        {
            "name": "parameters.get",
            "input": {"operator_path": root.path, "parameter": "driven"},
        }
    )
    assert result["mode"] == mode
    assert result["value"] == 2.5


def test_parameter_descriptor_rejects_disabled_obsolete_and_opaque_writes() -> None:
    root = FakeOperator("/project1")
    disabled = FakeParameter(1)
    disabled.name = "Disabled"
    disabled.enable = False
    disabled.hidden = False
    disabled.readOnly = False
    disabled.isPython = False
    disabled.isSequence = False
    disabled.isOP = False
    disabled.isPulse = False
    disabled.isMenu = False
    disabled.isToggle = False
    disabled.isInt = True
    disabled.isFloat = False
    disabled.isNumber = True
    disabled.isString = False
    disabled.style = "Int"
    root.par.Disabled = disabled

    control = make_control({root.path: root}.get)
    with pytest.raises(module.AgentCommandError, match="parameter_disabled"):
        control.execute(
            {
                "name": "parameters.set",
                "input": {
                    "operator_path": root.path,
                    "parameter": "Disabled",
                    "mode": "constant",
                    "value": 2,
                },
            }
        )
    assert disabled.val == 1

    opaque = FakeParameter(object())
    opaque.isPython = True
    opaque.style = "Python"
    root.par.Opaque = opaque
    inspected = control.execute(
        {
            "name": "parameters.get",
            "input": {"operator_path": root.path, "parameter": "Opaque"},
        }
    )
    assert inspected["value"] is None
    assert inspected["value_type"] == "python"
    assert inspected["unsupported_reason"] == "opaque_or_structural"


def test_parameter_operator_values_use_exact_canonical_paths() -> None:
    root = FakeOperator("/project1")
    target = FakeOperator("/project1/target")
    parameter = FakeParameter(None)
    parameter.style = "OP"
    parameter.isOP = True
    parameter.evalOPs = lambda: [] if parameter.val is None else [parameter.val]
    root.par.Target = parameter
    control = make_control({root.path: root, target.path: target}.get)

    result = control.execute(
        {
            "name": "parameters.set",
            "input": {
                "operator_path": root.path,
                "parameter": "Target",
                "mode": "constant",
                "value": target.path,
            },
        }
    )
    assert result["value"] == target.path
    assert result["value_type"] == "operator"

    with pytest.raises(module.AgentCommandError, match="parameter_source_not_found"):
        control.execute(
            {
                "name": "parameters.set",
                "input": {
                    "operator_path": root.path,
                    "parameter": "Target",
                    "mode": "constant",
                    "value": "/project1/missing",
                },
            }
        )


def test_parameter_sequence_replace_reads_back_complete_ordered_blocks() -> None:
    root = FakeOperator("/project1")

    def scalar(name: str, value: float):
        parameter = FakeParameter(value)
        parameter.name = name
        parameter.isSamePar = lambda other, item=parameter: other is item
        setattr(root.par, name, parameter)
        return parameter

    class Block:
        def __init__(self, index: int, value: float) -> None:
            self.index = index
            self.value_par = scalar(f"Items{index}value", value)
            self.namePar = scalar(f"Items{index}blockname", f"block-{index}")
            self.par = {
                "value": self.value_par,
                "blockname": self.namePar,
            }

        @property
        def name(self):
            return self.namePar.val

        @name.setter
        def name(self, value):
            self.namePar.val = value

        def __iter__(self):
            return iter(((self.value_par,), (self.namePar,)))

    class Sequence:
        name = "Items"
        blockSize = 2
        maxBlocks = 8
        owner = root

        def __init__(self) -> None:
            self.blocks = [Block(0, 1.0), Block(1, 2.0)]

        @property
        def numBlocks(self):
            return len(self.blocks)

        @numBlocks.setter
        def numBlocks(self, value):
            assert value == len(self.blocks)

    sequence = Sequence()
    root.seq = {"Items": sequence}
    control = make_control({root.path: root}.get)

    result = control.execute(
        {
            "name": "parameters.sequence.replace",
            "input": {
                "operator_path": root.path,
                "sequence": "Items",
                "blocks": [
                    {
                        "name": "first",
                        "parameters": [
                            {"parameter": "value", "mode": "constant", "value": 10.0}
                        ],
                    },
                    {
                        "name": "second",
                        "parameters": [
                            {"parameter": "value", "mode": "constant", "value": 20.0}
                        ],
                    },
                ],
            },
        }
    )
    assert [block["name"] for block in result["blocks"]] == ["first", "second"]
    assert [block["parameters"][0]["value"] for block in result["blocks"]] == [10.0, 20.0]


def test_bind_write_normalizes_socketio_omitted_nullable_source_fields() -> None:
    target = FakeOperator("/project1/target")
    source = FakeOperator("/project1/source")
    master = FakeParameter(0.5)
    master.name = "Master"
    master.owner = source
    source.par.Master = master

    class BoundParameter(FakeParameter):
        bindMaster = None

        @property
        def bindExpr(self):
            return getattr(self, "_bind_expr", "")

        @bindExpr.setter
        def bindExpr(self, value):
            self._bind_expr = value
            self.mode = "bind"
            self.bindMaster = master

    bound = BoundParameter(0.25)
    target.par.Gain = bound
    control = make_control({target.path: target, source.path: source}.get)
    result = control.execute(
        {
            "name": "parameters.set",
            "input": {
                "operator_path": target.path,
                "parameter": "Gain",
                "mode": "bind",
                "source": {
                    "kind": "bind_parameter",
                    "operator_path": source.path,
                    "parameter": "Master",
                },
            },
        }
    )
    assert result["mode"] == "bind"
    assert result["source"] == {
        "kind": "bind_parameter",
        "operator_path": source.path,
        "channel": None,
        "parameter": "Master",
    }


def test_phase_3_observation_binary_metadata_and_events_are_bounded() -> None:
    root = FakeOperator("/", family="COMP")
    project1 = FakeOperator("/project1", family="COMP")
    root.children = [project1]
    metadata = SimpleNamespace(
        name="Sample.toe",
        folder="E:/td-cli",
        saveVersion="099",
        saveBuild="2025.32050",
        saveTime="2026-08-09",
        saveOSName="Windows",
        saveOSVersion="11",
    )
    agent = AgentExt(
        FakeOwner(), operator_lookup={"/": root, "/project1": project1}.get, project_info=metadata
    )

    snapshot = agent.execute_command(
        {
            "name": "project.snapshot",
            "input": {"operator_path": "/", "max_depth": 1, "max_operators": 2},
        }
    )
    assert [item["path"] for item in snapshot["operators"]] == ["/", "/project1"]
    exported = agent.execute_command(
        {
            "name": "binary.export",
            "input": {"operator_path": "/project1", "format": "tox", "max_bytes": 100},
        }
    )
    assert exported["data_base64"] == "VEQtQklOQVJZ"
    assert agent.execute_command({"name": "project.metadata", "input": {}})["name"] == "Sample.toe"

    agent._record_event("command.succeeded", "request-1")
    observed = agent.execute_command(
        {"name": "events.read", "input": {"after": 0, "limit": 1, "include_errors": True}}
    )
    assert observed == {
        "events": [{"id": 1, "kind": "command.succeeded", "request_id": "request-1"}],
        "errors": ["sample error"],
        "next_after": 1,
    }


def test_batch_preflights_every_item_before_any_mutation() -> None:
    root = FakeOperator("/project1")
    root.par.display = FakeParameter(True)
    agent = AgentExt(FakeOwner(), operator_lookup=lambda _: root)

    with pytest.raises(module.AgentCommandError, match="parameter_not_found"):
        agent.execute_command(
            {
                "name": "batch.execute",
                "input": {
                    "commands": [
                        {
                            "name": "parameters.set",
                            "input": {
                                "operator_path": "/project1",
                                "parameter": "display",
                                "mode": "constant",
                                "value": False,
                            },
                        },
                        {
                            "name": "parameters.get",
                            "input": {"operator_path": "/project1", "parameter": "missing"},
                        },
                    ]
                },
            }
        )
    assert root.par.display.val is True


def test_batch_preflights_unsupported_parameter_value_before_mutation() -> None:
    root = FakeOperator("/project1")
    root.par.display = FakeParameter(True)
    root.par.unsupported = FakeParameter(object())
    agent = AgentExt(FakeOwner(), operator_lookup=lambda _: root)

    with pytest.raises(module.AgentCommandError, match="parameter_type_unsupported"):
        agent.execute_command(
            {
                "name": "batch.execute",
                "input": {
                    "commands": [
                        {
                            "name": "parameters.set",
                            "input": {
                                "operator_path": "/project1",
                                "parameter": "display",
                                "mode": "constant",
                                "value": False,
                            },
                        },
                        {
                            "name": "parameters.set",
                            "input": {
                                "operator_path": "/project1",
                                "parameter": "unsupported",
                                "mode": "constant",
                                "value": "opaque",
                            },
                        },
                    ]
                },
            }
        )
    assert root.par.display.val is True


def test_snapshot_is_deterministic_breadth_first_and_enforces_operator_cap() -> None:
    root = FakeOperator("/project1")
    child_b = FakeOperator("/project1/b")
    child_a = FakeOperator("/project1/a")
    grandchild = FakeOperator("/project1/a/z")
    child_a.children = [grandchild]
    root.children = [child_b, child_a]
    agent = AgentExt(FakeOwner(), operator_lookup=lambda _: root)

    result = agent.execute_command(
        {
            "name": "project.snapshot",
            "input": {"operator_path": "/project1", "max_depth": 2, "max_operators": 4},
        }
    )
    assert [(item["path"], item["depth"]) for item in result["operators"]] == [
        ("/project1", 0),
        ("/project1/a", 1),
        ("/project1/b", 1),
        ("/project1/a/z", 2),
    ]
    with pytest.raises(module.AgentCommandError, match="result_too_large"):
        agent.execute_command(
            {
                "name": "project.snapshot",
                "input": {"operator_path": "/project1", "max_depth": 2, "max_operators": 3},
            }
        )


def test_binary_export_enforces_family_and_raw_byte_cap() -> None:
    comp = FakeOperator("/project1/component", family="COMP")
    agent = AgentExt(FakeOwner(), operator_lookup=lambda _: comp)

    with pytest.raises(module.AgentCommandError, match="command_unsupported"):
        agent.execute_command(
            {
                "name": "binary.export",
                "input": {"operator_path": comp.path, "format": "png", "max_bytes": 100},
            }
        )
    with pytest.raises(module.AgentCommandError, match="result_too_large"):
        agent.execute_command(
            {
                "name": "binary.export",
                "input": {"operator_path": comp.path, "format": "tox", "max_bytes": 1},
            }
        )


def test_event_ring_retains_1000_and_reads_at_most_requested_200() -> None:
    root = FakeOperator("/")
    agent = AgentExt(FakeOwner(), operator_lookup=lambda _: root)
    for index in range(1001):
        agent._record_event("command.succeeded", f"request-{index}")

    result = agent.execute_command(
        {"name": "events.read", "input": {"after": 0, "limit": 200, "include_errors": False}}
    )

    assert len(agent.events) == 1000
    assert len(result["events"]) == 200
    assert result["events"][0]["id"] == 2
    assert result["next_after"] == 201


def test_accept_records_internal_and_oversized_outcomes() -> None:
    root = FakeOperator("/project1")
    lookup_fails = {"value": True}

    def lookup(_):
        if lookup_fails["value"]:
            raise RuntimeError("boom")
        return root

    agent = AgentExt(FakeOwner(), operator_lookup=lookup)
    agent.connection_id = "connection-1"
    event, result = agent.accept(
        {
            "request_id": "internal",
            "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
        }
    )
    assert (event, result["code"]) == ("request_rejected", "internal_error")

    lookup_fails["value"] = False
    agent.MAX_RESULT_BYTES = 1
    event, result = agent.accept(
        {
            "request_id": "oversized",
            "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
        }
    )
    assert (event, result["code"]) == ("request_rejected", "result_too_large")
    assert [(item["request_id"], item["code"]) for item in agent.events] == [
        ("internal", "internal_error"),
        ("oversized", "result_too_large"),
    ]
