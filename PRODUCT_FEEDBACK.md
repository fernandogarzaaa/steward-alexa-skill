# Product feedback

Per the hackathon submission requirements: which tools were used, what
worked, what needs work, onboarding, and whether I would build with them
again. All of this comes from actually building Steward with them.

## MCP Python SDK (mcp 2.x) - used for the self-hosted MCP server

What worked well: the Streamable HTTP transport works out of the box,
`@mcp.tool()` decorators with type hints generate correct schemas, and the
client session API (`initialize`, `list_tools`, `call_tool`) is clean.
Performance and reliability were solid once running.

What needs work: the 1.x to 2.x renames (FastMCP to MCPServer, client
import renames, snake_case result fields) break every existing example with
no import-time shim. The vendored httpx2 copy crashes client construction on
common `no_proxy` values (see FRICTION_LOG.md entry 3); that one cost real
debugging time because the error points at URL parsing, not at the proxy env.

Onboarding: moderate. The README gets a server running fast, but the
Streamable HTTP deployment details (stateless vs stateful, path config,
session headers) are scattered across the docs and the migration guide.

Would I build with it again: yes. It is the reference implementation and
the protocol behavior is correct.

## Strands SDK (strands-agents 1.57) - used for the agent framework

What worked well: `Agent(model=..., tools=[mcp_client])` is genuinely
expressive; the MCPClient tool provider discovers tools and converts them
with almost no glue code. BedrockModel and OpenAIModel share one interface,
which made the pluggable model layer small.

What needs work: the MCPClient lifecycle is surprising (the Agent starts the
provider itself, so pre-starting it errors out; see FRICTION_LOG.md entry 4).
The event-stream format for scripted testing is under-documented; I worked
it out from the source.

Onboarding: good for the happy path, rough at the edges. A short "custom
model provider" guide with the exact event shapes would help.

Would I build with it again: yes, especially for AWS-centered agent work.

## Devpost - used for hackathon registration and submission

What worked well: registration was quick, the rules pages are thorough, and
the submission checklist is clear.

What needs work: some registration form widgets (custom checkboxes) do not
toggle with normal clicks in all browsers; I had to click the required-field
asterisk inside the label to check one.

Onboarding: smooth.

Would I build with it again: yes.
