"""Agent tests: real StewardAgent + real MCP server, scripted model double.

The ScriptedModel is a minimal Strands Model implementation that plays a
canned tool-call script. Everything else in the loop (MCP client, tool
execution against SQLite, reply extraction) is the real production path.
"""
import json
from contextlib import contextmanager

from strands.models.model import Model

from steward.agent import StewardAgent, extract_tool_calls


class ScriptedModel(Model):
    """Plays scripted tool calls, then a final text reply."""

    def __init__(self, script, final_text="All done."):
        self._script = list(script)
        self._final_text = final_text
        self._config = {}
        self.calls = 0

    def update_config(self, **kwargs):
        self._config.update(kwargs)

    def get_config(self):
        return dict(self._config)

    def structured_output(self, *a, **k):
        raise NotImplementedError("not used in these tests")

    async def stream(self, messages, tool_specs=None, system_prompt=None,
                     **kwargs):
        self.calls += 1
        yield {"messageStart": {"role": "assistant"}}
        if self._script:
            name, tool_input = self._script.pop(0)
            tid = f"tool-{self.calls}"
            yield {"contentBlockStart": {
                "start": {"toolUse": {"toolUseId": tid, "name": name}}}}
            yield {"contentBlockDelta": {
                "delta": {"toolUse": {"input": json.dumps(tool_input)}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": self._final_text}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}


def test_extract_tool_calls_unit():
    messages = [
        {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "a", "name": "clock_now",
                         "input": {}}},
            {"text": "hi"},
        ]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "a", "content": [{"text": "t"}],
                            "status": "success"}}]},
    ]
    calls = extract_tool_calls(messages)
    assert calls == [{"name": "clock_now", "input": {}}]
    assert extract_tool_calls([]) == []
    assert extract_tool_calls(None) == []


def test_agent_runs_multistep_errand(mcp_server_url):
    script = [
        ("clock_now", {}),
        ("reminder_add", {"text": "Call mom", "due": "Saturday"}),
        ("prefs_set", {"key": "errand_test", "value": "yes"}),
    ]
    with StewardAgent(ScriptedModel(script, final_text="Reminder set."),
                       mcp_server_url) as agent:
        assert "clock_now" in agent.tool_names
        assert "reminder_add" in agent.tool_names
        out = agent.run("remind me to call mom Saturday")
    assert out["text"] == "Reminder set."
    assert [c["name"] for c in out["tool_calls"]] == [
        "clock_now", "reminder_add", "prefs_set"]
    assert out["tool_calls"][1]["input"]["text"] == "Call mom"


def test_agent_tool_side_effects_visible(mcp_server_url):
    script = [("note_save", {"title": "Errand note", "body": "buy milk",
                             "tags": "test"})]
    with StewardAgent(ScriptedModel(script), mcp_server_url) as agent:
        agent.run("save a note")
    # The note really landed in the server's store: search it back.
    with StewardAgent(ScriptedModel([("note_search", {"query": "Errand"})]),
                       mcp_server_url) as agent2:
        out = agent2.run("find the note")
    assert out["tool_calls"][0]["name"] == "note_search"


class TurnScriptedModel(Model):
    """Plays a canned script per turn, for the empty-reply failure mode.

    turns: list of (script, final_text). Turn 0 is the first agent call;
    the follow-up verify turn is detected by the nudge text in the last
    user message. A turn ends when its script is exhausted, yielding
    final_text, which may be "" (the observed gpt-oss failure mode).
    """

    def __init__(self, turns):
        self._turns = [(list(script), final) for script, final in turns]
        self._config = {}

    def update_config(self, **kwargs):
        self._config.update(kwargs)

    def get_config(self):
        return dict(self._config)

    def structured_output(self, *a, **k):
        raise NotImplementedError("not used in these tests")

    def _is_verify_turn(self, messages):
        # The nudge stays in history: tool results also arrive as user
        # messages, so scan all of them, not just the last one.
        for m in messages or []:
            if m.get("role") == "user":
                text = " ".join(
                    b.get("text", "") for b in m.get("content", []) or []
                )
                if "plain-text summary" in text:
                    return True
        return False

    async def stream(self, messages, tool_specs=None, system_prompt=None,
                     **kwargs):
        idx = 1 if self._is_verify_turn(messages) else 0
        script, final_text = self._turns[min(idx, len(self._turns) - 1)]
        yield {"messageStart": {"role": "assistant"}}
        if script:
            name, tool_input = script.pop(0)
            tid = f"turn{idx}-tool-{len(script)}"
            yield {"contentBlockStart": {
                "start": {"toolUse": {"toolUseId": tid, "name": name}}}}
            yield {"contentBlockDelta": {
                "delta": {"toolUse": {"input": json.dumps(tool_input)}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"start": {}}}
            if final_text:
                yield {"contentBlockDelta": {"delta": {"text": final_text}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}


def test_empty_reply_triggers_verify_turn(mcp_server_url):
    """Tools ran but the final text came back blank: the agent must take
    a follow-up turn and return a non-empty reply."""
    turns = [
        ([("clock_now", {}),
          ("reminder_add", {"text": "Buy groceries", "due": "Saturday"})],
         ""),
        ([], "Did the dentist, groceries, and mom call."),
    ]
    with StewardAgent(TurnScriptedModel(turns), mcp_server_url) as agent:
        out = agent.run("plan my Saturday")
    assert out["text"] == "Did the dentist, groceries, and mom call."
    assert [c["name"] for c in out["tool_calls"]] == [
        "clock_now", "reminder_add"]


def test_verify_turn_completes_missed_item(mcp_server_url):
    """The model skips an item on the first turn; the follow-up turn
    completes it and the reply covers everything."""
    turns = [
        ([("reminder_add", {"text": "Buy groceries", "due": "Saturday"})],
         ""),
        ([("calendar_add", {"title": "Dentist", "date": "2026-10-15",
                             "start_time": "10:00"})],
         "Done: groceries reminder and dentist appointment."),
    ]
    with StewardAgent(TurnScriptedModel(turns), mcp_server_url) as agent:
        out = agent.run("dentist and groceries")
    assert [c["name"] for c in out["tool_calls"]] == [
        "reminder_add", "calendar_add"]
    assert out["text"] == "Done: groceries reminder and dentist appointment."


def test_deterministic_fallback_when_model_stays_silent(mcp_server_url):
    """If the model returns no text even after the follow-up turn, the
    reply falls back to a deterministic summary of the tool calls."""
    turns = [
        ([("clock_now", {})], ""),
        ([], ""),
    ]
    with StewardAgent(TurnScriptedModel(turns), mcp_server_url) as agent:
        out = agent.run("what time is it")
    assert out["text"] == "Done: checked the time."
    assert [c["name"] for c in out["tool_calls"]] == ["clock_now"]


def test_summarize_tool_calls_unit():
    from steward.agent import _summarize_tool_calls
    assert _summarize_tool_calls([]) == "I could not complete that request."
    assert _summarize_tool_calls([
        {"name": "calendar_add",
         "input": {"title": "Dentist", "date": "2026-10-15",
                   "start_time": "10:00"}},
        {"name": "reminder_add",
         "input": {"text": "Buy groceries", "due": "Saturday"}},
    ]) == ("Done: added calendar event 'Dentist' on 2026-10-15 at 10:00; "
            "added reminder 'Buy groceries' (due Saturday).")
