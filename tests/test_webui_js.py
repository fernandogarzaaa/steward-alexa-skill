"""The web UI must surface Steward's own error details (not a generic
"waking up" message) when the server answered with JSON."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parent.parent / "webui" / "index.html"


def _error_text(status, data):
    src = HTML.read_text(encoding="utf-8")
    fn = re.search(r"function errorText\(status, data\) \{.*?\n\}", src, re.S)
    assert fn, "errorText() not found in webui/index.html"
    js = fn.group(0) + f"\nconsole.log(errorText({status}, {json.dumps(data)}));"
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not installed")


def test_unconfigured_model_shows_setup_detail():
    msg = _error_text(503, {"detail": "No model configured. Set STEWARD_MODEL_BASE_URL"})
    assert "not set up" in msg and "STEWARD_MODEL_BASE_URL" in msg


def test_cold_start_without_json_stays_generic():
    assert "waking up" in _error_text(503, {})
    assert "waking up" in _error_text(504, {})


def test_agent_failure_points_at_model_or_mcp():
    assert "MCP server" in _error_text(502, {"detail": "agent run failed"})


def test_validation_detail_list_is_readable():
    msg = _error_text(422, {"detail": [{"msg": "field required"}]})
    assert "field required" in msg and "[object Object]" not in msg
