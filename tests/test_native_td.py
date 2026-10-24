"""The native td must behave exactly like the Python td (ADR 0003).

Each case runs the same argv through both CLIs against a fake Daemon and compares exit code,
stdout, stderr and the requests the Daemon received. Build the native td first:
``cargo build --release --examples`` in ``native/td``; ``TD_NATIVE_EXE`` overrides its path.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "native" / "td" / "target" / "release"
NATIVE = Path(os.environ.get("TD_NATIVE_EXE", RELEASE / "td.exe"))
FAKE_TD_DAEMON = NATIVE.parent / "examples" / "fake_td_daemon.exe"
TOKEN = "a" * 64
REQUEST_ID = "01890a5d-ac96-774b-bcce-b302099a8057"
UUID7 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
# The Python td, pointed at the fake Daemon without changing its code.
PYTHON_TD = """
import sys
from td_cli import client
client.DaemonClient.__init__.__kwdefaults__["endpoint"] = sys.argv[1]
sys.argv = ["td", *sys.argv[2:]]
from td_cli.cli import run
run()
"""

pytestmark = pytest.mark.skipif(not NATIVE.exists(), reason=f"native td not built at {NATIVE}")

HEALTH = {
    "ready": True,
    "logging_healthy": True,
    "release_version": "0.11.0",
    "protocol_versions": [3],
    "schema_version": 2,
}
INSTANCE = {
    "instance_id": "7b0c3f5e-3c1d-4d55-9a8e-0c8b2f1d7a10",
    "selector": "專案 one",
    "status": "online",
    "protocol_version": 3,
    "fps": 59.94,
    "build": "2025.32050",
}


def snapshot(status="succeeded", **fields):
    return {
        "request_id": REQUEST_ID,
        "instance_id": INSTANCE["instance_id"],
        "command": {"name": "ops.get", "input": {"operator_path": "/project1"}},
        "status": status,
        "result": None,
        "error": None,
        **fields,
    }


Route = dict[tuple[str, str], list[tuple]]


@dataclass
class Received:
    method: str
    path: str
    authorization: str | None
    body: object
    port: int


# A scripted response that closes the connection without answering.
DROP = (0, None)
# Append to a response to close the connection after it, without saying so in a header.
CLOSE = "close"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@dataclass
class FakeDaemon:
    """Answer each (method, path) with its scripted responses in turn; the last one repeats.

    With ``start_after`` the port stays closed that long, so first connections are refused.
    """

    routes: Route
    start_after: float = 0.0
    received: list[Received] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.routes = {key: list(value) for key, value in self.routes.items()}
        daemon = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:
                del args

            def answer(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                route = self.path.split("?", 1)[0]
                daemon.received.append(
                    Received(
                        self.command,
                        self.path,
                        self.headers.get("Authorization"),
                        json.loads(raw) if raw else None,
                        self.client_address[1],
                    )
                )
                responses = daemon.routes.get((self.command, route)) or [
                    (404, {"detail": "Not Found"})
                ]
                status, payload, *after = responses.pop(0) if len(responses) > 1 else responses[0]
                if (status, payload) == DROP:
                    self.close_connection = True
                    return
                content = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                if CLOSE in after:
                    self.close_connection = True

            do_GET = answer
            do_POST = answer

        self.port = free_port() if self.start_after else 0
        self.server: ThreadingHTTPServer | None = None
        self.ready = threading.Event()

        def serve() -> None:
            time.sleep(self.start_after)
            self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
            self.server.daemon_threads = True
            self.port = self.server.server_address[1]
            self.ready.set()
            self.server.serve_forever()

        threading.Thread(target=serve, daemon=True).start()
        if not self.start_after:
            self.ready.wait(5)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        assert self.ready.wait(5) and self.server is not None
        self.server.shutdown()
        self.server.server_close()


@dataclass
class Run:
    exit_code: int
    stdout: str
    stderr: str
    requests: list[tuple[str, str, object]]
    connections: int


def run(
    kind: str,
    argv: list[str],
    routes: Route,
    home: Path,
    *,
    stdin: bytes | None = None,
    exe: Path = NATIVE,
    env: dict[str, str] | None = None,
    start_after: float = 0.0,
) -> Run:
    daemon = FakeDaemon(routes, start_after)
    try:
        command = (
            [sys.executable, "-c", PYTHON_TD, daemon.url, *argv]
            if kind == "python"
            else [str(exe), *argv]
        )
        environment = {
            **os.environ,
            "LOCALAPPDATA": str(home),
            "TDCLI_TEST_ENDPOINT": daemon.url,
            **(env or {}),
        }
        completed = subprocess.run(
            command, input=stdin, capture_output=True, env=environment, timeout=60, check=False
        )
    finally:
        daemon.close()
    assert all(item.authorization == f"Bearer {TOKEN}" for item in daemon.received)
    generated = {
        item.body["request_id"]
        for item in daemon.received
        if isinstance(item.body, dict)
        and UUID7.fullmatch(str(item.body.get("request_id")))
        and item.body["request_id"] != REQUEST_ID
    }

    def normalized(text: str) -> str:
        for request_id in generated:
            text = text.replace(request_id, "<generated>")
        return text

    return Run(
        completed.returncode,
        normalized(completed.stdout.decode()),
        normalized(completed.stderr.decode()),
        [
            (item.method, normalized(item.path), json.loads(normalized(json.dumps(item.body))))
            for item in daemon.received
        ],
        len({item.port for item in daemon.received}),
    )


@pytest.fixture
def home(tmp_path):
    state = tmp_path / "touchdesigner-cli" / "state"
    state.mkdir(parents=True)
    (state / "auth.token").write_text(TOKEN + "\n", encoding="ascii")
    return tmp_path


def same(
    argv: list[str],
    routes: Route,
    home: Path,
    *,
    stdin: bytes | None = None,
    compare_requests: bool = True,
) -> Run:
    python = run("python", argv, routes, home, stdin=stdin)
    native = run("native", argv, routes, home, stdin=stdin)
    assert (native.exit_code, native.stdout, native.stderr) == (
        python.exit_code,
        python.stdout,
        python.stderr,
    )
    if compare_requests:
        assert native.requests == python.requests
        assert native.connections == python.connections
    return native


def daemon(**routes) -> Route:
    """Routes named like ``get_v3_health``; a single response or a list of them."""
    table: Route = {("GET", "/v3/health"): [(200, HEALTH)]}
    for name, responses in routes.items():
        method, _, path = name.partition("_")
        key = (method.upper(), "/" + path.replace("__", "/").replace("_", "-"))
        table[key] = responses if isinstance(responses, list) else [responses]
    return table


INSTANCES = {("GET", "/v3/instances"): [(200, [INSTANCE, {**INSTANCE, "status": "offline"}])]}


@pytest.mark.parametrize(
    ("argv", "routes"),
    [
        (["--json", "version"], daemon()),
        (["version", "--json"], daemon()),
        (["version"], daemon()),
        (
            ["--json", "version"],
            {("GET", "/v3/health"): [(200, {**HEALTH, "protocol_versions": [4]})]},
        ),
        (["--json", "version"], {("GET", "/v3/health"): [(200, b"not json")]}),
        (["--json", "--instance", "x", "version"], daemon()),
        (["--version"], daemon()),
        (["--timeout", "0", "version"], daemon()),
        (["--json", "--timeout", "abc", "version"], daemon()),
        (["--timeout", "3601", "--json", "version"], daemon()),
    ],
)
def test_version(argv, routes, home):
    same(argv, routes, home)


@pytest.mark.parametrize(
    "argv",
    [
        ["instances", "list"],
        ["instances", "get", "a"],
        ["requests", "get", REQUEST_ID],
        ["requests", "wait", REQUEST_ID],
    ],
)
def test_queries_reject_an_instance_selector(argv, home):
    native = same(["--json", "--instance", "x", *argv], daemon(), home)
    assert (native.exit_code, native.requests) == (2, [])


def test_version_without_a_token(tmp_path):
    same(["--json", "version"], daemon(), tmp_path)


@pytest.mark.parametrize(
    ("argv", "routes"),
    [
        (["--json", "instances", "list"], INSTANCES),
        (["instances", "list"], INSTANCES),
        (["--json", "instances", "list", "--status", "offline"], INSTANCES),
        (["--json", "instances", "list", "--status", "lost"], INSTANCES),
        (
            ["--json", "instances", "list"],
            {("GET", "/v3/instances"): [(200, [{**INSTANCE, "protocol_version": 2}])]},
        ),
        (
            ["--json", "instances", "get", "專案 one/&x"],
            {("GET", "/v3/instances/resolve"): [(200, INSTANCE)]},
        ),
        (
            ["--json", "instances", "get"],
            {("GET", "/v3/instances/resolve"): [(404, {"detail": "instance_not_found"})]},
        ),
        (
            ["--json", "instances", "get", "a"],
            {("GET", "/v3/instances/resolve"): [(409, {"detail": "instance_selector_ambiguous"})]},
        ),
        (
            ["--json", "instances", "list"],
            {("GET", "/v3/instances"): [(404, {"detail": "Not Found"})]},
        ),
        (["--json", "instances", "list"], {("GET", "/v3/instances"): [(500, b"<html>")]}),
        (
            ["--json", "instances", "list"],
            {("GET", "/v3/instances"): [(500, {"detail": "internal_error"})]},
        ),
        (
            ["--json", "instances", "list"],
            {("GET", "/v3/instances"): [(422, {"detail": [{"loc": "x"}]})]},
        ),
        (["--json", "instances", "list"], {("GET", "/v3/instances"): [(401, {"detail": "nope"})]}),
        (["--json", "instances", "list"], {("GET", "/v3/instances"): [(503, {"detail": None})]}),
        (["--json", "instances", "list"], {("GET", "/v3/instances"): [(500, {"detail": 5})]}),
        (
            ["--json", "instances", "list"],
            {("GET", "/v3/instances"): [(500, {"detail": {"a": [1, "x", None, True, 1.5]}})]},
        ),
        (["--json", "instances", "list"], {("GET", "/v3/instances"): [(418, {})]}),
        (["--json", "instances", "list"], {("GET", "/v3/instances"): [(400, b"not json")]}),
        (
            ["--json", "instances", "list"],
            {("GET", "/v3/instances"): [(409, {"detail": "instance_busy", "details": [1]})]},
        ),
    ],
)
def test_instance_queries(argv, routes, home):
    same(argv, {**daemon(), **routes}, home)


REQUEST = f"/v3/requests/{REQUEST_ID}"


@pytest.mark.parametrize(
    ("argv", "responses"),
    [
        (
            ["--json", "requests", "get", REQUEST_ID],
            [(200, snapshot(result={"name": "é", "x": 1.5, "big": 2**80, "frames": -(2**70)}))],
        ),
        (
            ["requests", "get", REQUEST_ID],
            [(200, snapshot(result={"name": "é\U0001f319\n", "x": 1e-7, "big": 2**80, "y": 1e22}))],
        ),
        (["--json", "requests", "get", REQUEST_ID], [(200, snapshot(status="lost"))]),
        (
            ["--json", "requests", "get", REQUEST_ID],
            [(200, snapshot(status="failed", error={"code": "future_error"}))],
        ),
        (
            ["--json", "requests", "get", REQUEST_ID],
            [
                (
                    200,
                    snapshot(
                        command={"name": "parameters.get", "input": {}},
                        result={"mode": "constant", "value_type": "menu"},
                    ),
                )
            ],
        ),
        (["--json", "requests", "get", REQUEST_ID], [(404, {"detail": "request_not_found"})]),
        (
            ["--json", "requests", "wait", REQUEST_ID],
            [(200, snapshot(status="running")), (200, snapshot(result={"ok": True}))],
        ),
        (
            ["--json", "requests", "wait", REQUEST_ID],
            [
                (
                    200,
                    snapshot(status="failed", error={"code": "operator_not_found", "message": "x"}),
                )
            ],
        ),
        (["--json", "requests", "wait", REQUEST_ID], [(200, snapshot(status="cancelled"))]),
    ],
)
def test_request_reads(argv, responses, home):
    same(argv, {**daemon(), ("GET", REQUEST): responses}, home)


def test_request_wait_times_out_with_the_last_snapshot(home):
    argv = ["--json", "--timeout", "0.4", "requests", "wait", REQUEST_ID]
    native = same(
        argv,
        {**daemon(), ("GET", REQUEST): [(200, snapshot(status="running"))]},
        home,
        compare_requests=False,
    )
    assert native.exit_code == 6
    assert json.loads(native.stdout)["error"]["details"]["request"]["status"] == "running"


ADMITTED = (201, snapshot(status="queued"))


def submission(*waits):
    return {
        **daemon(),
        ("POST", "/v3/requests"): [ADMITTED],
        ("GET", REQUEST): list(waits) or [(200, snapshot(result={"path": "/project1", "名": 1.0}))],
    }


@pytest.mark.parametrize(
    ("argv", "routes", "stdin"),
    [
        (["--json", "ops", "get", "/project1", "--request-id", REQUEST_ID], submission(), None),
        (["ops", "get", "/project1", "--request-id", REQUEST_ID], submission(), None),
        (
            ["--json", "ops", "get", "/project1", "--request-id", REQUEST_ID, "--no-wait"],
            submission(),
            None,
        ),
        (
            ["--json", "--instance", "專案", "ops", "get", "/p", "--request-id", REQUEST_ID],
            submission(),
            None,
        ),
        (
            [
                "--json",
                "ops",
                "get",
                "--input",
                '{"operator_path":"/é","x":1.50e3,"y":-0}',
                "--request-id",
                REQUEST_ID,
            ],
            submission(),
            None,
        ),
        (
            ["--json", "ops", "get", "--input-file", "-", "--request-id", REQUEST_ID],
            submission(),
            b'{"operator_path": "/project1"}\n',
        ),
        (["--json", "ops", "get", "/project1"], submission(), None),
        (
            ["--json", "ops", "get", "/project1", "--request-id", REQUEST_ID],
            submission((200, snapshot(status="failed", error={"code": "operator_not_found"}))),
            None,
        ),
        (
            ["--json", "ops", "get", "/project1", "--request-id", REQUEST_ID],
            {
                **daemon(),
                ("POST", "/v3/requests"): [
                    (
                        422,
                        {
                            "detail": "invalid_arguments",
                            "details": {
                                "validation_errors": [
                                    {"location": ["input"], "type": "x", "message": "m"}
                                ]
                            },
                        },
                    )
                ],
            },
            None,
        ),
        (
            ["--json", "ops", "get", "/project1", "--request-id", REQUEST_ID],
            {**daemon(), ("POST", "/v3/requests"): [(409, {"detail": "instance_busy"})]},
            None,
        ),
        (
            ["--json", "ops", "get", "/project1", "--request-id", REQUEST_ID],
            {**daemon(), ("POST", "/v3/requests"): [(404, {"detail": "instance_not_found"})]},
            None,
        ),
        (["--json", "ops", "get"], submission(), None),
        (["--json", "ops", "get", "/p", "--input", "{}"], submission(), None),
        (["--json", "ops", "get", "--input", "[1]"], submission(), None),
        (["--json", "ops", "get", "--input-file", "missing.json"], submission(), None),
    ],
)
def test_ops_get_submission(argv, routes, stdin, home):
    same(argv, routes, home, stdin=stdin)


def test_one_keep_alive_connection_serves_a_command(home):
    native = same(
        ["--json", "ops", "get", "/project1", "--request-id", REQUEST_ID], submission(), home
    )
    assert [method for method, _, _ in native.requests] == ["GET", "POST", "GET"]
    assert native.connections == 1


def test_an_idle_connection_the_daemon_closed_is_replaced(home):
    routes = {**submission(), ("GET", "/v3/health"): [(200, HEALTH, CLOSE)]}
    native = same(["--json", "ops", "get", "/project1", "--request-id", REQUEST_ID], routes, home)
    assert (native.exit_code, native.connections) == (0, 2)


def test_a_dropped_get_is_not_retried(home):
    routes = {**daemon(), ("GET", REQUEST): [DROP]}
    native = same(["--json", "requests", "get", REQUEST_ID], routes, home)
    assert native.exit_code == 3
    assert [method for method, _, _ in native.requests] == ["GET", "GET"]


def test_a_dropped_submission_is_sent_once(home):
    routes = {**daemon(), ("POST", "/v3/requests"): [DROP]}
    native = same(["--json", "ops", "get", "/project1", "--request-id", REQUEST_ID], routes, home)
    assert native.exit_code == 3
    assert json.loads(native.stdout)["error"]["details"] == {"request_id": REQUEST_ID}
    assert [method for method, _, _ in native.requests] == ["GET", "POST"]


def test_a_refused_daemon_reads_as_unavailable(home):
    # Nothing listens on the port: every GET attempt is refused, then version reports no Daemon.
    port = free_port()
    environment = {**os.environ, "LOCALAPPDATA": str(home)}
    outputs = []
    for command in (
        [sys.executable, "-c", PYTHON_TD, f"http://127.0.0.1:{port}", "--json", "version"],
        [str(NATIVE), "--json", "version"],
    ):
        environment["TDCLI_TEST_ENDPOINT"] = f"http://127.0.0.1:{port}"
        completed = subprocess.run(
            command, capture_output=True, env=environment, timeout=60, check=False
        )
        outputs.append((completed.returncode, completed.stdout, completed.stderr))
    assert outputs[0] == outputs[1]
    assert json.loads(outputs[1][1])["data"]["daemon_release_version"] is None


def test_a_refused_get_is_retried_with_backoff(home):
    # The Python td starts too slowly to meet a port that opens 0.2 s later, so only the native
    # timing is checked: attempts at 0, 0.1 and 0.4 s, the last one answered.
    started = time.perf_counter()
    native = run("native", ["--json", "version"], daemon(), home, start_after=0.2)
    assert native.exit_code == 0, native.stderr
    assert json.loads(native.stdout)["data"]["daemon_release_version"] == "0.11.0"
    assert time.perf_counter() - started >= 0.4
    assert len(native.requests) == 1


def test_long_polls_ask_for_the_rest_of_the_command_deadline(home):
    argv = ["--json", "--timeout", "10", "requests", "wait", REQUEST_ID]
    routes = {
        **daemon(),
        ("GET", REQUEST): [(200, snapshot(status="running")), (200, snapshot(result={}))],
    }
    for kind in ("python", "native"):
        result = run(kind, argv, routes, home)
        assert result.exit_code == 0, result.stderr
        polls = [path for method, path, _ in result.requests if path.startswith(REQUEST)]
        waits = [int(path.split("wait_ms=")[1]) for path in polls]
        assert len(waits) == 2 and all(8_500 < wait <= 9_900 for wait in waits), (kind, waits)
        assert waits[1] < waits[0]


# The Python td would start a real Daemon here, so the native start path is checked alone.


@pytest.fixture
def native_beside_fake_daemon(tmp_path):
    if not FAKE_TD_DAEMON.exists():
        pytest.skip(f"fake td-daemon not built at {FAKE_TD_DAEMON}")
    folder = tmp_path / "bin"
    folder.mkdir()
    shutil.copy(NATIVE, folder / "td.exe")
    shutil.copy(FAKE_TD_DAEMON, folder / "td-daemon.exe")
    return folder / "td.exe"


def test_missing_daemon_is_started_with_the_remaining_deadline(native_beside_fake_daemon, tmp_path):
    log = tmp_path / "start.log"
    home = tmp_path / "home"
    result = run(
        "native",
        ["--json", "--timeout", "8", "instances", "list"],
        INSTANCES,
        home,
        exe=native_beside_fake_daemon,
        env={
            "FAKE_TD_DAEMON": "token",
            "FAKE_TD_DAEMON_TOKEN": TOKEN,
            "FAKE_TD_DAEMON_LOG": str(log),
        },
    )
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["data"][0]["selector"] == INSTANCE["selector"]
    command, flag, remaining = log.read_text().splitlines()
    assert (command, flag) == ("start", "--timeout")
    assert 7 < float(remaining) <= 8


def test_failed_start_reports_its_reason(native_beside_fake_daemon, tmp_path):
    reason = "Daemon startup timed out; inspect C:\\logs\\daemon.log"
    result = run(
        "native",
        ["--json", "instances", "list"],
        INSTANCES,
        tmp_path / "home",
        exe=native_beside_fake_daemon,
        env={"FAKE_TD_DAEMON": "fail", "FAKE_TD_DAEMON_REASON": reason},
    )
    assert (result.exit_code, result.stderr) == (3, "daemon_unavailable\r\n")
    assert json.loads(result.stdout) == {
        "protocol_version": 3,
        "error": {
            "code": "daemon_unavailable",
            "message": "daemon_unavailable",
            "details": {"reason": reason},
            "retryable": False,
        },
    }


def test_a_start_that_never_finishes_is_stopped(native_beside_fake_daemon, tmp_path):
    started = time.perf_counter()
    result = run(
        "native",
        ["--json", "--timeout", "0.2", "instances", "list"],
        INSTANCES,
        tmp_path / "home",
        exe=native_beside_fake_daemon,
        env={"FAKE_TD_DAEMON": "hang"},
    )
    assert time.perf_counter() - started < 15
    assert result.exit_code == 3
    assert json.loads(result.stdout)["error"]["details"] == {
        "reason": "td-daemon start did not finish in time"
    }
