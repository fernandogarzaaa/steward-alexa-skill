# Steward - an Alexa+ Agent Skill

Steward is a personal-operations Agent Skill for the **Alexa+ track** of the
Build, Ship, Shape: Amazon Developer Hackathon. It runs errands across your
calendar, reminders, notes, and remembered preferences through a **self-hosted
MCP server** (Streamable HTTP, MCP spec 2025-11-25), orchestrated by a
**Strands SDK** agent. A small web app provides the **simulated Alexa+
experience** allowed by the hackathon rules.

The agent also enters the **AWS Builder** mini-challenge (Strands SDK agent
framework plus a genuine Amazon Bedrock model path with documented setup)
and the **Open Source** mini-challenge (this repo: new, MIT-licensed, built
during the hackathon window).

## How it works

```
You type an errand
      v
Web UI (simulated Alexa+ experience, FastAPI)
      v
Strands SDK agent  (AWS Builder: Strands + Bedrock integration)
      |  MCP client (Streamable HTTP)
      v
Steward MCP server (13 tools, SQLite store, zero cloud dependencies)
```

Example: "Plan my Saturday: dentist at 10am, buy groceries, call mom" makes
the agent call `clock_now`, `calendar_add`, `reminder_add`, and `note_save`,
then summarize the plan. Preferences persist across sessions via
`prefs_set` / `prefs_get` (the agent checks them before assuming anything).

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Configure the model

The agent needs a model. Pick one:

**Option A: Amazon Bedrock (the AWS path).** Configure AWS credentials the
usual way (env vars, `~/.aws/config`, or IAM role), then:

```bash
export STEWARD_BEDROCK_MODEL_ID=amazon.nova-micro-v1:0   # or any Bedrock id
export STEWARD_AWS_REGION=us-east-1
```

**Option B: any OpenAI-compatible endpoint** (local server, gateway, etc.):

```bash
export STEWARD_MODEL_BASE_URL=http://127.0.0.1:11434/v1
export STEWARD_MODEL_ID=your-model-id
export STEWARD_MODEL_API_KEY=your-key   # or "not-needed"
```

With AWS credentials present and no `STEWARD_MODEL_BASE_URL`, Bedrock is
selected automatically. Set `STEWARD_MODEL_PROVIDER=bedrock` or
`STEWARD_MODEL_PROVIDER=openai-compatible` to force one. With nothing
configured, the agent raises a clear error instead of guessing.

## Run it

Terminal 1, the MCP server (Streamable HTTP on 127.0.0.1:8899):

```bash
python -m steward.mcp_server
```

Terminal 2, the web experience:

```bash
python -m steward.web
```

Open http://127.0.0.1:8898/ and give Steward an errand, for example:

- "Plan my Saturday: dentist at 10am, buy groceries, and remind me to call mom"
- "Remember that I prefer morning appointments"
- "What is on my calendar tomorrow?"

The UI shows which MCP tools ran for each reply.

### Talk to the MCP server directly

Any MCP client works. With the Python MCP SDK:

```python
import asyncio
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    async with streamable_http_client("http://127.0.0.1:8899/mcp") as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print([t.name for t in tools.tools])

asyncio.run(main())
```

## MCP tools (13)

| Tool | What it does |
|---|---|
| `clock_now` | Current local date/time (ISO 8601) |
| `calendar_add` | Add an event (title, date, start_time, notes) |
| `calendar_list` | List events, optionally filtered by date |
| `calendar_delete` | Delete an event by id |
| `reminder_add` | Add a reminder (text, due) |
| `reminder_list` | List open reminders |
| `reminder_done` | Mark a reminder completed |
| `note_save` | Save a note (title, body, tags) |
| `note_search` | Full-text search over notes |
| `note_get` | Fetch one full note by id |
| `prefs_set` | Remember a preference across sessions |
| `prefs_get` | Read a remembered preference |
| `prefs_all` | List all remembered preferences |

## Tests

```bash
python -m pytest tests/ -q
```

21 tests: SQLite store CRUD, the MCP server over real Streamable HTTP
(protocol version asserted >= 2025-11-25), provider selection, the Strands
agent loop against the real server with a scripted model double, and the
web API.

## Project layout

```
steward/
  store.py       SQLite storage (events, reminders, notes, prefs)
  mcp_server.py  MCPServer "steward" v1.0.0, 13 tools, Streamable HTTP
  models.py      build_model(): Bedrock or OpenAI-compatible (Strands)
  agent.py       StewardAgent: Strands agent + MCP client + session memory
  web.py         FastAPI app: the simulated Alexa+ experience
  netenv.py      proxy-env sanitizer for localhost MCP clients
webui/index.html Single-page chat UI (no build step)
tests/           pytest suite (21 tests)
```

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `STEWARD_DB` | `~/.steward/steward.db` | SQLite file |
| `STEWARD_MCP_HOST` / `STEWARD_MCP_PORT` | `127.0.0.1` / `8899` | MCP server bind |
| `STEWARD_MCP_URL` | `http://127.0.0.1:8899/mcp` | Where the agent finds the server |
| `STEWARD_WEB_HOST` / `STEWARD_WEB_PORT` | `127.0.0.1` / `8898` | Web app bind |
| `STEWARD_MODEL_PROVIDER` | `auto` | `bedrock`, `openai-compatible`, or `auto` |
| `STEWARD_BEDROCK_MODEL_ID` | `amazon.nova-micro-v1:0` | Bedrock model id |
| `STEWARD_AWS_REGION` | AWS config chain | Bedrock region |
| `STEWARD_MODEL_BASE_URL` | (unset) | OpenAI-compatible base URL |
| `STEWARD_MODEL_API_KEY` | (unset) | Key for the OpenAI-compatible endpoint |
| `STEWARD_MODEL_ID` | (unset) | Model id for the OpenAI-compatible endpoint |

## License

MIT. See [LICENSE](LICENSE).
