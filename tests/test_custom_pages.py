from types import SimpleNamespace

import pytest
from test_agent_runtime import FakeParameter, make_control, module

from td_cli.command_catalog import COMMAND_CATALOG
from td_cli.protocol import Command


def test_typed_page_contract_accepts_scalar_controls_and_rejects_invalid_definitions():
    payload = {
        "operator_path": "/project1/controls",
        "page": "Controls",
        "parameters": [
            {
                "name": "Gyrox",
                "label": "Gyro X",
                "kind": "float",
                "default": 0.0,
                "minimum": -1.0,
                "maximum": 1.0,
            },
            {"name": "Manual", "label": "Manual", "kind": "toggle", "default": True},
            {
                "name": "Source",
                "label": "Source",
                "kind": "menu",
                "default": "manual",
                "menu_names": ["manual", "device"],
                "menu_labels": ["Manual", "Device"],
            },
        ],
    }
    normalized = COMMAND_CATALOG.validate_input("parameters.page.create", payload)
    assert (
        Command.model_validate(
            {"name": "parameters.page.create", "input": payload}
        ).input.model_dump()
        == normalized
    )
    assert len(normalized["parameters"]) == 3
    for patch in [
        {"default": 2.0},
        {"minimum": 2.0},
        {"default": True},
        {"name": "Bad_Name"},
        {"menu_names": ["x"]},
    ]:
        with pytest.raises(ValueError):
            COMMAND_CATALOG.validate_input(
                "parameters.page.create",
                {
                    **payload,
                    "parameters": [{**payload["parameters"][0], **patch}],
                },
            )
    with pytest.raises(ValueError):
        COMMAND_CATALOG.validate_input(
            "parameters.page.create",
            {
                **payload,
                "parameters": [payload["parameters"][0]] * 2,
            },
        )


class ExternalPage:
    def __init__(self, owner, name):
        self.owner = owner
        self.name = name
        self.names = []

    def appendFloat(self, name, *, label, replace):
        assert replace is False
        parameter = FakeParameter(0.0)
        parameter.name, parameter.label, parameter.page = name, label, self
        parameter.style = "Float"
        setattr(self.owner.par, name, parameter)
        self.names.append(name)
        if self.owner.fail_append:
            raise RuntimeError("TD failed after adding parameter")
        return [parameter]

    def destroy(self):
        if self.owner.fail_destroy:
            raise RuntimeError("TD failed destroying page")
        for name in self.names:
            delattr(self.owner.par, name)
        self.owner.customPages.remove(self)


class ExternalComp:
    path = "/project1/controls"
    isCOMP = True
    fail_append = False
    fail_destroy = False

    def __init__(self):
        self.par = SimpleNamespace(Existing=FakeParameter(0.5))
        self.pages = []
        self.customPages = []

    def appendCustomPage(self, name):
        page = ExternalPage(self, name)
        self.customPages.append(page)
        return page


def create_command():
    return {
        "name": "parameters.page.create",
        "input": {
            "operator_path": ExternalComp.path,
            "page": "Controls",
            "parameters": [
                {
                    "name": "Gyrox",
                    "label": "Gyro X",
                    "kind": "float",
                    "default": 0.0,
                    "minimum": -1.0,
                    "maximum": 1.0,
                }
            ],
        },
    }


def test_display_render_patch_is_typed_and_verified():
    from test_agent_runtime import FakeOperator

    operator = FakeOperator("/project1/shape", family="SOP")
    operator.display = operator.render = False
    command = Command.model_validate(
        {
            "name": "ops.state.set",
            "input": {"operator_path": operator.path, "display": True, "render": True},
        }
    )
    result = make_control(lambda path: operator).execute(command.model_dump())
    assert result["state"]["display"] is True
    assert result["state"]["render"] is True


def test_page_creation_returns_readable_range_and_duplicate_preserves_existing_page():
    comp = ExternalComp()
    control = make_control(lambda path: comp)
    result = control.execute(create_command())
    assert result["parameters"][0]["descriptor"]["bounds"]["minimum"] == -1
    assert result["parameters"][0]["value"]["value"] == 0
    with pytest.raises(module.AgentCommandError, match="parameter_page_exists"):
        control.execute(create_command())
    assert comp.par.Gyrox.eval() == 0
    assert comp.par.Existing.eval() == 0.5
    assert len(comp.customPages) == 1


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_partial_creation_reports_rollback_and_preserves_unrelated_controls(rollback_fails):
    comp = ExternalComp()
    comp.fail_append = True
    comp.fail_destroy = rollback_fails
    control = make_control(lambda path: comp)
    code = "parameter_page_rollback_failed" if rollback_fails else "parameter_page_failed"
    with pytest.raises(module.AgentCommandError, match=code):
        control.execute(create_command())
    assert comp.par.Existing.eval() == 0.5
    assert len(comp.customPages) == int(rollback_fails)


def test_target_disappearing_during_rollback_is_unknown():
    comp = ExternalComp()
    comp.fail_append = True
    comp.fail_destroy = True
    lookups = iter([comp, comp, None])
    control = make_control(lambda path: next(lookups))
    with pytest.raises(module.AgentCommandError, match="parameter_page_outcome_unknown"):
        control.execute(create_command())


def test_large_menu_metadata_is_rejected_before_mutation():
    names = ["n" * 125 + str(i).zfill(3) for i in range(32)]
    with pytest.raises(ValueError, match="JSON budget"):
        COMMAND_CATALOG.validate_input(
            "parameters.page.create",
            {
                "operator_path": "/project1/controls",
                "page": "Controls",
                "parameters": [
                    {
                        "name": f"P{i}",
                        "label": "Control",
                        "kind": "menu",
                        "default": names[0],
                        "menu_names": names,
                        "menu_labels": ["霧" * 128] * 32,
                    }
                    for i in range(32)
                ],
            },
        )


def test_repeated_result_paths_count_toward_page_budget():
    with pytest.raises(ValueError, match="JSON budget"):
        COMMAND_CATALOG.validate_input(
            "parameters.page.create",
            {
                "operator_path": "/" + "x" * 10000,
                "page": "Controls",
                "parameters": [
                    {"name": f"P{i}", "label": "Control", "kind": "toggle", "default": False}
                    for i in range(32)
                ],
            },
        )
