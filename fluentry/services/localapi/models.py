"""Request, response and configuration types for the local API.

A port of `LocalAPIModels.swift`. Two properties carry over and both matter:
the API binds to loopback only, so it is never reachable from the network,
and it is **off by default** — a dictation app that opened an HTTP port
without being asked would be a surprise.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

DEFAULT_PORT = 47_733
MAX_REQUEST_BYTES = 500 * 1024 * 1024
LOOPBACK_HOST = "127.0.0.1"

ENABLED_KEY = "LocalAPIEnabled"
PORT_KEY = "LocalAPIPort"

JSON_CONTENT_TYPE = "application/json; charset=utf-8"


@dataclass(frozen=True)
class Configuration:
    enabled: bool
    port: int

    @staticmethod
    def current(defaults) -> "Configuration":
        enabled = defaults.bool(ENABLED_KEY) if defaults.object(ENABLED_KEY) is not None else False
        raw_port = defaults.integer(PORT_KEY)
        port = raw_port if 0 < raw_port <= 65_535 else DEFAULT_PORT
        return Configuration(enabled=enabled, port=port)


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    query: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    @property
    def is_json(self) -> bool:
        return "application/json" in (self.headers.get("content-type") or "").lower()

    def decode_json(self) -> Any:
        """The parsed body, or None when it is absent or not JSON."""
        if not self.body:
            return None
        try:
            return json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


def _encode(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()  # ISO 8601, as the macOS encoder produced.
    raise TypeError(f"Cannot encode {type(value)!r}")


def json_response(value: Any, status: int = 200) -> Response:
    try:
        body = json.dumps(value, sort_keys=True, default=_encode).encode("utf-8")
    except TypeError:
        return error("Failed to encode response.", status=500)
    return Response(status=status, headers={"Content-Type": JSON_CONTENT_TYPE}, body=body)


def empty(status: int = 204) -> Response:
    return Response(status=status)


def error(message: str, status: int) -> Response:
    body = json.dumps({"error": message}).encode("utf-8")
    return Response(status=status, headers={"Content-Type": JSON_CONTENT_TYPE}, body=body)


def bounded_limit(request: Request, default: int = 100, maximum: int = 1000) -> int:
    raw = request.query.get("limit")
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, min(value, maximum))
