"""Access control shared by the web app and the MCP server.

* API key: set ``STEWARD_API_KEY`` (web app) and ``STEWARD_MCP_API_KEY``
  (MCP server; falls back to ``STEWARD_API_KEY``). Clients send it as
  ``Authorization: Bearer <key>`` or ``X-Steward-Key: <key>``.
* Rate limit: a small in-memory sliding-window limiter per client IP.
* Safe binds: a server bound to a non-loopback address refuses to start
  without a key unless ``STEWARD_ALLOW_OPEN=1`` is set explicitly.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import os
import threading
import time
from collections import deque
from typing import Callable, Iterable, Mapping, Optional

LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}


def web_api_key() -> str:
    return os.environ.get("STEWARD_API_KEY", "").strip()


def mcp_api_key() -> str:
    return (os.environ.get("STEWARD_MCP_API_KEY", "").strip()
            or os.environ.get("STEWARD_API_KEY", "").strip())


def allow_open() -> bool:
    return os.environ.get("STEWARD_ALLOW_OPEN", "").strip().lower() in (
        "1", "true", "yes")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def extract_key(headers: Mapping[str, str]) -> str:
    """Key from ``Authorization: Bearer`` or ``X-Steward-Key`` (case-insensitive)."""
    lowered = {k.lower(): v for k, v in headers.items()}
    auth = lowered.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return lowered.get("x-steward-key", "").strip()


def key_matches(provided: str, expected: str) -> bool:
    """Constant-time comparison; an empty expected key never matches."""
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"),
                               expected.encode("utf-8"))


def is_loopback(host: str) -> bool:
    host = (host or "").strip().strip("[]")
    if host.lower() in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_bind(host: str, key: str, service: str) -> None:
    """Refuse to expose an unauthenticated service beyond loopback."""
    if is_loopback(host) or key or allow_open():
        return
    raise SystemExit(
        f"Refusing to start {service} on {host!r} without an API key. "
        "Set STEWARD_API_KEY (and STEWARD_MCP_API_KEY for the MCP server) or, "
        "for a deliberately open demo, STEWARD_ALLOW_OPEN=1. "
        "Use --host 127.0.0.1 for local-only use.")


class RateLimiter:
    """Sliding-window limiter: at most ``limit`` hits per ``window`` seconds per key."""

    def __init__(self, limit: int, window: float = 60.0,
                 clock: Callable[[], float] = time.monotonic,
                 max_keys: int = 10_000) -> None:
        self.limit = limit
        self.window = window
        self._clock = clock
        self._max_keys = max_keys
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        now = self._clock()
        with self._lock:
            if len(self._hits) >= self._max_keys and key not in self._hits:
                self._evict(now)
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def _evict(self, now: float) -> None:
        for k in [k for k, q in self._hits.items()
                  if not q or now - q[-1] >= self.window]:
            del self._hits[k]
        if len(self._hits) >= self._max_keys:  # still full: drop oldest
            oldest = min(self._hits, key=lambda k: self._hits[k][-1])
            del self._hits[oldest]


def _client_ip(scope: dict) -> str:
    client = scope.get("client")
    return client[0] if client else "unknown"


class ApiKeyASGIMiddleware:
    """Bearer-key check (+ optional per-IP rate limit) around any ASGI app.

    Used for the MCP Streamable HTTP endpoint. Non-HTTP scopes (lifespan)
    pass straight through; ``exempt_paths`` (e.g. a health check) skip auth.
    """

    def __init__(self, app, key: str, limiter: Optional[RateLimiter] = None,
                 exempt_paths: Iterable[str] = ()) -> None:
        self.app = app
        self.key = key
        self.limiter = limiter
        self.exempt = set(exempt_paths)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") in self.exempt:
            return await self.app(scope, receive, send)
        if self.limiter is not None and not self.limiter.allow(_client_ip(scope)):
            return await _json_response(send, 429, {"detail": "rate limit exceeded"},
                                        {"retry-after": str(int(self.limiter.window))})
        if self.key:
            headers = {k.decode("latin-1"): v.decode("latin-1")
                       for k, v in scope.get("headers", [])}
            if not key_matches(extract_key(headers), self.key):
                return await _json_response(send, 401, {"detail": "missing or invalid API key"},
                                            {"www-authenticate": "Bearer"})
        return await self.app(scope, receive, send)


async def _json_response(send, status: int, body: dict,
                         extra_headers: Optional[dict] = None) -> None:
    payload = json.dumps(body).encode("utf-8")
    headers = [(b"content-type", b"application/json"),
               (b"content-length", str(len(payload)).encode("ascii"))]
    for k, v in (extra_headers or {}).items():
        headers.append((k.encode("latin-1"), v.encode("latin-1")))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})
