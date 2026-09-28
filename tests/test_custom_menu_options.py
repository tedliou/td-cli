"""Public seams of `parameters.menu.set` (issue 145)."""

from types import SimpleNamespace

import pytest
from test_agent_runtime import FakeParameter, make_control, module

from td_cli.command_catalog import COMMAND_CATALOG
from td_cli.command_run import CommandPlan


def menu_set_input(**overrides):
    return {
        "operator_path": "/project1/controls",
        "parameter": "Scene",
        "menu_names": ["D01", "D02", "D03"],
        "menu_labels": ["01 Tide", "02 Mist", "03 Tide"],
        "preserve": "index",
        **overrides,
    }


def test_menu_set_contract_is_bounded_and_accepted_by_command_plans():
    normalized = COMMAND_CATALOG.validate_input("parameters.menu.set", menu_set_input())
    assert normalized == menu_set_input()
    assert COMMAND_CATALOG.effect("parameters.menu.set") == "mutation"
    assert "parameters.menu.set" not in COMMAND_CATALOG.batch_names
    plan = CommandPlan.model_validate(
        {"commands": [{"name": "parameters.menu.set", "input": menu_set_input()}]}
    )
    assert plan.commands[0].input.model_dump() == normalized
    widest = menu_set_input(
        menu_names=[f"D{i:02}" for i in range(1, 33)], menu_labels=["霧" * 128] * 32
    )
    assert COMMAND_CATALOG.validate_input("parameters.menu.set", widest) == widest
    for patch in [
        {"menu_names": ["D01", "D01", "D03"]},
        {"menu_names": ["D01", "D02"]},
        {"menu_names": ["D01", "", "D03"]},
        {"menu_labels": ["01", "", "03"]},
        {"menu_names": [], "menu_labels": []},
        {"menu_names": [f"D{i}" for i in range(33)], "menu_labels": ["x"] * 33},
        {"menu_names": ["n" * 129, "D02", "D03"]},
        {"preserve": "value"},
        {"parameter": ""},
    ]:
        with pytest.raises(ValueError):
            COMMAND_CATALOG.validate_input("parameters.menu.set", menu_set_input(**patch))
    without_policy = {key: value for key, value in menu_set_input().items() if key != "preserve"}
    with pytest.raises(ValueError):
        COMMAND_CATALOG.validate_input("parameters.menu.set", without_policy)


class FakeMenuParameter:
    """Custom Menu Par following the locked TD 2025.32050 behavior probed for issue 145.

    Assigning names keeps the selected index and pairs names with the current labels
    (truncating to the shorter list); assigning longer labels fills new names from labels.
    Built-in menus reject option writes and a ``menuSource`` silently ignores them.
    """

    def __init__(self, names, labels, value, default, *, custom=True, menu_source=None):
        self.name, self.label, self.style = "Scene", "Scene", "Menu"
        self.page = SimpleNamespace(name="Preview")
        self.mode = "ParMode.CONSTANT"
        self.expr = self.bindExpr = ""
        self.readOnly = self.hidden = False
        self.enable = True
        self.isMenu, self.isCustom = True, custom
        self.isPython = self.isSequence = self.isOP = self.isPulse = False
        self.menuSource = menu_source
        self._entries = list(zip(names, labels, strict=True))
        self.val, self.default = value, default
        self.fail_on = set()

    def _check(self, attribute):
        if attribute in self.fail_on:
            self.fail_on.discard(attribute)
            raise RuntimeError(f"TD rejected {attribute}")

    def eval(self):
        return self.val

    @property
    def menuNames(self):
        return [name for name, _ in self._entries]

    @menuNames.setter
    def menuNames(self, names):
        self._check("menuNames")
        if not self.isCustom:
            raise RuntimeError("Custom menu parameter expected")
        if self.menuSource:
            return
        index = self.menuIndex or 0
        labels = self.menuLabels
        self._entries = [(names[i], labels[i]) for i in range(min(len(names), len(labels)))]
        self.val = self._entries[min(index, len(self._entries) - 1)][0]

    @property
    def menuLabels(self):
        return [label for _, label in self._entries]

    @menuLabels.setter
    def menuLabels(self, labels):
        self._check("menuLabels")
        if not self.isCustom:
            raise RuntimeError("Custom menu parameter expected")
        names = self.menuNames
        self._entries = [
            (names[i] if i < len(names) else labels[i], labels[i]) for i in range(len(labels))
        ]

    @property
    def menuIndex(self):
        names = self.menuNames
        return names.index(self.val) if self.val in names else None

    def __setattr__(self, attribute, value):
        if attribute in {"val", "default"} and hasattr(self, "fail_on"):
            self._check(attribute)
        object.__setattr__(self, attribute, value)


class MenuComp:
    path = "/project1/controls"
    isCOMP = True

    def __init__(self, parameter):
        self.par = SimpleNamespace(Scene=parameter, Gain=FakeParameter(0.5))


OLD_SCENES = ["D10", "D02", "D05"]
OLD_LABELS = ["01 D10 Tide", "02 D02 Mist", "03 D05 Tide"]


def menu_state(parameter):
    return (parameter.menuNames, parameter.menuLabels, parameter.val, parameter.default)


def run_menu_set(menu, lookup=None, **overrides):
    comp = MenuComp(menu)
    control = make_control(lookup or (lambda path: comp))
    return control.execute({"name": "parameters.menu.set", "input": menu_set_input(**overrides)})


def test_menu_set_renames_options_and_keeps_the_selected_position():
    parameter = FakeMenuParameter(OLD_SCENES, OLD_LABELS, "D02", "D10")
    result = run_menu_set(parameter)
    assert result == {
        "operator_path": "/project1/controls",
        "parameter": "Scene",
        "preserve": "index",
        "menu_names": ["D01", "D02", "D03"],
        "menu_labels": ["01 Tide", "02 Mist", "03 Tide"],
        "before": {"value": "D02", "index": 1, "default": "D10"},
        "after": {"value": "D02", "index": 1, "default": "D01"},
    }
    assert menu_state(parameter) == (
        ["D01", "D02", "D03"],
        ["01 Tide", "02 Mist", "03 Tide"],
        "D02",
        "D01",
    )


def test_menu_set_grows_the_menu_despite_native_label_truncation():
    parameter = FakeMenuParameter(OLD_SCENES, OLD_LABELS, "D05", "D10")
    names = [f"D{i:02}" for i in range(1, 31)]
    labels = [f"{i:02} Scene" for i in range(1, 31)]
    result = run_menu_set(parameter, menu_names=names, menu_labels=labels)
    assert (parameter.menuNames, parameter.menuLabels) == (names, labels)
    assert result["after"] == {"value": "D03", "index": 2, "default": "D01"}


def test_menu_set_name_policy_follows_the_selected_name_through_reordering():
    parameter = FakeMenuParameter(OLD_SCENES, OLD_LABELS, "D05", "D10")
    result = run_menu_set(
        parameter, menu_names=["D05", "D10", "D02"], menu_labels=["a", "b", "c"], preserve="name"
    )
    assert result["before"] == {"value": "D05", "index": 2, "default": "D10"}
    assert result["after"] == {"value": "D05", "index": 0, "default": "D10"}
    assert parameter.default == "D10"


def test_menu_set_leaves_a_default_that_names_no_option_unchanged():
    parameter = FakeMenuParameter(OLD_SCENES, OLD_LABELS, "D02", "gone")
    result = run_menu_set(parameter)
    assert result["before"]["default"] == result["after"]["default"] == "gone"


def _menu(value="D05", **options):
    return FakeMenuParameter(OLD_SCENES, OLD_LABELS, value, "D10", **options)


@pytest.mark.parametrize(
    ("parameter", "overrides", "code"),
    [
        (_menu(custom=False), {}, "parameter_menu_not_writable"),
        (_menu(menu_source="op('table')"), {}, "parameter_menu_not_writable"),
        (_menu("stale"), {}, "parameter_value_invalid"),
        (
            _menu(),
            {"menu_names": ["D01", "D02"], "menu_labels": ["a", "b"]},
            "parameter_value_invalid",
        ),
        (
            _menu("D02"),
            {"menu_names": ["D02", "D03"], "menu_labels": ["a", "b"], "preserve": "name"},
            "parameter_value_invalid",
        ),
        (
            _menu(),
            {"menu_names": ["D05", "D01", "D02"], "preserve": "name"},
            "parameter_value_invalid",
        ),
    ],
)
def test_menu_set_rejects_unmappable_or_foreign_menus_before_mutation(parameter, overrides, code):
    before = menu_state(parameter)
    with pytest.raises(module.AgentCommandError, match=code):
        run_menu_set(parameter, **overrides)
    assert menu_state(parameter) == before


@pytest.mark.parametrize(
    ("attribute", "value", "code"),
    [
        ("mode", "ParMode.EXPRESSION", "parameter_menu_not_writable"),
        ("readOnly", True, "parameter_read_only"),
        ("style", "StrMenu", "parameter_type_unsupported"),
    ],
)
def test_menu_set_requires_a_constant_writable_menu(attribute, value, code):
    parameter = _menu()
    setattr(parameter, attribute, value)
    before = menu_state(parameter)
    with pytest.raises(module.AgentCommandError, match=code):
        run_menu_set(parameter)
    assert menu_state(parameter) == before


def test_menu_set_rejects_non_menu_parameters():
    with pytest.raises(module.AgentCommandError, match="parameter_type_unsupported"):
        run_menu_set(_menu(), parameter="Gain")


def test_menu_set_rolls_back_a_partial_write():
    parameter = _menu("D02")
    parameter.fail_on = {"default"}
    with pytest.raises(module.AgentCommandError, match="parameter_write_rejected"):
        run_menu_set(parameter)
    parameter.fail_on = set()
    assert menu_state(parameter) == (OLD_SCENES, OLD_LABELS, "D02", "D10")


def test_menu_set_reports_a_failed_rollback():
    parameter = _menu("D02")
    parameter.fail_on = {"default"}
    writes = []
    original = type(parameter).menuNames.fset

    class RestoreRejectingMenu(FakeMenuParameter):
        @FakeMenuParameter.menuNames.setter
        def menuNames(self, names):
            writes.append(names)
            if names == OLD_SCENES:
                raise RuntimeError("TD rejected restore")
            original(self, names)

    parameter.__class__ = RestoreRejectingMenu
    with pytest.raises(module.AgentCommandError, match="parameter_rollback_failed"):
        run_menu_set(parameter)
    assert OLD_SCENES in writes


def test_menu_set_target_vanishing_after_a_failed_write_is_unknown():
    parameter = _menu("D02")
    parameter.fail_on = {"default"}
    comp = MenuComp(parameter)
    lookups = iter([comp, None])
    with pytest.raises(module.AgentCommandError, match="parameter_outcome_unknown"):
        run_menu_set(parameter, lookup=lambda path: next(lookups))
