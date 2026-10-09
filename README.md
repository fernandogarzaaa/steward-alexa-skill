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

33 tests: SQLite store CRUD, the MCP server over real Streamable HTTP
(protocol version asserted >= 2025-11-25), provider selection, the Strands
agent loop against the real server with a scripted model double, the
web API, and the hosted-demo isolation (per-visitor stores, reset job,
session cookies).

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
tests/           pytest suite (33 tests)
scripts/start.sh One-container launcher (MCP server + web app)
Dockerfile       Hosted demo image (python:3.12-slim)
railway.json     Railway build/deploy config
DEPLOY_AWS.md    Step-by-step AWS (EC2 + Bedrock) deployment runbook
```

## Deploy (hosted demo)

The repo ships as **one container**: `scripts/start.sh` starts the MCP
server privately on `127.0.0.1:8899`, waits for its `/health`, then serves
the web app on `0.0.0.0:$PORT` (default 8080). If either process dies the
container exits so the platform restarts it. `GET /health` is a cheap
liveness check (no model or AWS calls); `GET /api/health` also reports the
selected model.

**Public demo isolation.** The image sets `STEWARD_DEMO_MODE=1`: each
browser gets a random `steward_sid` cookie, its own agent conversation,
and its own SQLite store on the MCP server (sent as the
`X-Steward-Session` header), so visitors never see each other's data.
**The demo resets every 6 hours** (`STEWARD_DEMO_RESET_HOURS`): all visitor
stores are deleted and conversations dropped. SQLite lives at
`/data/steward.db` (falls back to `/tmp` when `/data` is not writable).
Without `STEWARD_DEMO_MODE`, Steward behaves exactly as before: one user,
one shared store.

```bash
docker build -t steward .
docker run -p 8080:8080 \
  -e STEWARD_MODEL_BASE_URL=https://openrouter.ai/api/v1 \
  -e STEWARD_MODEL_ID=amazon/nova-micro-v1 \
  -e STEWARD_MODEL_API_KEY=sk-or-... steward
curl localhost:8080/health
```

### Railway (any OpenAI-compatible key)

1. New project, deploy from this GitHub repo. Railway builds the
   `Dockerfile` and injects `PORT`.
2. Variables: `STEWARD_MODEL_PROVIDER=openai-compatible`,
   `STEWARD_MODEL_BASE_URL=https://openrouter.ai/api/v1`,
   `STEWARD_MODEL_ID=amazon/nova-micro-v1` (or any OpenRouter model with tool calling),
   `STEWARD_MODEL_API_KEY=<your OpenRouter key>`.
3. Healthcheck path `/health`. `railway.json` sets it, but Railway has
   deprecated `railway.json` (new services ignore it and existing files
   stop being read on 2026-12-01), so also set it under Settings >
   Deploy > Healthcheck Path.
4. Settings > Networking > Generate Domain for a stable HTTPS URL.
   Optional: attach a volume at `/data` (the demo resets anyway).

### AWS (Bedrock Nova Micro, IAM role)

AWS App Runner stopped accepting new customers on 2026-04-30, so it is
not an option for a new account. The cheapest always-on AWS option that
can use an **IAM role** (no access keys on the box) is a single
**EC2 t4g.micro** (Graviton, 1 GiB) running the container, with an
Elastic IP and an instance profile allowed to call
`amazon.nova-micro-v1:0`. About **$10.60/month** in us-east-1:
instance $6.13 + public IPv4 $3.65 + 10 GB gp3 $0.80, plus Bedrock tokens
(Nova Micro is $0.035 per 1M input / $0.14 per 1M output tokens, well
under $1 per 1,000 demo turns).

Lightsail is cheaper on paper (a $7/month 1 GB instance including a static
IP, or a $7/month Nano container service), but neither supports IAM roles,
so Bedrock would need long-lived access keys stored on the host.

Full commands, IAM policy, and verification: [DEPLOY_AWS.md](DEPLOY_AWS.md).

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
| `PORT` | `8080` | Public port, container only (`scripts/start.sh`) |
| `STEWARD_DEMO_MODE` | off (`1` in the Docker image) | Per-browser sessions and stores |
| `STEWARD_DEMO_RESET_HOURS` | `6` in demo mode, else off | Wipe visitor data every N hours |
| `STEWARD_DEMO_MAX_SESSIONS` | `25` | Live conversations kept (oldest dropped) |
| `STEWARD_DEMO_IDLE_MINUTES` | `60` | Drop a conversation after this idle time |
| `STEWARD_DEMO_MAX_CHARS` | `1000` | Longest message the demo accepts |

## License

MIT. See [LICENSE](LICENSE).
