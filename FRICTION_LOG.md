# Friction log

Honest notes from building Steward. Per the hackathon rules, friction logs
are encouraged; these are real issues hit during development, with the
workarounds used.

## 1. pip hash mismatch from a stale cache (Critical, worked around)
Installing `mcp` failed with `ERROR: THESE PACKAGES DO NOT MATCH THE HASHES
FROM THE REQUIREMENTS FILE` (expected sha256 9dab55f5..., got 545e0477...).
The local pip cache held a stale wheel. Workaround: `pip install
--no-cache-dir`. Everything installed cleanly after that. Actionable
suggestion: pip could name the offending cached file so it can be removed
surgically instead of disabling the whole cache.

## 2. mcp 2.x renamed FastMCP to MCPServer (Important, worked around)
`from mcp.server.fastmcp import FastMCP` raises ModuleNotFoundError on
mcp 2.x; the class is now `mcp.server.mcpserver.MCPServer` and several
kwargs changed (`path` became `streamable_http_path`,
`streamablehttp_client` became `streamable_http_client`,
`InitializeResult.protocolVersion` became `protocol_version`). The migration
guide exists but the renames are easy to miss when following older examples.
Suggestion: keep import-time deprecation shims for one major version.

## 3. Vendored httpx2 crashes on no_proxy entries (Critical, worked around)
`httpx2.AsyncClient()` raised `InvalidURL("Invalid port: ':1]'")` at
construction time because this machine's `no_proxy` contains entries like
`*[::1]`, which the vendored URL parser cannot handle. This broke every
localhost MCP client (official SDK and Strands) before any request was made.
Workaround: `steward/netenv.py` normalizes `no_proxy`/`NO_PROXY` to plain
host forms before constructing local clients. Suggestion: parse proxy env
lazily per request, or at least skip malformed entries instead of crashing
client construction.

## 4. Strands MCPClient double-start (Important, worked around)
Calling `MCPClient.__enter__()` and then passing the client to `Agent(...)`
fails with "the client session is currently running", because the Agent's
tool loading starts the provider itself. The fix is to let the Agent own the
client lifecycle (create it, pass it in, stop it on exit). Suggestion: the
Agent could detect an already-started provider and reuse it instead of
erroring.

## 5. anyio cancel-scope strictness in tests (Nice-to-have, worked around)
Returning from inside `async for` over an async generator that holds MCP
client context managers raised "Attempted to exit cancel scope in a different
task". Restructured the test helper to a plain async function. Not a library
bug, just a sharp edge when combining async generators with anyio.

## Severity key
Critical: blocked the build until resolved. Important: cost real debugging
time. Nice-to-have: minor.
