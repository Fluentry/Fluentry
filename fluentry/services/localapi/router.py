"""Method-and-path dispatch.

A port of `LocalAPIRouter`. The one detail worth keeping: a known path with
the wrong verb answers 405, not 404, so a caller can tell a typo in the path
from a typo in the method.
"""

from __future__ import annotations

from typing import Protocol

from .controllers import (
    DictionaryAPIController,
    HealthController,
    HistoryAPIController,
    InferenceAPIController,
)
from .models import Request, Response, error


class RouteHandler(Protocol):
    def handle(self, request: Request) -> Response: ...


class LocalAPIRouter:
    def __init__(self, app=None, version: str = "unknown") -> None:
        self._routes: dict[tuple[str, str], RouteHandler] = {}
        if app is None:
            return

        self.register("GET", "/v1/health", HealthController(version))

        history = HistoryAPIController(app)
        self.register("GET", "/v1/history", history)

        dictionary = DictionaryAPIController(app)
        self.register("GET", "/v1/dictionary/replacements", dictionary)
        self.register("POST", "/v1/dictionary/replacements", dictionary)
        self.register("GET", "/v1/dictionary/custom-words", dictionary)
        self.register("POST", "/v1/dictionary/custom-words", dictionary)

        inference = InferenceAPIController(app)
        self.register("POST", "/v1/transcribe", inference)
        self.register("POST", "/v1/postprocess", inference)

    def register(self, method: str, path: str, handler: RouteHandler) -> None:
        self._routes[(method.upper(), path)] = handler

    def route(self, request: Request) -> Response:
        handler = self._routes.get((request.method.upper(), request.path))
        if handler is None:
            if any(path == request.path for _method, path in self._routes):
                return error("Method not allowed.", status=405)
            return error("Route not found.", status=404)
        return handler.handle(request)
