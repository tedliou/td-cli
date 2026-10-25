"""The native td keeps behaving exactly like the Python td it replaced (ADR 0003).

Each case runs the native td against a fake Daemon and compares exit code, stdout, stderr and
the requests the Daemon received with what the Python td did for the same case, recorded in
``tests/data/native_td_expected.json``. Build the native td first:
``cargo build --release --bins --examples`` in ``native/td``; ``TD_NATIVE_EXE`` overrides its path.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import NamedTuple

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "native" / "td" / "target" / "release"
NATIVE = Path(os.environ.get("TD_NATIVE_EXE", RELEASE / "td.exe"))
FAKE_TD_DAEMON = NATIVE.parent / "examples" / "fake_td_daemon.exe"
TOKEN = "a" * 64
REQUEST_ID = "01890a5d-ac96-774b-bcce-b302099a8057"
UUID7 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
pytestmark = pytest.mark.skipif(not NATIVE.exists(), reason=f"native td not built at {NATIVE}")

HEALTH = {
    "ready": True,
    "logging_healthy": True,
    "release_version": "1.0.0",
    "protocol_versions": [4],
    "schema_version": 2,
}
INSTANCE = {
    "instance_id": "7b0c3f5e-3c1d-4d55-9a8e-0c8b2f1d7a10",
    "selector": "專案 one",
    "status": "online",
    "protocol_version": 4,
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


def parsed(raw: bytes) -> object:
    """A request body as JSON, or as text when it is not JSON (a malformed plan, say)."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode(errors="replace")


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
                        parsed(raw),
                        self.client_address[1],
                    )
                )
                # "/v3/requests/*" answers for Request IDs the CLI generates itself.
                responses = (
                    daemon.routes.get((self.command, route))
                    or daemon.routes.get((self.command, route.rsplit("/", 1)[0] + "/*"))
                    or [(404, {"detail": "Not Found"})]
                )
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
        command = [str(exe), *argv]
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
        return portable(text.replace(daemon.url.removeprefix("http://"), "<daemon>"), home)

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


def portable(text: str, home: Path) -> str:
    """Name the test's temporary folder ``<home>``, as typed and as JSON escapes it."""
    forms = {str(home), str(home.resolve())}
    for form in sorted(forms | {json.dumps(form)[1:-1] for form in forms}, key=len, reverse=True):
        text = text.replace(form, "<home>")
    return text


# What the Python td did for each case, recorded before it was removed (ADR 0003). Rerun with
# TD_NATIVE_UPDATE_EXPECTED=1 to record the native td instead, after a deliberate change.
EXPECTED_PATH = ROOT / "tests" / "data" / "native_td_expected.json"
UPDATE_EXPECTED = os.environ.get("TD_NATIVE_UPDATE_EXPECTED") == "1"
_expected: dict[str, dict] = (
    json.loads(EXPECTED_PATH.read_text(encoding="utf-8")) if EXPECTED_PATH.exists() else {}
)
_case = {"id": "", "count": 0}


@pytest.fixture(autouse=True)
def _expected_case(request):
    _case.update(id=request.node.nodeid.split("::", 1)[1], count=0)


@pytest.fixture(scope="session", autouse=True)
def _write_expected():
    yield
    if UPDATE_EXPECTED:
        EXPECTED_PATH.parent.mkdir(exist_ok=True)
        text = json.dumps(dict(sorted(_expected.items())), indent=1, ensure_ascii=False)
        EXPECTED_PATH.write_text(text + chr(10), encoding="utf-8", newline=chr(10))


def recorded(produce) -> Run:
    """The recorded result for this test's next comparison; ``produce`` makes it when updating."""
    key = f"{_case['id']}#{_case['count']}"
    _case["count"] += 1
    if UPDATE_EXPECTED:
        result = produce()
        _expected[key] = {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "requests": result.requests,
            "connections": result.connections,
        }
        return result
    assert key in _expected, f"no recorded result for {key}; see EXPECTED_PATH"
    entry = _expected[key]
    requests = [tuple(request) for request in entry["requests"]]
    return Run(entry["exit_code"], entry["stdout"], entry["stderr"], requests, entry["connections"])


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
    expected = recorded(lambda: run(argv, routes, home, stdin=stdin))
    native = run(argv, routes, home, stdin=stdin)
    assert (native.exit_code, native.stdout, native.stderr) == (
        expected.exit_code,
        expected.stdout,
        expected.stderr,
    )
    if compare_requests:
        assert native.requests == expected.requests
        assert native.connections == expected.connections
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
            {("GET", "/v3/health"): [(200, {**HEALTH, "protocol_versions": [3]})]},
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
            [(200, snapshot(status="dispatched")), (200, snapshot(result={"ok": True}))],
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
        {**daemon(), ("GET", REQUEST): [(200, snapshot(status="dispatched"))]},
        home,
        compare_requests=False,
    )
    assert native.exit_code == 6
    assert json.loads(native.stdout)["error"]["details"]["request"]["status"] == "dispatched"


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
    environment = {
        **os.environ,
        "LOCALAPPDATA": str(home),
        "TDCLI_TEST_ENDPOINT": f"http://127.0.0.1:{port}",
    }

    def version(command: list[str]) -> Run:
        completed = subprocess.run(
            [*command, "--json", "version"],
            capture_output=True,
            env=environment,
            timeout=60,
            check=False,
        )
        return Run(
            completed.returncode, completed.stdout.decode(), completed.stderr.decode(), [], 0
        )

    expected = recorded(lambda: version([str(NATIVE)]))
    native = version([str(NATIVE)])
    assert (native.exit_code, native.stdout, native.stderr) == (
        expected.exit_code,
        expected.stdout,
        expected.stderr,
    )
    assert json.loads(native.stdout)["data"]["daemon_release_version"] is None


def test_a_refused_get_is_retried_with_backoff(home):
    # The timing has no recording, so it is checked directly: attempts at 0, 0.1 and 0.4 s,
    # the last one answered.
    started = time.perf_counter()
    native = run(["--json", "version"], daemon(), home, start_after=0.2)
    assert native.exit_code == 0, native.stderr
    assert json.loads(native.stdout)["data"]["daemon_release_version"] == "1.0.0"
    assert time.perf_counter() - started >= 0.4
    assert len(native.requests) == 1


def test_long_polls_ask_for_the_rest_of_the_command_deadline(home):
    argv = ["--json", "--timeout", "10", "requests", "wait", REQUEST_ID]
    routes = {
        **daemon(),
        ("GET", REQUEST): [(200, snapshot(status="dispatched")), (200, snapshot(result={}))],
    }
    expected = recorded(lambda: run(argv, routes, home))
    for kind, result in (("recorded", expected), ("native", run(argv, routes, home))):
        assert result.exit_code == 0, result.stderr
        polls = [path for method, path, _ in result.requests if path.startswith(REQUEST)]
        waits = [int(path.split("wait_ms=")[1]) for path in polls]
        assert len(waits) == 2 and all(8_500 < wait <= 9_900 for wait in waits), (kind, waits)
        assert waits[1] < waits[0]


# The Python td would have started a real Daemon here, so the start path has no recording.


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
        ["--json", "instances", "list"],
        INSTANCES,
        tmp_path / "home",
        exe=native_beside_fake_daemon,
        env={"FAKE_TD_DAEMON": "fail", "FAKE_TD_DAEMON_REASON": reason},
    )
    assert (result.exit_code, result.stderr) == (3, "daemon_unavailable\r\n")
    assert json.loads(result.stdout) == {
        "protocol_version": 4,
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


# ops commands: the minimal dedicated arguments, every refinement, and a partial group.
class Case(NamedTuple):
    """A submitting command's argv after its name: ``None`` leaves that case out."""

    minimal: list[str] | None
    full: list[str] | None
    partial: list[str] | None
    takes_input: bool = True


OPS = {
    "get": Case(["/p"], ["/p"], None),
    "inspect": Case(["/p"], ["/p", "--max-items", "7"], ["--max-items", "7"]),
    "state get": Case(["/p"], ["/p"], None),
    "state set": Case(
        ["/p"],
        [
            "/p",
            "--node-x",
            "1",
            "--color",
            "1",
            "0.5",
            "0",
            "--comment",
            "é",
            "--no-bypass",
            "--lock",
        ],
        ["--lock"],
    ),
    "children": Case(["/p"], ["/p", "--op-type", "baseCOMP"], ["--op-type", "baseCOMP"]),
    "connections": Case(["/p"], ["/p", "--max-connections", "3"], ["--max-connections", "3"]),
    "hierarchy connections": Case(
        ["/p"], ["/p", "--max-connections", "0"], ["--max-connections", "1"]
    ),
    "hierarchy connect": Case(
        ["/a", "/b"], ["/a", "/b", "--output-index", "1", "--replace"], ["/a"]
    ),
    "hierarchy disconnect": Case(
        ["/a", "/b"],
        ["/a", "/b", "--input-index", "2"],
        ["--input-index", "2"],
    ),
    "create": Case(
        ["/p", "baseCOMP", "n"],
        ["/p", "baseCOMP", "n", "--node-x", "5", "--allow-conditional"],
        ["/p", "baseCOMP"],
    ),
    "connect": Case(["/a", "/b"], ["/a", "/b", "--input-index", "0", "--replace"], ["--replace"]),
    "rename": Case(["/a", "b"], ["/a", "b"], ["/a"]),
    "destroy": Case(
        ["/a"],
        [
            "/a",
            "--recursive",
            "--allow-connected",
            "--max-operators",
            "9",
            "--inventory",
            "summary",
        ],
        ["--recursive"],
    ),
    "copy": Case(
        ["/a", "/b", "c"],
        ["/a", "/b", "c", "--include-docked", "--node-y", "3"],
        ["/a", "/b"],
    ),
    "move": Case(
        ["/a", "/b", "c"],
        ["/a", "/b", "c", "--allow-connected", "--max-operators", "0", "--inventory", "x"],
        ["--node-x", "1"],
    ),
    "tox import": Case(
        ["/p", "C:/x.tox", "C:/", "t"],
        [
            "/p",
            "C:/x.tox",
            "C:/",
            "t",
            "--trusted",
            "--replace",
            "--root-child",
            "r",
            "--max-file-bytes",
            "10",
            "--max-operators",
            "2",
            "--inventory",
            "summary",
        ],
        ["/p", "C:/x.tox"],
    ),
    "disconnect": Case(["/a", "/b"], ["/a", "/b", "--output-index", "3"], ["/a"]),
}
FIXED = ["--request-id", REQUEST_ID]


# The other submitting commands: (minimal, full, partial, takes --input); None when absent.
OTHERS = {
    "dat text get": Case(
        ["/d"], ["/d", "--max-bytes", "10", "--offset", "2"], ["--offset", "2"], True
    ),
    "dat text set": Case(["/d", "héllo\n"], ["/d", ""], ["/d"], True),
    "dat table get": Case(
        ["/d"],
        ["/d", "--row-offset", "1", "--column-count", "2", "--max-bytes", "9"],
        ["--row-count", "3"],
        True,
    ),
    "dat table replace": Case(["/d", '[["a","é"],[1]]'], ["/d", " [] "], ["/d"], True),
    "dat table patch": Case(
        ["/d", '[["x"]]'],
        ["/d", '[["x"]]', "--row-offset", "2", "--column-offset", "1"],
        ["--row-offset", "1"],
        True,
    ),
    "chop values get": Case(
        ["/c"],
        ["/c", "--channels", "tx,,ty", "--sample-offset", "0", "--sample-count", "5"],
        ["--channels", "tx"],
        True,
    ),
    "parameters get": Case(["/o", "tx"], ["/o", "tx"], ["/o"], True),
    "parameters list": Case(
        ["/o"],
        ["/o", "--names", "a,b", "--include-values", "--non-default-only", "--no-menu-options"],
        ["--include-values"],
        True,
    ),
    "parameters pulse": Case(["/o", "p"], ["/o", "p"], ["/o"], True),
    "parameters set": Case(
        ["/o", "p", "--integer", "3"],
        ["/o", "p", "--export-source-operator", "/c", "--export-channel", "tx"],
        ["/o", "p", "--integer", "1", "--number", "2"],
        True,
    ),
    "parameters page-create": Case(None, None, None, True),
    "parameters menu-set": Case(
        ["/o", "Menu", "--names-json", '["a"]', "--labels-json", '["A"]', "--preserve", "index"],
        ["/o", "Menu", "--names-json", "[]", "--labels-json", "[]", "--preserve", "name"],
        ["/o", "Menu", "--names-json", '["a"]'],
        True,
    ),
    "parameters sequence-get": Case(
        ["/o", "seq"],
        ["/o", "seq", "--max-blocks", "3", "--max-parameters", "4"],
        ["/o"],
        True,
    ),
    "parameters sequence-replace": Case(
        ["/o", "seq", "--blocks-json", '[{"a":1}]'],
        ["/o", "seq", "--blocks-json", "[]"],
        ["--blocks-json", "[{}]"],
        True,
    ),
    "project metadata": Case([], [], None, False),
    "project snapshot": Case(
        ["/"],
        ["/", "--max-depth", "2", "--paginate", "--cursor", "c", "--include-connections"],
        ["--paginate"],
        True,
    ),
    "project save": Case(["C:/p.toe"], ["C:/p.toe", "--expected-sha256", "ab"], None, True),
    "binary export": Case(
        ["/t", "--format", "png"],
        ["/t", "--format", "png", "--max-bytes", "5"],
        ["/t"],
        True,
    ),
    "batch execute": Case(None, None, None, True),
    "events read": Case([], ["--after", "3", "--limit", "7", "--no-include-errors"], None, False),
}


def submitting_cases(table: dict[str, Case], prefix: tuple[str, ...] = ()):
    for name, (minimal, full, partial, takes_input) in table.items():
        command = [*prefix, *name.split()]
        inline = ["--input", '{"operator_path":"/p","n":1.0}']
        stdin = b'{"source_path": "/a", "target_path": "/b"}'
        if minimal is not None:
            yield pytest.param([*command, *minimal, *FIXED], None, False, id=f"{name}-minimal")
            yield pytest.param(
                [*command, *full, "--no-wait", *FIXED], None, False, id=f"{name}-full"
            )
        if takes_input:
            yield pytest.param([*command, *inline, *FIXED], None, False, id=f"{name}-input")
            yield pytest.param(
                [*command, "--input-file", "-", *FIXED], stdin, False, id=f"{name}-stdin"
            )
        if partial is not None:
            yield pytest.param([*command, *partial, *FIXED], None, True, id=f"{name}-partial")


def ops_cases():
    yield from submitting_cases(OPS, ("ops",))


@pytest.mark.parametrize(("argv", "stdin", "rejected"), ops_cases())
def test_ops_commands(argv, stdin, rejected, home):
    check_submission(argv, stdin, rejected, home)


@pytest.mark.parametrize(("argv", "stdin", "rejected"), list(submitting_cases(OTHERS)))
def test_other_submitting_commands(argv, stdin, rejected, home):
    check_submission(argv, stdin, rejected, home)


def check_submission(argv, stdin, rejected, home):
    native = same(["--json", *argv], submission(), home, stdin=stdin)
    if rejected:
        assert (native.exit_code, native.requests) == (2, [])
    else:
        assert native.exit_code == 0, native.stderr
        assert [method for method, _, _ in native.requests][:2] == ["GET", "POST"]


@pytest.mark.parametrize(
    "argv",
    [
        ["parameters", "set", "/o", "p", "--operators-json", "[1]"],
        ["parameters", "set", "/o", "p", "--operators-json", "["],
        ["parameters", "set", "/o", "p", "--bool", "maybe"],
        ["parameters", "set", "/o", "p", "--export-channel", "tx"],
        ["parameters", "set", "/o", "--integer", "1"],
        ["parameters", "menu-set", "/o", "M", "--names-json", '"a"', "--labels-json", "[]"],
        ["parameters", "sequence-replace", "/o", "s", "--blocks-json", "[1]"],
        ["dat", "table", "replace", "/d", '{"a":1}'],
        ["dat", "table", "patch", "/d", "nope"],
        ["dat", "table", "patch", "--row-offset", "1"],
        ["parameters", "set", "/o", "p", "--bind-source-operator", "/b"],
        ["parameters", "menu-set", "/o", "M", "--names-json", "[]", "--labels-json", '{"a":1}'],
        ["parameters", "menu-set", "/o", "M", "--names-json", '["a"]', "--labels-json", '["A"]'],
        ["parameters", "menu-set", "--names-json", "["],
    ],
)
def test_malformed_json_and_value_options_fail_before_sending(argv, home):
    native = same(["--json", *argv, *FIXED], submission(), home)
    assert (native.exit_code, native.requests) == (2, [])


@pytest.mark.parametrize(
    "argv",
    [
        ["parameters", "set", "/o", "p", "--bool", "false"],
        ["parameters", "set", "/o", "p", "--number", "1e-7"],
        ["parameters", "set", "/o", "p", "--operators-json", ' ["/a", "/b"] '],
        ["parameters", "set", "/o", "p", "--expression", "me.time.frame"],
        ["parameters", "set", "/o", "p", "--bind-source-operator", "/b", "--bind-parameter", "x"],
        ["events", "read", "--include-errors"],
        ["parameters", "set", "/o", "p", "--bool", "true"],
        ["parameters", "set", "/o", "p", "--string", ""],
        ["parameters", "set", "/o", "p", "--operator", "/x"],
        ["project", "snapshot", "/", "--max-operators", "5"],
    ],
)
def test_value_modes_and_paired_flags(argv, home):
    native = same(["--json", *argv, *FIXED], submission(), home)
    assert native.exit_code == 0, native.stderr


@pytest.mark.parametrize(
    "argv",
    [
        ["project", "save", "--expected-sha256", "AB"],
        ["binary", "export", "--max-bytes", "5"],
        ["binary", "export", "/t", "--max-bytes", "5"],
    ],
)
def test_lone_refinements_match(argv, home):
    same(["--json", *argv, *FIXED], submission(), home)


def test_dedicated_options_and_input_are_exclusive(home):
    argv = ["--json", "ops", "rename", "/a", "b", "--input", "{}", *FIXED]
    native = same(argv, submission(), home)
    assert (native.exit_code, native.requests) == (2, [])


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "commands", "schema"],
        ["commands", "schema"],
        ["--json", "commands", "schema", "parameters.set"],
        ["commands", "schema", "ops.get"],
        ["--json", "commands", "schema", "ops.nope"],
        ["--json", "--instance", "x", "commands", "schema"],
        ["--json", "ops", "types"],
        ["ops", "types", "--family", "CHOP", "--limit", "3"],
        ["--json", "ops", "types", "noiseTOP"],
        ["--json", "ops", "types", "nopeTOP"],
        ["--json", "ops", "types", "noiseTOP", "--family", "CHOP"],
        ["--json", "ops", "types", "--family", "XYZ"],
        ["--json", "ops", "types", "--limit", "680"],
        ["--json", "ops", "types", "--limit", "0"],
        ["--json", "--instance", "x", "ops", "types"],
    ],
)
def test_offline_queries(argv, home):
    native = same(argv, daemon(), home)
    assert native.requests == []


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "my project"
    folder.mkdir()
    (folder / "scene.TOE").write_bytes(b"")
    (folder / "scene.txt").write_bytes(b"")
    shutil.copy(FAKE_TD_DAEMON, folder / "touchdesigner.exe")
    shutil.copy(FAKE_TD_DAEMON, folder / "td.exe")
    return folder


@pytest.mark.parametrize(
    ("toe", "exe", "extra"),
    [
        ("scene.txt", "touchdesigner.exe", []),
        ("scene.TOE", "td.exe", []),
        ("scene.TOE", "touchdesigner.exe", ["--instance", "x"]),
        ("missing.toe", "touchdesigner.exe", []),
        ("scene.TOE", ".", []),
    ],
)
def test_project_open_rejects_before_launching(toe, exe, extra, project, home):
    argv = ["--json", *extra, "project", "open", str(project / toe)]
    native = same([*argv, "--executable", str(project / exe)], daemon(), home)
    assert native.exit_code == 2


@pytest.mark.parametrize("folder", ["my project", "專案 資料夾"])
def test_project_open_launches_detached(folder, tmp_path, home):
    project = tmp_path / folder
    project.mkdir()
    (project / "scene.TOE").write_bytes(b"")
    shutil.copy(FAKE_TD_DAEMON, project / "TouchDesigner.exe")
    argv = ["--json", "project", "open", str(project / "scene.TOE")]
    argv += ["--executable", str(project / "TouchDesigner.exe")]
    results = [recorded(lambda: run(argv, daemon(), home)), run(argv, daemon(), home)]
    data = [json.loads(result.stdout)["data"] for result in results]
    assert [result.exit_code for result in results] == [0, 0]
    assert data[0]["pid"] > 0 and data[1]["pid"] > 0
    assert {**data[1], "pid": 0} == {**data[0], "pid": 0}
    assert data[1]["path"] == portable(str((project / "scene.TOE").resolve()), home)


def test_project_open_reports_a_rejected_launch(project, home):
    (project / "touchdesigner.exe").write_bytes(b"not a program")
    argv = ["--json", "project", "open", str(project / "scene.TOE")]
    # The Win32_Process.Create return code for a non-program differs between Windows builds
    # (9 locally, 8 on the CI runner), so only the shape of the reason is fixed.
    native = run([*argv, "--executable", str(project / "touchdesigner.exe")], daemon(), home)
    assert native.exit_code == 5
    error = json.loads(native.stdout)["error"]
    assert error["code"] == "process_launch_failed"
    assert re.fullmatch(
        r"Windows process service rejected launch [(]Win32_Process [0-9]+[)]",
        error["details"]["reason"],
    )
    assert native.stderr == "process_launch_failed" + chr(13) + chr(10)


def test_project_open_reports_a_launch_that_outlives_the_timeout(project, home):
    argv = ["--json", "--timeout", "0.1", "project", "open", str(project / "scene.TOE")]
    native = same([*argv, "--executable", str(project / "touchdesigner.exe")], daemon(), home)
    assert native.exit_code == 5


PLAN = [
    {"name": "ops.get", "input": {"operator_path": "/a"}},
    {"name": "ops.rename", "input": {"operator_path": "/a", "new_name": "é"}},
]
FAILED = snapshot(
    status="failed",
    error={"code": "operator_not_found", "message": "missing", "details": {"path": "/a"}},
)


def plan_routes(*, validate=None, resolve=None, posts=None, waits=None) -> Route:
    return {
        **daemon(),
        ("POST", "/v3/command-plans/validate"): [validate or (200, {"commands": PLAN})],
        ("GET", "/v3/instances/resolve"): [resolve or (200, INSTANCE)],
        ("POST", "/v3/requests"): posts or [ADMITTED],
        ("GET", "/v3/requests/*"): waits or [(200, snapshot(result={"ok": True}))],
    }


MIB = 1024 * 1024


@pytest.mark.parametrize(
    ("globals_", "routes", "document"),
    [
        pytest.param(["--json"], plan_routes(), None, id="succeeds"),
        pytest.param(["--json", "--instance", "專案"], plan_routes(), None, id="selector"),
        pytest.param([], plan_routes(), None, id="human"),
        pytest.param(
            ["--json"], plan_routes(waits=[(200, snapshot()), (200, FAILED)]), None, id="fails"
        ),
        pytest.param(
            ["--json"],
            plan_routes(waits=[(200, snapshot(status="cancelled"))]),
            None,
            id="cancelled",
        ),
        pytest.param(
            ["--json"],
            plan_routes(validate=(422, {"detail": "invalid_arguments"})),
            b"nope",
            id="rejected-plan",
        ),
        pytest.param(
            ["--json"],
            plan_routes(resolve=(404, {"detail": "instance_not_found"})),
            None,
            id="no-instance",
        ),
        pytest.param(["--json"], plan_routes(posts=[DROP]), None, id="dropped-submit"),
        pytest.param(
            ["--json"],
            plan_routes(posts=[ADMITTED, (409, {"detail": "request_conflict"})]),
            None,
            id="second-rejected",
        ),
        pytest.param(["--json"], plan_routes(), b" " * MIB, id="at-limit"),
        pytest.param(["--json"], plan_routes(), b" " * (MIB + 1), id="over-limit"),
        pytest.param(["--json"], plan_routes(), "﻿{}".encode(), id="bom"),
    ],
)
def test_commands_execute(globals_, routes, document, home):
    plan = home / "plan.json"
    plan.write_bytes(json.dumps({"commands": PLAN}).encode() if document is None else document)
    same([*globals_, "commands", "execute", "--input-file", str(plan)], routes, home)


def test_commands_execute_without_a_plan_file(home):
    argv = ["--json", "commands", "execute", "--input-file", str(home / "missing.json")]
    native = same(argv, plan_routes(), home)
    assert (native.exit_code, native.requests) == (2, [])


def test_commands_execute_stops_at_the_deadline(home):
    plan = home / "plan.json"
    plan.write_text(json.dumps({"commands": PLAN}), encoding="utf-8")
    argv = ["--json", "--timeout", "0.4", "commands", "execute", "--input-file", str(plan)]
    routes = plan_routes(waits=[(200, snapshot(status="dispatched"))])
    native = same(argv, routes, home, compare_requests=False)
    events = [json.loads(line) for line in native.stdout.splitlines()]
    assert [event["event"] for event in events[:2]] == ["submitting", "stopped"]
    assert events[1]["error"]["code"] == "wait_timeout"
    assert native.exit_code == 6


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["dat", "text", "get"], ["32768", "td commands schema dat.text.get"]),
        (["project", "snapshot"], ["1000", "1-1000"]),
        (["batch", "execute"], ["16", "commands execute"]),
        (["ops", "destroy"], ["non-empty COMP", "detached_connections"]),
    ],
)
def test_help_states_option_meaning_and_limits(argv, expected):
    completed = subprocess.run(
        [str(NATIVE), *argv, "--help"], capture_output=True, timeout=60, check=False
    )
    assert completed.returncode == 0, completed.stderr
    text = " ".join(completed.stdout.decode().split())
    for fragment in expected:
        assert fragment in text


def frame_batch_report(count, stopped_reason="completed", failed=None):
    results = [{"ok": True, "result": {"index": index}} for index in range(count)]
    if failed is not None:
        results.append({"ok": False, "error": failed})
    return snapshot(
        result={
            "results": results,
            "executed_count": len(results),
            "stopped_reason": "failed" if failed else stopped_reason,
        }
    )


def get(path):
    return {"name": "ops.get", "input": {"operator_path": path}}


SAVE = {"name": "project.save", "input": {}}


def run_frame_batch(plan, waits, home, *globals_):
    path = home / "plan.json"
    path.write_text(json.dumps({"commands": plan}), encoding="utf-8")
    argv = [*globals_, "--json", "commands", "execute", "--frame-batch", "--input-file", str(path)]
    native = run(argv, plan_routes(validate=(200, {"commands": plan}), waits=waits), home)
    submitted = [body["command"] for _, route, body in native.requests if route == "/v3/requests"]
    lines = [json.loads(line) for line in native.stdout.splitlines()]
    return native, submitted, [line for line in lines if "event" in line]


def frame_batch_request(*commands):
    return {"name": "plan.execute", "input": {"commands": list(commands)}}


def test_frame_batch_groups_batchable_commands_and_resumes_after_the_frame_budget(home):
    plan = [get("/a"), get("/b"), SAVE, get("/c"), get("/d")]
    waits = [
        (200, frame_batch_report(1, "frame_budget")),
        (200, frame_batch_report(1)),
        (200, snapshot(result={"saved": True})),
        (200, frame_batch_report(2)),
    ]

    native, submitted, events = run_frame_batch(plan, waits, home)

    assert native.exit_code == 0, native.stderr
    assert submitted == [
        frame_batch_request(*plan[:2]),
        frame_batch_request(plan[1]),
        SAVE,
        frame_batch_request(*plan[3:]),
    ]
    assert [(event["event"], event.get("index"), event.get("count")) for event in events] == [
        ("submitting", 0, 2),
        ("completed", 0, None),
        ("submitting", 1, 1),
        ("completed", 1, None),
        ("submitting", 2, None),
        ("completed", 2, None),
        ("submitting", 3, 2),
        ("completed", 3, None),
        ("completed", 4, None),
        ("finished", None, None),
    ]
    assert events[1] == {
        "event": "completed",
        "index": 0,
        "request_id": "<generated>",
        "result": {"index": 0},
    }
    assert events[-1]["succeeded"] == 5


def test_frame_batch_splits_at_the_command_count_and_escaped_byte_limits(home):
    wide = [{"name": "ops.rename", "input": {"operator_path": "/a", "new_name": "é" * 6000}}] * 2
    plan = [get("/a")] * 257 + wide
    waits = [
        (200, frame_batch_report(256)),
        (200, frame_batch_report(2)),
        (200, frame_batch_report(1)),
    ]

    _, submitted, _ = run_frame_batch(plan, waits, home)

    # 6,000 é are 12,000 UTF-8 bytes but 36,000 escaped ones, so the two cannot share a batch.
    assert [len(command["input"]["commands"]) for command in submitted] == [256, 2, 1]
    assert submitted[1:] == [frame_batch_request(get("/a"), wide[0]), frame_batch_request(wide[1])]


def test_frame_batch_stops_at_its_failed_command(home):
    plan = [get("/a"), get("/b"), get("/c"), get("/d")]
    error = {"code": "operator_not_found", "message": "missing", "details": {"path": "/b"}}
    waits = [(200, frame_batch_report(1, failed=error))]

    native, submitted, events = run_frame_batch(plan, waits, home)

    assert native.exit_code != 0
    assert len(submitted) == 1
    assert events[-1] == {
        "event": "stopped",
        "index": 1,
        "request_id": "<generated>",
        "error": {
            "code": "operator_not_found",
            "details": {"path": "/b", "request_id": "<generated>"},
        },
        "succeeded": 1,
        "unsubmitted": 2,
    }


def test_frame_batch_points_at_its_first_command_when_it_is_unknown(home):
    plan = [SAVE, get("/a"), get("/b"), SAVE]
    unknown = snapshot(status="unknown", error={"code": "request_outcome_unknown", "message": ""})
    waits = [(200, snapshot(result={"saved": True})), (200, unknown)]

    native, submitted, events = run_frame_batch(plan, waits, home)

    assert native.exit_code != 0
    assert submitted == [SAVE, frame_batch_request(*plan[1:3])]
    stopped = events[-1]
    assert stopped["event"] == "stopped"
    assert (stopped["index"], stopped["succeeded"], stopped["unsubmitted"]) == (1, 1, 1)
    assert stopped["error"]["code"] == "request_outcome_unknown"


def test_frame_batch_stops_at_the_deadline(home):
    waits = [(200, snapshot(status="dispatched"))]

    native, _, events = run_frame_batch([get("/a"), get("/b")], waits, home, "--timeout", "0.4")

    assert native.exit_code == 6
    assert [event["event"] for event in events] == ["submitting", "stopped"]
    assert events[1]["error"]["code"] == "wait_timeout"
    assert events[1]["unsubmitted"] == 0
