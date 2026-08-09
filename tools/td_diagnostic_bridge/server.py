"""Loopback-only HTTP adapter for the TouchDesigner diagnostic bridge."""

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class ServerHandle:
    server: ThreadingHTTPServer
    thread: threading.Thread

    @property
    def host(self) -> str:
        return str(self.server.server_address[0])

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def start_server(application, port: int = 9983) -> ServerHandle:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._handle()

        def do_POST(self) -> None:
            self._handle()

        def _handle(self) -> None:
            content_length = int(self.headers.get("Content-Length", "0"))
            data = self.rfile.read(content_length)
            result = application.handle_request(
                self.command,
                self.path.partition("?")[0],
                dict(self.headers.items()),
                data,
            )
            encoded = json.dumps(result.payload, separators=(",", ":")).encode()
            self.send_response(result.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever,
        name="td-diagnostic-bridge",
        daemon=True,
    )
    thread.start()
    return ServerHandle(server, thread)
