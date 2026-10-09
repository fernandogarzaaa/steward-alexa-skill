"""Steward web app: the simulated Alexa+ experience.

A small FastAPI app serving a chat UI that drives the Steward agent. Per the
hackathon rules, entrants may "simulate the Alexa+ experience in a web app
using their own agentic tools": this is that simulation, and every turn it
takes runs the real Strands agent against the real MCP server.

Run:  python -m steward.web [--host 127.0.0.1] [--port 8898]
Then open http://127.0.0.1:8898/ (the MCP server must be running too).

Hosted demo mode (STEWARD_DEMO_MODE=1): every browser gets a random session
cookie, its own agent (conversation memory), and its own store namespace on
the MCP server, so strangers never see each other's data. Sessions are
capped, dropped when idle, and everything resets every
STEWARD_DEMO_RESET_HOURS hours.
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import os
import secrets
import threading
import time
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse

from .agent import StewardAgent
from .models import ConfigurationError, build_model, describe_active_provider
from .store import valid_namespace

WEBUI_DIR = Path(__file__).resolve().parent.parent / "webui"
SESSION_COOKIE = "steward_sid"


def mcp_url() -> str:
    return os.environ.get("STEWARD_MCP_URL", "http://127.0.0.1:8899/mcp")


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes",
                                                         "on")


def _env_num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def demo_settings() -> dict:
    """Demo-mode knobs, read from the environment."""
    return {
        "reset_hours": max(0.0, _env_num("STEWARD_DEMO_RESET_HOURS", 6)),
        "max_sessions": max(1, int(_env_num("STEWARD_DEMO_MAX_SESSIONS", 25))),
        "idle_minutes": max(1.0, _env_num("STEWARD_DEMO_IDLE_MINUTES", 60)),
        "max_chars": max(1, int(_env_num("STEWARD_DEMO_MAX_CHARS", 1000))),
    }


def _call_factory(factory, session_id: str):
    """Call an agent factory, passing the session id if it takes one."""
    try:
        takes_arg = len(inspect.signature(factory).parameters) >= 1
    except (TypeError, ValueError):
        takes_arg = False
    return factory(session_id) if takes_arg else factory()


def create_app(agent_factory=None, demo: bool | None = None) -> FastAPI:
    """Build the FastAPI app.

    agent_factory: optional callable returning a context manager that
    yields an object with .run(task) and .tool_names. It may take zero
    args, or one (the session id, demo mode only). Used by tests to inject
    a scripted agent; production uses the real StewardAgent.
    demo: force demo mode on/off; default reads STEWARD_DEMO_MODE.
    """
    demo = _env_flag("STEWARD_DEMO_MODE") if demo is None else demo
    settings = demo_settings()
    app = FastAPI(title="Steward (simulated Alexa+ experience)")

    factory = agent_factory or (
        lambda sid="": StewardAgent(build_model(), mcp_url(), session_id=sid))

    # session id -> {"cm", "agent", "seen", "lock"}; "" is the single
    # shared session used outside demo mode.
    sessions: OrderedDict[str, dict] = OrderedDict()
    state = {"error": None, "epoch": time.monotonic()}
    lock = threading.Lock()

    def _close(entry: dict) -> None:
        try:
            entry["cm"].__exit__(None, None, None)
        except Exception:
            pass

    def _sweep(now: float) -> list[dict]:
        """Drop expired/idle/excess sessions (caller holds the lock)."""
        if not demo:
            return []
        dropped: list[dict] = []
        reset_s = settings["reset_hours"] * 3600
        if reset_s and now - state["epoch"] >= reset_s:
            dropped.extend(sessions.values())
            sessions.clear()
            state["epoch"] = now
        idle_s = settings["idle_minutes"] * 60
        for sid in [s for s, e in sessions.items() if now - e["seen"] > idle_s]:
            dropped.append(sessions.pop(sid))
        while len(sessions) >= settings["max_sessions"]:
            dropped.append(sessions.popitem(last=False)[1])
        return dropped

    def get_agent(sid: str):
        if state["error"] is not None:
            raise state["error"]
        now = time.monotonic()
        with lock:
            entry = sessions.get(sid)
            if entry is not None:
                entry["seen"] = now
                sessions.move_to_end(sid)
                return entry
            dropped = _sweep(now)
        for old in dropped:
            _close(old)
        try:
            cm = _call_factory(factory, sid)
            agent = cm.__enter__()
        except ConfigurationError as e:
            state["error"] = e
            raise
        entry = {"cm": cm, "agent": agent, "seen": now,
                 "lock": threading.Lock()}
        with lock:
            existing = sessions.get(sid)
            if existing is None:
                sessions[sid] = entry
        if existing is not None:  # lost a race: keep the first one
            _close(entry)
            return existing
        return entry

    def session_id(request: Request, response: Response) -> str:
        """Per-browser id in demo mode; "" (one shared session) otherwise."""
        if not demo:
            return ""
        sid = request.cookies.get(SESSION_COOKIE, "")
        if not valid_namespace(sid):
            sid = secrets.token_urlsafe(16)
            https = (request.headers.get("x-forwarded-proto", "")
                     or request.url.scheme) == "https"
            response.set_cookie(
                SESSION_COOKIE, sid, httponly=True, samesite="lax",
                secure=https,
                max_age=int(settings["reset_hours"] * 3600) or None)
        return sid

    @app.get("/health")
    def health_cheap():
        # Liveness for platform health checks: no model or AWS calls.
        return {"ok": True}

    @app.get("/api/health")
    def health():
        body = {"ok": True, "model": describe_active_provider(),
                "mcp_url": mcp_url()}
        if demo:
            body["demo"] = {"reset_hours": settings["reset_hours"]}
        return body

    @app.get("/api/tools")
    def tools(request: Request, response: Response):
        try:
            entry = get_agent(session_id(request, response))
        except ConfigurationError as e:
            raise HTTPException(status_code=503, detail=str(e))
        return {"tools": entry["agent"].tool_names}

    @app.post("/api/chat")
    async def chat(payload: dict, request: Request, response: Response):
        message = (payload.get("message") or "").strip()
        if not message:
            raise HTTPException(status_code=400,
                                detail="message must be a non-empty string")
        if demo and len(message) > settings["max_chars"]:
            raise HTTPException(
                status_code=413,
                detail="message too long for the demo (max %d characters)"
                       % settings["max_chars"])
        sid = session_id(request, response)
        try:
            entry = await asyncio.to_thread(get_agent, sid)
        except ConfigurationError as e:
            raise HTTPException(status_code=503, detail=str(e))

        def run():
            with entry["lock"]:  # one turn at a time per conversation
                return entry["agent"].run(message)
        try:
            out = await asyncio.to_thread(run)
        except Exception as e:  # model or MCP failure: report, don't fake
            raise HTTPException(status_code=502,
                                detail="agent run failed: %s" % e)
        return {"reply": out["text"], "tool_calls": out["tool_calls"]}

    @app.get("/")
    def index():
        return FileResponse(WEBUI_DIR / "index.html")

    app.state.sessions = sessions  # exposed for tests and debugging
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Steward web experience")
    parser.add_argument("--host", default=os.environ.get("STEWARD_WEB_HOST",
                                                         "127.0.0.1"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("STEWARD_WEB_PORT", "8898")))
    args = parser.parse_args()
    import uvicorn
    print(f"Steward web experience on http://{args.host}:{args.port}/ "
          f"(model: {describe_active_provider()})", flush=True)
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
