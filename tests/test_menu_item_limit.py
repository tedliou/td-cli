"""Issue 151: custom menus up to 256 items inside a 64 KiB ASCII-escaped JSON budget,
and `invalid_arguments` details that name the violated field."""

import json

import pytest
from pydantic import ValidationError
from test_agent_runtime import AgentExt, FakeOwner, execute_v2, isolated_touchdesigner_runtime
from test_custom_menu_options import FakeMenuParameter, MenuComp
from typer.testing import CliRunner

from td_cli import cli
from td_cli.client import MAX_VALIDATION_ERRORS, ClientError
from td_cli.command_catalog import COMMAND_CATALOG, MAX_MENU_JSON_BYTES
from td_cli.command_run import CommandPlan, read_plan
from td_cli.protocol import Command

__all__ = ["isolated_touchdesigner_runtime"]


def menu_set_input(**overrides):
    return {
        "operator_path": "/project1/dream_controls",
        "parameter": "Scene",
        "menu_names": ["D01"],
        "menu_labels": ["01"],
        "preserve": "index",
        **overrides,
    }


def numbered_menu(count):
    return {
        "menu_names": [f"D{i:03}" for i in range(1, count + 1)],
        "menu_labels": [f"{i:03} 靜水漂浮" for i in range(1, count + 1)],
    }


def menu_json_bytes(names, labels):
    return len(json.dumps(names + labels, ensure_ascii=True).encode("ascii"))


def budget_menu(target_bytes):
    """256 unique names whose ASCII-escaped JSON with the labels is exactly ``target_bytes``."""
    names = [f"D{i:03}" for i in range(256)]
    labels = ["霧" * 40] * 256
    shortfall = target_bytes - menu_json_bytes(names, labels)
    assert shortfall >= 0
    names = [
        name + "x" * (shortfall // 256 + (index < shortfall % 256))
        for index, name in enumerate(names)
    ]
    assert menu_json_bytes(names, labels) == target_bytes
    return {"menu_names": names, "menu_labels": labels}


@pytest.mark.parametrize("count", [33, 38, 256])
def test_menu_set_accepts_up_to_256_items_in_commands_and_plans(count):
    payload = menu_set_input(**numbered_menu(count))
    assert COMMAND_CATALOG.validate_input("parameters.menu.set", payload) == payload
    command = Command.model_validate({"name": "parameters.menu.set", "input": payload})
    assert command.input.model_dump() == payload
    plan = CommandPlan.model_validate(
        {"commands": [{"name": "parameters.menu.set", "input": payload}]}
    )
    assert plan.commands[0].input.model_dump() == payload


def test_menu_set_rejects_257_items_and_the_json_budget_is_inclusive():
    with pytest.raises(ValueError, match="at most 256 items"):
        COMMAND_CATALOG.validate_input("parameters.menu.set", menu_set_input(**numbered_menu(257)))
    at_budget = menu_set_input(**budget_menu(MAX_MENU_JSON_BYTES))
    assert COMMAND_CATALOG.validate_input("parameters.menu.set", at_budget) == at_budget
    with pytest.raises(ValueError, match="65536-byte JSON budget"):
        COMMAND_CATALOG.validate_input(
            "parameters.menu.set", menu_set_input(**budget_menu(MAX_MENU_JSON_BYTES + 1))
        )


def test_page_create_menu_definitions_share_the_256_item_limit():
    def page(count):
        return {
            "operator_path": "/project1/controls",
            "page": "Preview",
            "parameters": [
                {
                    "name": "Scene",
                    "label": "Scene",
                    "kind": "menu",
                    "default": "D001",
                    "menu_names": [f"D{i:03}" for i in range(1, count + 1)],
                    "menu_labels": [f"{i:03}" for i in range(1, count + 1)],
                }
            ],
        }

    assert COMMAND_CATALOG.validate_input("parameters.page.create", page(256)) == page(256)
    with pytest.raises(ValueError, match="at most 256 items"):
        COMMAND_CATALOG.validate_input("parameters.page.create", page(257))


def write_plan(tmp_path, payload):
    path = tmp_path / "plan.json"
    commands = [
        {"name": "ops.get", "input": {"operator_path": "/project1"}},
        {"name": "parameters.menu.set", "input": payload},
    ]
    path.write_text(json.dumps({"commands": commands}, ensure_ascii=False), encoding="utf-8")
    return path


def test_plan_validation_error_names_the_command_field_and_limit(tmp_path):
    assert len(read_plan(write_plan(tmp_path, menu_set_input(**numbered_menu(38))))) == 2
    with pytest.raises(ClientError) as raised:
        read_plan(write_plan(tmp_path, menu_set_input(**numbered_menu(257))))
    assert raised.value.code == "invalid_arguments"
    errors = raised.value.details["validation_errors"]
    assert errors[0]["location"] == ["commands", 1, "input", "menu_names"]
    assert errors[0]["type"] == "too_long"
    assert "256" in errors[0]["message"]
    assert "D001" not in json.dumps(raised.value.details)


class RecordingDaemonClient:
    submitted = None

    def __init__(self, **kwargs):
        del kwargs

    def select_instance(self, selector, *, online_only=True):
        del selector, online_only
        return {"instance_id": "instance-1"}

    def submit(self, request_id, instance_id, command):
        self.__class__.submitted = command
        return {"request_id": request_id, "instance_id": instance_id, "status": "queued"}

    def wait(self, request_id):
        return {"request_id": request_id, "status": "succeeded", "result": {}}


def invoke_menu_set(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(cli, "DaemonClient", RecordingDaemonClient)
    monkeypatch.setattr(RecordingDaemonClient, "submitted", None)
    path = tmp_path / "menu.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    argv = ["--json", "parameters", "menu-set", "--input-file", str(path)]
    return CliRunner().invoke(cli.app, argv)


def test_cli_submits_a_38_item_menu(monkeypatch, tmp_path):
    payload = menu_set_input(**numbered_menu(38))
    result = invoke_menu_set(monkeypatch, tmp_path, payload)
    assert result.exit_code == 0, result.output
    assert RecordingDaemonClient.submitted == {"name": "parameters.menu.set", "input": payload}


def test_cli_rejection_names_each_violated_field_without_echoing_input(monkeypatch, tmp_path):
    result = invoke_menu_set(monkeypatch, tmp_path, menu_set_input(**numbered_menu(257)))
    assert result.exit_code == 2
    assert RecordingDaemonClient.submitted is None
    envelope = json.loads(result.stdout)
    assert envelope["error"]["code"] == "invalid_arguments"
    assert "request" not in envelope
    errors = envelope["error"]["details"]["validation_errors"]
    assert [item["location"] for item in errors] == [
        ["input", "menu_names"],
        ["input", "menu_labels"],
    ]
    assert all(item["type"] == "too_long" and "256" in item["message"] for item in errors)
    assert "D001" not in result.stdout

    result = invoke_menu_set(
        monkeypatch, tmp_path, menu_set_input(**budget_menu(MAX_MENU_JSON_BYTES + 1))
    )
    assert result.exit_code == 2
    [item] = json.loads(result.stdout)["error"]["details"]["validation_errors"]
    assert item["location"] == ["input"]
    assert item["type"] == "value_error"
    assert "65536-byte JSON budget" in item["message"]


def test_validation_details_are_bounded():
    payload = {
        "operator_path": "/project1/controls",
        "page": "Controls",
        "parameters": [
            {"name": "x" * 40, "label": "", "kind": "toggle", "default": "no"} for _ in range(20)
        ],
    }
    with pytest.raises(ValidationError) as raised:
        Command.model_validate({"name": "parameters.page.create", "input": payload})
    assert len(raised.value.errors()) > MAX_VALIDATION_ERRORS
    details = ClientError.invalid_arguments(raised.value).details
    assert len(details["validation_errors"]) == MAX_VALIDATION_ERRORS
    assert details["validation_errors_truncated"] is True
    assert all(len(item["message"]) <= 256 for item in details["validation_errors"])
    assert all(
        set(item) == {"location", "type", "message"} for item in details["validation_errors"]
    )


def details_for(command):
    with pytest.raises(ValidationError) as raised:
        Command.model_validate(command)
    return ClientError.invalid_arguments(raised.value).details


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {
                "operator_path": "/project1/controls",
                "page": "Controls",
                "parameters": [
                    {"name": "Gain", "label": "Gain", "kind": "SECRET_TAG_VALUE", "default": 1}
                ],
            },
            "does not match any of the expected tags: 'float', 'toggle', 'menu'",
        ),
        (
            {"operator_path": "/project1/controls", "text": "SECRET\ud800"},
            "Input failed value_error validation",
        ),
    ],
)
def test_messages_never_echo_rejected_values(payload, expected):
    name = "parameters.page.create" if "page" in payload else "dat.text.set"
    details = details_for({"name": name, "input": payload})
    assert "SECRET" not in json.dumps(details)
    assert any(expected in item["message"] for item in details["validation_errors"])


def test_locations_are_bounded_and_relative_to_the_command():
    key = "k" * 1000
    details = details_for({"name": "ops.get", "input": {"operator_path": "/project1", key: 1}})
    [item] = details["validation_errors"]
    assert item["location"] == ["input", "k" * 64]
    assert item["type"] == "extra_forbidden"
    [item] = details_for({"name": 5, "input": {}})["validation_errors"]
    assert item["location"] == ["name"]
    create = {"parent_path": "/project1", "op_type": "baseCOMP", "name": 5}
    [item] = details_for({"name": "ops.create", "input": create})["validation_errors"]
    assert item["location"] == ["input", "name"]
    [item] = details_for({"name": "ops.get"})["validation_errors"]
    assert item["location"] == ["input"]


@pytest.mark.parametrize(
    ("menu", "message"),
    [
        ({"menu_names": ["D01", "D02"]}, "same number of items"),
        ({"menu_names": ["D01", "D01"], "menu_labels": ["a", "b"]}, "must be unique"),
        ({"menu_labels": [""]}, "must not be empty"),
        ({"menu_labels": ["x" * 129]}, "at most 128 characters"),
    ],
)
def test_each_menu_rule_states_its_own_limit(menu, message):
    details = details_for({"name": "parameters.menu.set", "input": menu_set_input(**menu)})
    [item] = details["validation_errors"]
    assert item["location"] == ["input"]
    assert message in item["message"]


def test_budget_menu_outcome_stays_far_below_the_outcome_limit(isolated_touchdesigner_runtime):
    menu = budget_menu(MAX_MENU_JSON_BYTES)
    menu["menu_labels"] = ["\U0001f319" * 20] * 256
    shortfall = MAX_MENU_JSON_BYTES - menu_json_bytes(menu["menu_names"], menu["menu_labels"])
    menu["menu_names"][0] += "y" * shortfall
    assert menu_json_bytes(menu["menu_names"], menu["menu_labels"]) == MAX_MENU_JSON_BYTES
    parameter = FakeMenuParameter(["D01", "D02"], ["a", "b"], "D02", "D01")
    comp = MenuComp(parameter)
    comp.path = "/project1/dream_controls"
    agent = AgentExt(FakeOwner(), operator_lookup=lambda path: comp)
    agent.connection_id = "connection-1"
    outcome = execute_v2(
        agent, "budget", {"name": "parameters.menu.set", "input": menu_set_input(**menu)}
    )
    assert outcome["status"] == "succeeded", outcome["error"]
    size = len(json.dumps(outcome, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    assert outcome["result"]["menu_labels"] == menu["menu_labels"]
    assert size < agent.MAX_OUTCOME_BYTES // 2
