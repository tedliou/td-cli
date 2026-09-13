import json

import pytest
from typer.testing import CliRunner

from td_cli import cli
from td_cli.client import ClientError


class FakeClient:
    def __init__(self, statuses=None, submit_error=False):
        self.statuses = statuses or ["succeeded"] * 3
        self.submit_error = submit_error
        self.calls = []
        self.selected = 0

    def select_instance(self, selector):
        self.selected += 1
        return {"instance_id": "instance-1"}

    def submit(self, request_id, instance_id, command):
        self.calls.append((request_id, command))
        if self.submit_error:
            raise ClientError("daemon_unavailable")

    def wait(self, request_id):
        status = self.statuses[len(self.calls) - 1]
        return {
            "request_id": request_id,
            "status": status,
            "result": {"path": "/project1/test"} if status == "succeeded" else None,
            "error": None if status == "succeeded" else {"code": "operator_not_found"},
        }


def invoke(tmp_path, monkeypatch, commands, client):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"commands": commands}), encoding="utf-8")
    monkeypatch.setattr(cli, "_client", lambda ctx: client)
    result = CliRunner().invoke(
        cli.app, ["--json", "commands", "execute", "--input-file", str(path)]
    )
    return result, [json.loads(line) for line in result.stdout.splitlines()]


READ = {"name": "ops.get", "input": {"operator_path": "/project1"}}
CREATE = {
    "name": "ops.create",
    "input": {"parent_path": "/project1", "op_type": "baseCOMP", "name": "test"},
}


def test_ordered_public_cli_progress(tmp_path, monkeypatch):
    client = FakeClient()
    result, events = invoke(tmp_path, monkeypatch, [CREATE, READ], client)
    assert result.exit_code == 0, result.output
    assert client.selected == 1
    assert [command["name"] for _, command in client.calls] == ["ops.create", "ops.get"]
    assert [event["event"] for event in events] == [
        "submitting",
        "completed",
        "submitting",
        "completed",
        "finished",
    ]
    assert [events[i]["index"] for i in (0, 2)] == [0, 1]
    assert events[0]["request_id"] == events[1]["request"]["request_id"]


@pytest.mark.parametrize("status", ["failed", "unknown", "instance_offline"])
def test_stop_first_non_success(tmp_path, monkeypatch, status):
    client = FakeClient(["succeeded", status])
    result, events = invoke(tmp_path, monkeypatch, [READ] * 3, client)
    assert result.exit_code != 0
    assert len(client.calls) == 2
    stopped = next(event for event in events if event.get("event") == "stopped")
    assert stopped["succeeded"] == 1 and stopped["unsubmitted"] == 1
    assert stopped["request_id"] == client.calls[-1][0]
    assert not any(event.get("event") == "finished" for event in events)


def test_transport_error_retains_id_without_retry(tmp_path, monkeypatch):
    client = FakeClient(submit_error=True)
    result, events = invoke(tmp_path, monkeypatch, [READ] * 3, client)
    assert result.exit_code == 3
    assert len(client.calls) == 1
    assert events[1]["request_id"] == events[0]["request_id"]
    assert events[1]["unsubmitted"] == 2


@pytest.mark.parametrize(
    "commands",
    [
        [],
        [READ] * 257,
        [READ, {"name": "exec", "input": {}}],
        [{"name": "batch.execute", "input": {"commands": [READ]}}],
    ],
)
def test_validate_entire_plan_before_network(tmp_path, monkeypatch, commands):
    client = FakeClient()
    result, _ = invoke(tmp_path, monkeypatch, commands, client)
    assert result.exit_code == 2
    assert client.selected == 0 and not client.calls


def test_total_deadline_stops_before_next_submission(tmp_path, monkeypatch):
    from td_cli import command_run

    times = iter([0, 0, 31])
    monkeypatch.setattr(command_run.time, "monotonic", lambda: next(times))
    client = FakeClient()
    result, events = invoke(tmp_path, monkeypatch, [READ] * 2, client)
    assert result.exit_code == 6
    assert len(client.calls) == 1
    stopped = next(event for event in events if event.get("event") == "stopped")
    assert stopped["request_id"] is None and stopped["unsubmitted"] == 1


@pytest.mark.parametrize("as_json", [False, True])
def test_selection_error_emits_stopped(tmp_path, monkeypatch, as_json):
    client = FakeClient()

    def fail_select(selector):
        raise ClientError("instance_not_found")

    monkeypatch.setattr(client, "select_instance", fail_select)
    monkeypatch.setattr(cli, "_client", lambda ctx: client)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"commands": [READ]}), encoding="utf-8")
    result = CliRunner().invoke(
        cli.app,
        (["--json"] if as_json else []) + ["commands", "execute", "--input-file", str(path)],
    )
    assert result.exit_code == 4
    stopped = json.loads(result.stdout.splitlines()[0])
    assert stopped["event"] == "stopped"
    assert stopped["request_id"] is None
    assert stopped["succeeded"] == 0 and stopped["unsubmitted"] == 1
    assert not client.calls


def test_interrupt_preserves_submitting_id(tmp_path, monkeypatch):
    client = FakeClient()

    def interrupt(request_id, instance_id, command):
        raise KeyboardInterrupt

    monkeypatch.setattr(client, "submit", interrupt)
    result, events = invoke(tmp_path, monkeypatch, [READ] * 2, client)
    assert result.exit_code != 0
    assert len(events) == 1
    assert events[0]["event"] == "submitting" and events[0]["request_id"]


def test_oversized_plan_rejected_before_network(tmp_path, monkeypatch):
    from td_cli.command_run import MAX_PLAN_BYTES, read_plan

    path = tmp_path / "large.json"
    path.write_bytes(b" " * (MAX_PLAN_BYTES + 1))
    with pytest.raises(ClientError, match="invalid_arguments"):
        read_plan(path)
