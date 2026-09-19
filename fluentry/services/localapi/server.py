"""The loopback HTTP server.

The socket is bound to 127.0.0.1 rather than filtering connections after
accepting them, so the loopback-only guarantee is enforced by the kernel
rather than by this code.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .models import (
    DEFAULT_PORT,
    LOOPBACK_HOST,
    MAX_REQUEST_BYTES,
    Configuration,
    Request,
    Response,
    error,
)
from .router import LocalAPIRouter

__all__ = [
    "DEFAULT_PORT",
    "LOOPBACK_HOST",
    "MAX_REQUEST_BYTES",
    "Configuration",
    "LocalAPIRouter",
    "LocalAPIServer",
    "Request",
    "Response",
]


class _Handler(BaseHTTPRequestHandler):
    router: LocalAPIRouter
    protocol_version = "HTTP/1.1"
    server_version = "Fluentry"
    sys_version = ""

    def log_message(self, format, *args) -> None:  # noqa: A002 - base signature
        return  # The app has its own log; stderr noise helps nobody.

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._respond(error("Invalid Content-Length.", status=400))
            return
        if length > MAX_REQUEST_BYTES:
            self._respond(error("Request too large.", status=413))
            return

        request = Request(
            method=method,
            path=parsed.path,
            query={key: values[0] for key, values in parse_qs(parsed.query).items()},
            headers={key.lower(): value for key, value in self.headers.items()},
            body=self.rfile.read(length) if length > 0 else b"",
        )
        try:
            response = self.router.route(request)
        except Exception as problem:  # A crash here must not kill the server.
            response = error(str(problem), status=500)
        self._respond(response)

    def _respond(self, response: Response) -> None:
        self.send_response(response.status)
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(response.body)))
        self.end_headers()
        if response.body:
            self.wfile.write(response.body)

    def do_GET(self) -> None:  # noqa: N802 - base signature
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802 - base signature
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802 - base signature
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802 - base signature
        self._dispatch("DELETE")


class LocalAPIServer:
    def __init__(self, router: LocalAPIRouter, port: int = DEFAULT_PORT) -> None:
        self.router = router
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @staticmethod
    def from_app(app, version: str = "unknown") -> "LocalAPIServer":
        configuration = Configuration.current(app.settings.defaults)
        return LocalAPIServer(LocalAPIRouter(app, version=version), port=configuration.port)

    @property
    def is_running(self) -> bool:
        return self._server is not None

    @property
    def bound_port(self) -> int | None:
        return self._server.server_address[1] if self._server is not None else None

    def start(self) -> None:
        if self._server is not None:
            return
        handler = type("BoundHandler", (_Handler,), {"router": self.router})
        # Loopback only: the API is never reachable from the network.
        self._server = ThreadingHTTPServer((LOOPBACK_HOST, self.port), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="fluentry.local-api", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server = self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)
