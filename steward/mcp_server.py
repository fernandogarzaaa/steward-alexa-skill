"""Steward MCP server: a self-hosted MCP server over Streamable HTTP.

This is the Alexa+ track deliverable: a working MCP server implementing a
current MCP spec version (the bundled SDK negotiates 2026-07-28, which is
newer than the 2025-11-25 minimum required by the hackathon rules) served
over Streamable HTTP. Every tool below is backed by the local SQLite store,
so the server runs with zero cloud dependencies.

Run:  python -m steward.mcp_server [--host 127.0.0.1] [--port 8899]

Access control: set STEWARD_MCP_API_KEY (or STEWARD_API_KEY) to require
``Authorization: Bearer <key>`` on /mcp; requests are also rate-limited per
client IP (STEWARD_MCP_RATE_LIMIT_PER_MIN, default 600). Binding to a
non-loopback host without a key is refused unless STEWARD_ALLOW_OPEN=1.
"""
from __future__ import annotations

import argparse
import datetime
import os

from mcp.server.mcpserver import MCPServer
from mcp.types import LATEST_PROTOCOL_VERSION

from .security import (ApiKeyASGIMiddleware, RateLimiter, _int_env,
                       check_bind, mcp_api_key)
from .store import Store

SERVER_NAME = "steward"
SERVER_VERSION = "1.0.0"

mcp = MCPServer(SERVER_NAME, version=SERVER_VERSION)
store = Store()


def _iso_now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


@mcp.tool()
def clock_now() -> str:
    """Current local date and time (ISO 8601). Use it to resolve relative
    dates like 'today', 'tomorrow', or 'this Saturday' before planning."""
    return _iso_now()


@mcp.tool()
def calendar_add(title: str, date: str, start_time: str = "",
                 notes: str = "") -> dict:
    """Add a calendar event. date is YYYY-MM-DD, start_time is HH:MM (24h).
    Returns the created event including its id."""
    return store.calendar_add(title=title, date=date,
                              start_time=start_time, notes=notes)


@mcp.tool()
def calendar_list(date: str = "") -> list:
    """List calendar events. Pass date as YYYY-MM-DD to filter to one day,
    or omit it to list everything upcoming in chronological order."""
    return store.calendar_list(date=date)


@mcp.tool()
def calendar_delete(event_id: int) -> bool:
    """Delete a calendar event by id. Returns true when something was deleted."""
    return store.calendar_delete(event_id=event_id)


@mcp.tool()
def reminder_add(text: str, due: str = "") -> dict:
    """Add a reminder. due is free text like '2026-10-10 18:00' or 'Saturday'.
    Returns the created reminder including its id."""
    return store.reminder_add(text=text, due=due)


@mcp.tool()
def reminder_list(include_done: bool = False) -> list:
    """List open reminders (set include_done to also see completed ones)."""
    return store.reminder_list(include_done=include_done)


@mcp.tool()
def reminder_done(reminder_id: int) -> bool:
    """Mark a reminder completed by id. Returns true when it was updated."""
    return store.reminder_done(reminder_id=reminder_id)


@mcp.tool()
def note_save(title: str, body: str, tags: str = "") -> dict:
    """Save a note. tags is a comma-separated string like 'groceries, weekly'.
    Returns the created note including its id."""
    return store.note_save(title=title, body=body, tags=tags)


@mcp.tool()
def note_search(query: str) -> list:
    """Full-text search over saved notes by title, body, or tags.
    Returns matching notes with a 280-character snippet."""
    return store.note_search(query=query)


@mcp.tool()
def note_get(note_id: int) -> dict:
    """Fetch one full note by id."""
    result = store.note_get(note_id=note_id)
    return result if result is not None else {}


@mcp.tool()
def prefs_set(key: str, value: str) -> dict:
    """Remember a user preference across sessions, e.g. key
    'appointment_time' value 'mornings'. This is Steward's long-term memory:
    read it back with prefs_get before making assumptions."""
    return store.prefs_set(key=key, value=value)


@mcp.tool()
def prefs_get(key: str) -> dict:
    """Read a remembered preference. Returns {"key": ..., "value": ""} when
    nothing was stored under that key yet."""
    return store.prefs_get(key=key)


@mcp.tool()
def prefs_all() -> list:
    """List every remembered preference."""
    return store.prefs_all()


def protocol_version() -> str:
    """The MCP protocol version this server negotiates."""
    return LATEST_PROTOCOL_VERSION


def build_http_app(host: str = "127.0.0.1", key: str | None = None):
    """The Streamable HTTP ASGI app at /mcp, wrapped with key auth + rate limit."""
    key = mcp_api_key() if key is None else key
    app = mcp.streamable_http_app(streamable_http_path="/mcp", host=host)
    limiter = RateLimiter(_int_env("STEWARD_MCP_RATE_LIMIT_PER_MIN", 600), 60.0)
    return ApiKeyASGIMiddleware(app, key=key, limiter=limiter)


def main() -> None:
    parser = argparse.ArgumentParser(description="Steward MCP server")
    parser.add_argument("--host", default=os.environ.get("STEWARD_MCP_HOST",
                                                         "127.0.0.1"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("STEWARD_MCP_PORT", "8899")))
    args = parser.parse_args()
    key = mcp_api_key()
    check_bind(args.host, key, "the Steward MCP server")
    print(f"Steward MCP server {SERVER_VERSION} on "
          f"http://{args.host}:{args.port}/mcp "
          f"(protocol {LATEST_PROTOCOL_VERSION}, "
          f"auth {'on' if key else 'off'})", flush=True)
    import uvicorn
    uvicorn.run(build_http_app(host=args.host, key=key), host=args.host,
                port=args.port, log_level="info")


if __name__ == "__main__":
    main()
