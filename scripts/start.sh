#!/usr/bin/env bash
# Run Steward as one container: MCP server (private, 127.0.0.1) + web app
# (public, 0.0.0.0:$PORT). Exits as soon as either process dies so the
# platform restarts the container instead of serving a half-broken app.
set -euo pipefail

export PORT="${PORT:-8080}"
export STEWARD_MCP_HOST="${STEWARD_MCP_HOST:-127.0.0.1}"
export STEWARD_MCP_PORT="${STEWARD_MCP_PORT:-8899}"
export STEWARD_MCP_URL="${STEWARD_MCP_URL:-http://127.0.0.1:${STEWARD_MCP_PORT}/mcp}"
PY="${PYTHON:-python}"

# SQLite lives under /data (mount a volume there to keep it) and falls
# back to /tmp when /data is missing or not writable (e.g. root-owned volume).
if [ -z "${STEWARD_DB:-}" ]; then
  if mkdir -p /data 2>/dev/null && [ -w /data ]; then
    export STEWARD_DB=/data/steward.db
  else
    mkdir -p /tmp/steward
    export STEWARD_DB=/tmp/steward/steward.db
  fi
fi
echo "start.sh: STEWARD_DB=${STEWARD_DB}"

"$PY" -m steward.mcp_server --host "$STEWARD_MCP_HOST" --port "$STEWARD_MCP_PORT" &
MCP_PID=$!

cleanup() { kill "$MCP_PID" "${WEB_PID:-}" 2>/dev/null || true; }
trap cleanup EXIT
trap 'exit 143' TERM INT

# Wait for the MCP server's /health (no curl in slim images: use Python).
for i in $(seq 1 60); do
  if ! kill -0 "$MCP_PID" 2>/dev/null; then
    echo "start.sh: MCP server exited during startup" >&2
    exit 1
  fi
  if "$PY" - "$STEWARD_MCP_HOST" "$STEWARD_MCP_PORT" <<'PYEOF' 2>/dev/null
import sys, urllib.request
host, port = sys.argv[1], sys.argv[2]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
opener.open(f"http://{host}:{port}/health", timeout=1).read()
PYEOF
  then
    echo "start.sh: MCP server up on ${STEWARD_MCP_HOST}:${STEWARD_MCP_PORT}"
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "start.sh: MCP server not healthy after 30s" >&2
    exit 1
  fi
  sleep 0.5
done

"$PY" -m steward.web --host 0.0.0.0 --port "$PORT" &
WEB_PID=$!

# Exit when either process stops; the EXIT trap stops the other one.
set +e
wait -n "$MCP_PID" "$WEB_PID"
STATUS=$?
echo "start.sh: a Steward process exited (status ${STATUS}); stopping" >&2
exit "$STATUS"
