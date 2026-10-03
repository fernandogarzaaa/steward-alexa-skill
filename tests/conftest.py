"""Shared fixtures: temp DB and a real MCP server subprocess."""
import os
import socket
import subprocess
import sys
import tempfile
import time

import pytest

# The MCP server reads STEWARD_DB at import time; point it at a temp file
# before any steward.* import happens (conftest imports first).
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["STEWARD_DB"] = _tmp.name

# Local MCP clients must bypass the egress proxy; the vendored httpx copy
# in mcp>=2 crashes parsing some no_proxy entries (see steward/netenv.py).
from steward.netenv import sanitize_proxy_env  # noqa: E402

sanitize_proxy_env()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def mcp_server_url():
    """A real `python -m steward.mcp_server` subprocess on an ephemeral port."""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "steward.mcp_server",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env={**os.environ, "PYTHONPATH": os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            pytest.fail("MCP server exited early:\n" + out)
        with socket.socket() as s:
            s.settimeout(0.5)
            try:
                s.connect(("127.0.0.1", port))
                break
            except OSError:
                time.sleep(0.2)
    else:
        proc.terminate()
        pytest.fail("MCP server did not start in 30s")
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
