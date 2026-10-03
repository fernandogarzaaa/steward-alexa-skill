"""MCP server tests: real Streamable HTTP server, official SDK client."""
import asyncio

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

EXPECTED_TOOLS = {
    "clock_now", "calendar_add", "calendar_list", "calendar_delete",
    "reminder_add", "reminder_list", "reminder_done",
    "note_save", "note_search", "note_get",
    "prefs_set", "prefs_get", "prefs_all",
}


async def _with_session(url, fn):
    """Open a client session, run fn(session, init), then clean up."""
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            return await fn(session, init)


def test_protocol_version_meets_hackathon_minimum(mcp_server_url):
    async def go():
        async def fn(session, init):
            return init.protocol_version
        return await _with_session(mcp_server_url, fn)
    version = asyncio.run(go())
    # Hackathon rules require MCP spec >= 2025-11-25; date-ordered compare works.
    assert version >= "2025-11-25", f"protocol version {version} too old"
    print(f"MCP protocol version: {version}")


def test_list_tools(mcp_server_url):
    async def go():
        async def fn(session, _init):
            tools = await session.list_tools()
            return {t.name for t in tools.tools}
        return await _with_session(mcp_server_url, fn)
    names = asyncio.run(go())
    assert EXPECTED_TOOLS <= names, f"missing: {EXPECTED_TOOLS - names}"


def test_tool_roundtrip_over_http(mcp_server_url):
    async def go():
        async def fn(session, _init):
            now = await session.call_tool("clock_now", {})
            assert now.content, "clock_now returned no content"

            added = await session.call_tool(
                "calendar_add",
                {"title": "MCP test", "date": "2026-11-01",
                 "start_time": "09:30"})
            assert "MCP test" in added.content[0].text

            listed = await session.call_tool("calendar_list",
                                             {"date": "2026-11-01"})
            assert "MCP test" in listed.content[0].text

            await session.call_tool("prefs_set", {"key": "t", "value": "v"})
            got = await session.call_tool("prefs_get", {"key": "t"})
            assert "v" in got.content[0].text
            return True
        return await _with_session(mcp_server_url, fn)
    assert asyncio.run(go()) is True
