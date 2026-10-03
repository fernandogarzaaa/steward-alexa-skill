"""Steward agent: a Strands SDK agent wired to the Steward MCP server.

The agent is the "simulated Alexa+ experience": it takes a natural-language
errand, plans across the MCP tools (calendar, reminders, notes, prefs), and
executes the steps. Cross-session memory comes from the prefs_* tools, which
persist in the server's SQLite store.
"""
from __future__ import annotations

from strands import Agent
from strands.models.model import Model
from strands.tools.mcp import MCPClient

from .models import build_model
from .netenv import sanitize_proxy_env

DEFAULT_SYSTEM_PROMPT = """You are Steward, a household operations assistant
running as an Alexa+ Agent Skill. You help one person run their personal life:
calendar, reminders, notes, and preferences.

Rules:
- Always call clock_now first when the request involves relative dates like
  "today", "tomorrow", or "Saturday", so dates are correct.
- Before assuming a preference (appointment times, routines), call
  prefs_get. When the user states a lasting preference, store it with
  prefs_set so future sessions remember it.
- For multi-step errands, do every step with the available tools, then reply
  with a short summary of what you did, as a concise list.
- Keep replies short and plain. No em dashes, ever.
- Never invent events, reminders, or notes: only report what the tools
  actually returned."""


def _message_text(message) -> str:
    """Concatenate text blocks, skipping reasoning/thinking blocks.

    Reasoning models (e.g. Nemotron) put chain-of-thought in
    reasoningContent blocks; the user-visible reply is in text blocks.
    """
    parts = []
    for block in message.get("content", []) or []:
        text = block.get("text")
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def extract_tool_calls(messages: list) -> list[dict]:
    """Pull the tool calls out of a Strands message history.

    Returns [{"name": ..., "input": {...}}, ...] in call order.
    """
    calls: list[dict] = []
    for msg in messages or []:
        for block in msg.get("content", []) or []:
            use = block.get("toolUse")
            if use:
                calls.append({"name": use.get("name", ""),
                              "input": use.get("input", {})})
    return calls


class StewardAgent:
    """Context manager around a Strands Agent connected to the MCP server.

    Usage:
        with StewardAgent(build_model(), "http://127.0.0.1:8899/mcp") as s:
            out = s.run("Plan my Saturday")
            print(out["text"], out["tool_calls"])
    """

    def __init__(self, model: Model | None = None, mcp_url: str = "",
                 system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> None:
        self._model = model or build_model()
        self._mcp_url = mcp_url or "http://127.0.0.1:8899/mcp"
        self._system_prompt = system_prompt
        self._client: MCPClient | None = None
        self._agent: Agent | None = None

    def __enter__(self) -> "StewardAgent":
        # Local MCP clients must bypass the egress proxy, and the vendored
        # httpx copy in mcp>=2 crashes parsing some no_proxy entries.
        sanitize_proxy_env()
        self._client = MCPClient(url=self._mcp_url)
        try:
            # Agent init loads the tool provider, which starts the client.
            self._agent = Agent(model=self._model, tools=[self._client],
                                system_prompt=self._system_prompt)
        except Exception:
            self._client.stop(None, None, None)
            self._client = None
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            if self._client is not None:
                self._client.__exit__(*exc)
        finally:
            self._client = None
            self._agent = None

    @property
    def tool_names(self) -> list[str]:
        """Names of the MCP tools currently exposed to the agent."""
        if self._agent is None:
            raise RuntimeError("StewardAgent is not entered")
        names: list[str] = []
        for t in self._agent.tool_registry.registry.values():
            name = getattr(t, "tool_name", None) or getattr(t, "name", None)
            if name:
                names.append(str(name))
        return sorted(set(names))

    def run(self, task: str) -> dict:
        """Run one errand. Returns {"text": reply, "tool_calls": [...]}."""
        if self._agent is None:
            raise RuntimeError("StewardAgent is not entered")
        result = self._agent(task)
        return {
            "text": _message_text(result.message),
            "tool_calls": extract_tool_calls(self._agent.messages),
        }
