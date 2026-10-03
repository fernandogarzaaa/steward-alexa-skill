"""Steward web app: the simulated Alexa+ experience.

A small FastAPI app serving a chat UI that drives the Steward agent. Per the
hackathon rules, entrants may "simulate the Alexa+ experience in a web app
using their own agentic tools": this is that simulation, and every turn it
takes runs the real Strands agent against the real MCP server.

Run:  python -m steward.web [--host 127.0.0.1] [--port 8898]
Then open http://127.0.0.1:8898/ (the MCP server must be running too).
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from .agent import StewardAgent
from .models import ConfigurationError, build_model, describe_active_provider

WEBUI_DIR = Path(__file__).resolve().parent.parent / "webui"


def mcp_url() -> str:
    return os.environ.get("STEWARD_MCP_URL", "http://127.0.0.1:8899/mcp")


def create_app(agent_factory=None) -> FastAPI:
    """Build the FastAPI app.

    agent_factory: optional zero-arg callable returning a context manager
    that yields an object with .run(task) and .tool_names. Used by tests
    to inject a scripted agent; production uses the real StewardAgent.
    """
    app = FastAPI(title="Steward (simulated Alexa+ experience)")
    holder: dict = {"cm": None, "agent": None, "error": None}

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

    @app.get("/api/health")
    def health():
        return {"ok": True, "model": describe_active_provider(),
                "mcp_url": mcp_url()}

    @app.get("/api/tools")
    def tools():
        try:
            agent = get_agent()
        except ConfigurationError as e:
            raise HTTPException(status_code=503, detail=str(e))
        return {"tools": agent.tool_names}

    @app.post("/api/chat")
    async def chat(payload: dict):
        message = (payload.get("message") or "").strip()
        if not message:
            raise HTTPException(status_code=400,
                                detail="message must be a non-empty string")
        try:
            agent = get_agent()
        except ConfigurationError as e:
            raise HTTPException(status_code=503, detail=str(e))
        try:
            out = await asyncio.to_thread(agent.run, message)
        except Exception as e:  # model or MCP failure: report, don't fake
            raise HTTPException(status_code=502,
                                detail="agent run failed: %s" % e)
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
    import uvicorn
    print(f"Steward web experience on http://{args.host}:{args.port}/ "
          f"(model: {describe_active_provider()})", flush=True)
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
