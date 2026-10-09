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
- For a request with several items, handle EVERY item: first enumerate the
  items in the request, then do each one with the available tools. Do not
  stop after the first item, and do not skip an item because it looks
  similar to one you already did.
- For multi-step errands, do every step with the available tools, then reply
  with a short summary of what you did, as a concise list.
- Keep replies short and plain. No em dashes, ever.
- Never invent events, reminders, or notes: only report what the tools
  actually returned."""

# Follow-up turn used when the model ends its turn with no text (some
# reasoning models finish after tool calls with the visible reply stuck in
# reasoning content, or with an empty final message). It also gives the
# model a chance to complete any request item it missed on the first pass.
VERIFY_PROMPT = (
    "Review the original request and the tools you just called. "
    "If any part of the request was not handled, handle it now with the "
    "tools. Then reply with a short plain-text summary of everything you "
    "did, as a concise list."
)


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


def _describe_call(name: str, tool_input: dict) -> str:
    """One human-readable phrase for a tool call, for the fallback summary."""
    inp = tool_input or {}
    if name == "clock_now":
        return "checked the time"
    if name == "calendar_add":
        when = str(inp.get("date", "")).strip()
        at = str(inp.get("start_time", "")).strip()
        return "added calendar event '{}'{}{}".format(
            inp.get("title", ""),
            f" on {when}" if when else "",
            f" at {at}" if at else "",
        )
    if name == "calendar_list":
        return "listed calendar events"
    if name == "calendar_delete":
        return "deleted a calendar event"
    if name == "reminder_add":
        due = str(inp.get("due", "")).strip()
        return "added reminder '{}'{}".format(
            inp.get("text", ""), f" (due {due})" if due else ""
        )
    if name == "reminder_list":
        return "listed reminders"
    if name == "reminder_done":
        return "marked a reminder done"
    if name == "note_save":
        return "saved note '{}'".format(inp.get("title", ""))
    if name == "note_search":
        return "searched notes"
    if name == "note_get":
        return "read a note"
    if name == "prefs_set":
        return "saved preference '{}'".format(inp.get("key", ""))
    if name == "prefs_get":
        return "read a preference"
    if name == "prefs_all":
        return "listed preferences"
    return f"called {name}" if name else "called a tool"


def _summarize_tool_calls(tool_calls: list[dict]) -> str:
    """Deterministic last-resort summary so the reply is never blank."""
    if not tool_calls:
        return "I could not complete that request."
    bits = [_describe_call(c.get("name", ""), c.get("input", {})) for c in tool_calls]
    return "Done: " + "; ".join(bits) + "."


def extract_tool_calls(messages: list) -> list[dict]:
    """Pull the tool calls out of a Strands message history.

    Returns [{"name": ..., "input": {...}}, ...] in call order.
    """
    calls: list[dict] = []
    for msg in messages or []:
        for block in msg.get("content", []) or []:
            use = block.get("toolUse")
            if use:
                calls.append(
                    {"name": use.get("name", ""), "input": use.get("input", {})}
                )
    return calls


class StewardAgent:
    """Context manager around a Strands Agent connected to the MCP server.

    Usage:
        with StewardAgent(build_model(), "http://127.0.0.1:8899/mcp") as s:
            out = s.run("Plan my Saturday")
            print(out["text"], out["tool_calls"])
    """

    def __init__(
        self,
        model: Model | None = None,
        mcp_url: str = "",
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        session_id: str = "",
    ) -> None:
        self._model = model or build_model()
        self._mcp_url = mcp_url or "http://127.0.0.1:8899/mcp"
        self._system_prompt = system_prompt
        # Hosted demo: the MCP server keeps a separate store per session id.
        self._session_id = session_id
        self._client: MCPClient | None = None
        self._agent: Agent | None = None

    def __enter__(self) -> "StewardAgent":
        # Local MCP clients must bypass the egress proxy, and the vendored
        # httpx copy in mcp>=2 crashes parsing some no_proxy entries.
        sanitize_proxy_env()
        headers = {"X-Steward-Session": self._session_id} if self._session_id else None
        self._client = MCPClient(url=self._mcp_url, headers=headers)
        try:
            # Agent init loads the tool provider, which starts the client.
            self._agent = Agent(
                model=self._model,
                tools=[self._client],
                system_prompt=self._system_prompt,
            )
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
        """Run one errand. Returns {"text": reply, "tool_calls": [...]}.

        The reply is guaranteed non-empty: if the model ends its turn with
        no text block (seen with reasoning models that leave the visible
        reply in reasoning content, or stop right after tool calls), we
        take one follow-up turn asking for a plain-text summary, which
        also lets the model complete any request item it missed. If that
        still yields nothing, we fall back to a deterministic summary of
        the tool calls actually made.
        """
        if self._agent is None:
            raise RuntimeError("StewardAgent is not entered")
        result = self._agent(task)
        text = _message_text(result.message)
        if not text:
            result = self._agent(VERIFY_PROMPT)
            text = _message_text(result.message)
        tool_calls = extract_tool_calls(self._agent.messages)
        if not text:
            text = _summarize_tool_calls(tool_calls)
        return {"text": text, "tool_calls": tool_calls}
