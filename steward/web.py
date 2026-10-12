"""Steward web app: the simulated Alexa+ experience.

A small FastAPI app serving a chat UI that drives the Steward agent. Per the
hackathon rules, entrants may "simulate the Alexa+ experience in a web app
using their own agentic tools": this is that simulation, and every turn it
takes runs the real Strands agent against the real MCP server.

Run:  python -m steward.web [--host 127.0.0.1] [--port 8898]
Then open http://127.0.0.1:8898/ (the MCP server must be running too).

Access control (see steward/security.py and README "Security"):
STEWARD_API_KEY protects /api/chat and /api/tools; /api/chat is also
rate-limited per client IP and capped in message length. Binding to a
non-loopback host without a key is refused unless STEWARD_ALLOW_OPEN=1.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from .agent import StewardAgent
from .models import ConfigurationError, build_model, describe_active_provider
from .security import (RateLimiter, _int_env, check_bind, extract_key,
                       key_matches, web_api_key)

logger = logging.getLogger(__name__)

WEBUI_DIR = Path(__file__).resolve().parent.parent / "webui"


def mcp_url() -> str:
    return os.environ.get("STEWARD_MCP_URL", "http://127.0.0.1:8899/mcp")


def create_app(agent_factory=None, api_key=None, limiter=None,
               max_message_chars=None) -> FastAPI:
    """Build the FastAPI app.

    agent_factory: optional zero-arg callable returning a context manager
    that yields an object with .run(task) and .tool_names. Used by tests
    to inject a scripted agent; production uses the real StewardAgent.
    api_key / limiter / max_message_chars default to the STEWARD_API_KEY,
    STEWARD_RATE_LIMIT_PER_MIN (20) and STEWARD_MAX_MESSAGE_CHARS (2000)
    environment variables.
    """
    app = FastAPI(title="Steward (simulated Alexa+ experience)")
    holder: dict = {"cm": None, "agent": None, "error": None}
    key = web_api_key() if api_key is None else api_key
    rate = limiter if limiter is not None else RateLimiter(
        _int_env("STEWARD_RATE_LIMIT_PER_MIN", 20), 60.0)
    max_chars = (_int_env("STEWARD_MAX_MESSAGE_CHARS", 2000)
                 if max_message_chars is None else max_message_chars)
    # One shared agent: serialize turns so concurrent requests cannot
    # interleave a conversation (thread-safety of the agent is not assumed).
    run_lock = threading.Lock()

    def require_key(request: Request) -> None:
        if key and not key_matches(extract_key(request.headers), key):
            raise HTTPException(status_code=401,
                                detail="missing or invalid API key",
                                headers={"WWW-Authenticate": "Bearer"})

    def rate_limited(request: Request) -> None:
        ip = request.client.host if request.client else "unknown"
        if not rate.allow(ip):
            raise HTTPException(status_code=429,
                                detail="rate limit exceeded, try again later",
                                headers={"Retry-After": str(int(rate.window))})

    def run_serialized(agent, message):
        with run_lock:
            return agent.run(message)

    def get_agent():
        if holder["agent"] is not None:
            return holder["agent"]
        if holder["error"] is not None:
            raise holder["error"]
        try:
            factory = agent_factory or (
                lambda: StewardAgent(build_model(), mcp_url()))
            cm = factory()
            holder["agent"] = cm.__enter__()
            holder["cm"] = cm
            return holder["agent"]
        except ConfigurationError as e:
            holder["error"] = e
            raise

    @app.get("/health")
    def liveness():
        # Public liveness probe (used by the keep-warm workflow). Reveals
        # nothing and does not touch the model or the MCP server.
        return {"ok": True}

    @app.get("/api/health")
    def health():
        return {"ok": True, "model": describe_active_provider(),
                "auth_required": bool(key)}

    @app.get("/api/tools", dependencies=[Depends(require_key)])
    def tools():
        try:
            agent = get_agent()
        except ConfigurationError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception:
            # e.g. the MCP server is unreachable: the service is
            # unavailable, not internally broken. Details go to the log.
            logger.exception("agent initialisation failed")
            raise HTTPException(status_code=503, detail="agent unavailable")
        return {"tools": agent.tool_names}

    @app.post("/api/chat",
              dependencies=[Depends(require_key), Depends(rate_limited)])
    async def chat(payload: dict):
        message = payload.get("message")
        message = message.strip() if isinstance(message, str) else ""
        if not message:
            raise HTTPException(status_code=400,
                                detail="message must be a non-empty string")
        if len(message) > max_chars:
            raise HTTPException(
                status_code=413,
                detail=f"message too long (max {max_chars} characters)")
        try:
            agent = get_agent()
        except ConfigurationError as e:
            raise HTTPException(status_code=503, detail=str(e))
        try:
            out = await asyncio.to_thread(run_serialized, agent, message)
        except Exception:  # model or MCP failure: report, don't fake
            logger.exception("agent run failed")
            raise HTTPException(status_code=502, detail="agent run failed")
        return {"reply": out["text"], "tool_calls": out["tool_calls"]}

    @app.get("/")
    def index():
        return FileResponse(WEBUI_DIR / "index.html")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Steward web experience")
    parser.add_argument("--host", default=os.environ.get("STEWARD_WEB_HOST",
                                                         "127.0.0.1"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("STEWARD_WEB_PORT", "8898")))
    args = parser.parse_args()
    check_bind(args.host, web_api_key(), "the Steward web app")
    import uvicorn
    print(f"Steward web experience on http://{args.host}:{args.port}/ "
          f"(model: {describe_active_provider()})", flush=True)
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
