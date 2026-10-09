"""Steward MCP server: a self-hosted MCP server over Streamable HTTP.

This is the Alexa+ track deliverable: a working MCP server implementing a
current MCP spec version (the bundled SDK negotiates 2026-07-28, which is
newer than the 2025-11-25 minimum required by the hackathon rules) served
over Streamable HTTP. Every tool below is backed by the local SQLite store,
so the server runs with zero cloud dependencies.

Run:  python -m steward.mcp_server [--host 127.0.0.1] [--port 8899]
"""
from __future__ import annotations

import argparse
import datetime
import os
import threading

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import LATEST_PROTOCOL_VERSION
from starlette.requests import Request
from starlette.responses import JSONResponse

from .store import Store, StorePool

SERVER_NAME = "steward"
SERVER_VERSION = "1.0.0"

# Optional per-visitor isolation for the hosted demo: a client that sends
# this header gets its own store; clients that do not (local use, any
# plain MCP client) share the default store exactly as before.
NAMESPACE_HEADER = "x-steward-session"

mcp = MCPServer(SERVER_NAME, version=SERVER_VERSION)
pool = StorePool()
store: Store = pool.get()  # the default (un-namespaced) store


def _store(ctx: Context | None) -> Store:
    """The store for this request: namespaced by header when present."""
    ns = ""
    if ctx is not None:
        try:
            headers = ctx.headers or {}
            ns = headers.get(NAMESPACE_HEADER, "") or ""
        except Exception:  # no request context (direct call): default
            ns = ""
    return pool.get(ns)


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "server": SERVER_NAME,
                         "version": SERVER_VERSION})


def _iso_now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


@mcp.tool()
def clock_now() -> str:
    """Current local date and time (ISO 8601). Use it to resolve relative
    dates like 'today', 'tomorrow', or 'this Saturday' before planning."""
    return _iso_now()


@mcp.tool()
def calendar_add(title: str, date: str, start_time: str = "",
                 notes: str = "",
                 ctx: Context | None = None) -> dict:
    """Add a calendar event. date is YYYY-MM-DD, start_time is HH:MM (24h).
    Returns the created event including its id."""
    return _store(ctx).calendar_add(title=title, date=date,
                                    start_time=start_time, notes=notes)


@mcp.tool()
def calendar_list(date: str = "",
                  ctx: Context | None = None) -> list:
    """List calendar events. Pass date as YYYY-MM-DD to filter to one day,
    or omit it to list everything upcoming in chronological order."""
    return _store(ctx).calendar_list(date=date)


@mcp.tool()
def calendar_delete(event_id: int,
                    ctx: Context | None = None) -> bool:
    """Delete a calendar event by id. Returns true when something was deleted."""
    return _store(ctx).calendar_delete(event_id=event_id)


@mcp.tool()
def reminder_add(text: str, due: str = "",
                 ctx: Context | None = None) -> dict:
    """Add a reminder. due is free text like '2026-10-10 18:00' or 'Saturday'.
    Returns the created reminder including its id."""
    return _store(ctx).reminder_add(text=text, due=due)


@mcp.tool()
def reminder_list(include_done: bool = False,
                  ctx: Context | None = None) -> list:
    """List open reminders (set include_done to also see completed ones)."""
    return _store(ctx).reminder_list(include_done=include_done)


@mcp.tool()
def reminder_done(reminder_id: int,
                  ctx: Context | None = None) -> bool:
    """Mark a reminder completed by id. Returns true when it was updated."""
    return _store(ctx).reminder_done(reminder_id=reminder_id)


@mcp.tool()
def note_save(title: str, body: str, tags: str = "",
              ctx: Context | None = None) -> dict:
    """Save a note. tags is a comma-separated string like 'groceries, weekly'.
    Returns the created note including its id."""
    return _store(ctx).note_save(title=title, body=body, tags=tags)


@mcp.tool()
def note_search(query: str,
                ctx: Context | None = None) -> list:
    """Full-text search over saved notes by title, body, or tags.
    Returns matching notes with a 280-character snippet."""
    return _store(ctx).note_search(query=query)


@mcp.tool()
def note_get(note_id: int,
             ctx: Context | None = None) -> dict:
    """Fetch one full note by id."""
    result = _store(ctx).note_get(note_id=note_id)
    return result if result is not None else {}


@mcp.tool()
def prefs_set(key: str, value: str,
              ctx: Context | None = None) -> dict:
    """Remember a user preference across sessions, e.g. key
    'appointment_time' value 'mornings'. This is Steward's long-term memory:
    read it back with prefs_get before making assumptions."""
    return _store(ctx).prefs_set(key=key, value=value)


@mcp.tool()
def prefs_get(key: str,
              ctx: Context | None = None) -> dict:
    """Read a remembered preference. Returns {"key": ..., "value": ""} when
    nothing was stored under that key yet."""
    return _store(ctx).prefs_get(key=key)


@mcp.tool()
def prefs_all(ctx: Context | None = None) -> list:
    """List every remembered preference."""
    return _store(ctx).prefs_all()


def protocol_version() -> str:
    """The MCP protocol version this server negotiates."""
    return LATEST_PROTOCOL_VERSION


def demo_reset_hours() -> float:
    """STEWARD_DEMO_RESET_HOURS (0 or unset disables the reset job)."""
    try:
        return max(0.0, float(os.environ.get("STEWARD_DEMO_RESET_HOURS", "0")))
    except ValueError:
        return 0.0


def start_reset_job(hours: float) -> threading.Thread | None:
    """Wipe every per-visitor store every `hours` hours (hosted demo)."""
    if hours <= 0:
        return None
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(hours * 3600):
            dropped = pool.reset()
            print(f"Steward demo reset: dropped {dropped} visitor store(s)",
                  flush=True)

    t = threading.Thread(target=loop, name="steward-demo-reset", daemon=True)
    t.stop = stop  # type: ignore[attr-defined]
    t.start()
    return t


def main() -> None:
    parser = argparse.ArgumentParser(description="Steward MCP server")
    parser.add_argument("--host", default=os.environ.get("STEWARD_MCP_HOST",
                                                         "127.0.0.1"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("STEWARD_MCP_PORT", "8899")))
    args = parser.parse_args()
    print(f"Steward MCP server {SERVER_VERSION} on "
          f"http://{args.host}:{args.port}/mcp "
          f"(protocol {LATEST_PROTOCOL_VERSION})", flush=True)
    hours = demo_reset_hours()
    if hours:
        pool.reset()  # a fresh container starts with no visitor data
        start_reset_job(hours)
        print(f"Demo mode: visitor stores reset every {hours:g}h", flush=True)
    mcp.run(transport="streamable-http", host=args.host, port=args.port,
            streamable_http_path="/mcp")


if __name__ == "__main__":
    main()
