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
    A default naming no option is stored verbatim; a default never assigned (``None``)
    reports the current first option and becomes explicit once assigned. Built-in menus reject option writes and
    a ``menuSource`` silently ignores them.

    ``failures`` maps an attribute to how many writes succeed before exactly one write of
    it raises; ``corrupt_labels`` maps the 1-based labels write that silently loses its
    last label, modelling a write TouchDesigner accepts but does not apply.
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
        self.failures = {}
        self.writes = {}
        self.corrupt_labels = set()
        object.__setattr__(self, "val", value)
        self._default = default

    def _check(self, attribute):
        count = self.writes.get(attribute, 0)
        self.writes[attribute] = count + 1
        if self.failures.get(attribute) == count:
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
        object.__setattr__(self, "val", self._entries[min(index, len(self._entries) - 1)][0])

    @property
    def menuLabels(self):
        return [label for _, label in self._entries]

    @menuLabels.setter
    def menuLabels(self, labels):
        self._check("menuLabels")
        if not self.isCustom:
            raise RuntimeError("Custom menu parameter expected")
        if self.writes["menuLabels"] in self.corrupt_labels:
            labels = [*labels[:-1], "lost"]
        names = self.menuNames
        self._entries = [
            (names[i] if i < len(names) else labels[i], labels[i]) for i in range(len(labels))
        ]

    @property
    def default(self):
        if self._default is None:
            return self.menuNames[0] if self._entries else ""
        return self._default

    @default.setter
    def default(self, value):
        self._check("default")
        self._default = value

    @property
    def menuIndex(self):
        names = self.menuNames
        return names.index(self.val) if self.val in names else None

    def __setattr__(self, attribute, value):
        if attribute == "val" and hasattr(self, "failures"):
            self._check(attribute)
        object.__setattr__(self, attribute, value)


class MenuComp:
    path = "/project1/controls"
    isCOMP = True

    def __init__(self, parameter, operator_id=1):
        self.id = operator_id
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


THIRTY = [f"D{i:02}" for i in range(1, 31)]


@pytest.mark.parametrize(
    ("overrides", "failures"),
    [
        ({}, {"default": 0}),
        ({"menu_names": THIRTY, "menu_labels": THIRTY}, {"default": 0}),
        ({"menu_names": THIRTY, "menu_labels": THIRTY}, {"menuNames": 1}),
        ({"menu_names": ["D01", "D02"], "menu_labels": ["a", "b"]}, {"default": 0}),
        ({"menu_names": ["D01", "D02"], "menu_labels": ["a", "b"]}, {"val": 0}),
    ],
)
def test_menu_set_rolls_back_a_partial_write(overrides, failures):
    parameter = _menu("D02")
    parameter.failures = failures
    with pytest.raises(module.AgentCommandError, match="parameter_write_rejected"):
        run_menu_set(parameter, **overrides)
    assert menu_state(parameter) == (OLD_SCENES, OLD_LABELS, "D02", "D10")


@pytest.mark.parametrize("names", [["D01", "D02", "D03"], THIRTY, ["D01", "D02"]])
def test_menu_set_rolls_back_a_write_that_reads_back_differently(names):
    parameter = _menu("D02")
    parameter.corrupt_labels = {1}
    with pytest.raises(module.AgentCommandError, match="parameter_write_rejected"):
        run_menu_set(parameter, menu_names=names, menu_labels=[f"L{name}" for name in names])
    assert menu_state(parameter) == (OLD_SCENES, OLD_LABELS, "D02", "D10")


def test_menu_set_keeps_an_orphan_default_without_rewriting_it():
    parameter = FakeMenuParameter(OLD_SCENES, OLD_LABELS, "D02", "gone")
    parameter.failures = {"default": 0}
    result = run_menu_set(parameter, menu_names=THIRTY, menu_labels=THIRTY)
    assert result["after"] == {"value": "D02", "index": 1, "default": "gone"}
    assert "default" not in parameter.writes


def test_menu_set_restores_an_orphan_default_menu_after_a_failed_write():
    parameter = FakeMenuParameter(OLD_SCENES, OLD_LABELS, "D02", "gone")
    parameter.failures = {"val": 0}
    with pytest.raises(module.AgentCommandError, match="parameter_write_rejected"):
        run_menu_set(parameter)
    assert menu_state(parameter) == (OLD_SCENES, OLD_LABELS, "D02", "gone")
    assert "default" not in parameter.writes


@pytest.mark.parametrize(
    ("failures", "corrupt_labels"),
    [({"default": 0, "menuNames": 2}, set()), ({"default": 0}, {2})],
)
def test_menu_set_reports_a_failed_or_unverified_rollback(failures, corrupt_labels):
    parameter = _menu("D02")
    parameter.failures = failures
    parameter.corrupt_labels = corrupt_labels
    with pytest.raises(module.AgentCommandError, match="parameter_rollback_failed"):
        run_menu_set(parameter)


def _vanishing(*targets):
    lookups = iter(targets)
    return lambda path: next(lookups)


def test_menu_set_target_vanishing_after_a_failed_write_is_unknown():
    parameter = _menu("D02")
    parameter.failures = {"default": 0}
    comp = MenuComp(parameter)
    with pytest.raises(module.AgentCommandError, match="parameter_outcome_unknown"):
        run_menu_set(parameter, lookup=_vanishing(comp, None))


def test_menu_set_target_vanishing_during_rollback_is_unknown():
    parameter = _menu("D02")
    parameter.failures = {"default": 0, "menuNames": 2}
    comp = MenuComp(parameter)
    with pytest.raises(module.AgentCommandError, match="parameter_outcome_unknown"):
        run_menu_set(parameter, lookup=_vanishing(comp, comp, None))


@pytest.mark.parametrize(
    "replacement",
    [
        MenuComp(_menu(), operator_id=2),
        SimpleNamespace(path="/project1/controls", id=1, par=SimpleNamespace()),
    ],
    ids=["operator-replaced", "parameter-destroyed"],
)
def test_menu_set_never_restores_onto_a_different_target(replacement):
    parameter = _menu("D02")
    parameter.failures = {"default": 0}
    comp = MenuComp(parameter)
    foreign = menu_state(replacement.par.Scene) if hasattr(replacement.par, "Scene") else None
    with pytest.raises(module.AgentCommandError, match="parameter_outcome_unknown"):
        run_menu_set(parameter, lookup=_vanishing(comp, replacement))
    if foreign is not None:
        assert menu_state(replacement.par.Scene) == foreign


def test_menu_set_pins_an_implicit_default_only_when_the_policy_moves_it():
    moved = FakeMenuParameter(OLD_SCENES, OLD_LABELS, "D05", None)
    result = run_menu_set(
        moved, menu_names=["D05", "D10", "D02"], menu_labels=["a", "b", "c"], preserve="name"
    )
    assert result["after"]["default"] == "D10"
    assert moved._default == "D10"
    kept = FakeMenuParameter(OLD_SCENES, OLD_LABELS, "D05", None)
    assert run_menu_set(kept)["after"]["default"] == "D01"
    assert kept._default is None


def test_menu_set_cannot_return_a_pinned_implicit_default_to_implicit():
    parameter = FakeMenuParameter(OLD_SCENES, OLD_LABELS, "D05", None)
    parameter.failures = {"val": 0}
    with pytest.raises(module.AgentCommandError, match="parameter_rollback_failed"):
        run_menu_set(
            parameter,
            menu_names=["D05", "D10", "D02"],
            menu_labels=["a", "b", "c"],
            preserve="name",
        )
    assert menu_state(parameter) == (OLD_SCENES, OLD_LABELS, "D05", "D10")


def test_menu_set_treats_a_rejected_attempt_to_pin_an_implicit_default_as_pinned():
    # TD may have applied a default write that raised, so the attempt alone is reported.
    parameter = FakeMenuParameter(OLD_SCENES, OLD_LABELS, "D05", None)
    parameter.failures = {"default": 0}
    with pytest.raises(module.AgentCommandError, match="parameter_rollback_failed"):
        run_menu_set(
            parameter, menu_names=["D05", "D10", "D02"], menu_labels=["a", "b", "c"], preserve="name"
        )
    assert menu_state(parameter) == (OLD_SCENES, OLD_LABELS, "D05", "D10")
    assert parameter._default is None


def test_menu_set_restores_an_untouched_implicit_default_exactly():
    parameter = FakeMenuParameter(OLD_SCENES, OLD_LABELS, "D05", None)
    parameter.failures = {"val": 0}
    with pytest.raises(module.AgentCommandError, match="parameter_write_rejected"):
        run_menu_set(parameter)
    assert menu_state(parameter) == (OLD_SCENES, OLD_LABELS, "D05", "D10")
    assert parameter._default is None


class DestroyedDuringWriteComp(MenuComp):
    """A TD OP whose Python wrapper raises once the node is destroyed by the failed write."""

    @property
    def path(self):
        if "default" in self.par.Scene.writes:
            raise RuntimeError("tdError: Invalid OP object")
        return "/project1/controls"

    @property
    def id(self):
        return 1 if "default" not in self.par.Scene.writes else self.path

    @id.setter
    def id(self, value):
        pass


def test_menu_set_rollback_uses_the_identity_captured_before_mutation():
    parameter = _menu("D02")
    parameter.failures = {"default": 0}
    comp = DestroyedDuringWriteComp(parameter)
    with pytest.raises(module.AgentCommandError, match="parameter_outcome_unknown"):
        run_menu_set(parameter, lookup=_vanishing(comp, None))
