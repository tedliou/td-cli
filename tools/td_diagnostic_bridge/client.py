"""Command-line client for the session-only TouchDesigner diagnostic bridge."""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_CONFIG_PATH = (
    Path(os.environ["LOCALAPPDATA"]) / "touchdesigner-cli" / "diagnostic-bridge.json"
)


class BridgeClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    @classmethod
    def from_config(cls, config_path: Path = DEFAULT_CONFIG_PATH):
        config = json.loads(config_path.read_text(encoding="utf-8"))
        return cls(config["base_url"], config["token"])

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode()
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=5) as response:
                return json.loads(response.read())
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"bridge returned HTTP {error.code}: {body}") from error
        except URLError as error:
            raise RuntimeError(f"diagnostic bridge is unavailable: {error.reason}") from error

    def health(self) -> dict:
        return self.request("GET", "/health")

    def execute(self, code: str, mode: str = "exec", timeout: float = 15.0) -> dict:
        submitted = self.request("POST", "/jobs", {"code": code, "mode": mode})
        job_id = submitted["job_id"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.request("GET", f"/jobs/{job_id}")
            if job["status"] in {"succeeded", "failed"}:
                return job
            time.sleep(0.05)
        raise TimeoutError(f"diagnostic job {job_id} did not finish within {timeout}s")

    def shutdown(self) -> dict:
        return self.request("POST", "/shutdown")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("health")
    subparsers.add_parser("shutdown")
    execute_parser = subparsers.add_parser("exec")
    execute_parser.add_argument("--code")
    execute_parser.add_argument("--file", type=Path)
    execute_parser.add_argument("--mode", choices=("exec", "eval"), default="exec")
    execute_parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)

    client = BridgeClient.from_config(args.config)
    if args.command == "health":
        result = client.health()
    elif args.command == "shutdown":
        result = client.shutdown()
    else:
        if bool(args.code) == bool(args.file):
            parser.error("exec requires exactly one of --code or --file")
        code = args.code if args.code is not None else args.file.read_text(encoding="utf-8")
        result = client.execute(code, args.mode, args.timeout)
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0 if result.get("status") != "failed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, TimeoutError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
