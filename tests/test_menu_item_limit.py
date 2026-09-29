"""Issue 151: custom menus up to 256 items inside a 64 KiB ASCII-escaped JSON budget,
and `invalid_arguments` details that name the violated field."""

import json

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from td_cli import cli
from td_cli.client import MAX_VALIDATION_ERRORS, ClientError
from td_cli.command_catalog import COMMAND_CATALOG, MAX_MENU_JSON_BYTES
from td_cli.command_run import CommandPlan, read_plan
from td_cli.protocol import Command


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
    assert errors[0]["location"] == ["commands", 1, "menu_names"]
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
    assert [item["location"] for item in errors] == [["menu_names"], ["menu_labels"]]
    assert all(item["type"] == "too_long" and "256" in item["message"] for item in errors)
    assert "D001" not in result.stdout

    result = invoke_menu_set(
        monkeypatch, tmp_path, menu_set_input(**budget_menu(MAX_MENU_JSON_BYTES + 1))
    )
    assert result.exit_code == 2
    [item] = json.loads(result.stdout)["error"]["details"]["validation_errors"]
    assert item["location"] == []
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
