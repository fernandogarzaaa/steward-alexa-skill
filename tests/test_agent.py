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
