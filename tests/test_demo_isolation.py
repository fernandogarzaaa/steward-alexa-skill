"""Hosted-demo tests: per-visitor store namespaces, reset job, web sessions.

Covers the multi-visitor isolation added for the public demo: the store
pool, the MCP server's X-Steward-Session namespacing over real Streamable
HTTP (through the production StewardAgent path), the periodic reset, and
the web app's per-browser session cookie.
"""
import os
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from steward.agent import StewardAgent
from steward.store import StorePool, valid_namespace
from steward.web import SESSION_COOKIE, create_app
from test_agent import ScriptedModel  # noqa: E402 (pytest puts tests/ on sys.path)

NS_A = "visitorAAAAAAAAAAAA"
NS_B = "visitorBBBBBBBBBBBB"


# Store pool -----------------------------------------------------------------

@pytest.fixture()
def pool(tmp_path):
    p = StorePool(str(tmp_path / "steward.db"))
    yield p
    p.reset()
    p.get().close()


def test_namespace_validation():
    assert valid_namespace("abcDEF12_-xyz")
    for bad in ("", None, "short", "../../etc/passwd", "a" * 65, "sp ace12345"):
        assert not valid_namespace(bad)


def test_pool_isolates_namespaces(pool, tmp_path):
    pool.get(NS_A).prefs_set("color", "red")
    pool.get(NS_B).prefs_set("color", "blue")
    pool.get().prefs_set("color", "green")  # default store, local use
    assert pool.get(NS_A).prefs_get("color")["value"] == "red"
    assert pool.get(NS_B).prefs_get("color")["value"] == "blue"
    assert pool.get().prefs_get("color")["value"] == "green"
    assert pool.get(NS_A) is pool.get(NS_A)
    assert (tmp_path / f"steward.ns-{NS_A}.db").exists()
    with pytest.raises(ValueError):
        pool.get("../escape")


def test_pool_reset_drops_visitors_keeps_default(pool, tmp_path):
    pool.get(NS_A).note_save("secret", "visitor data")
    pool.get().note_save("mine", "owner data")
    assert pool.reset() >= 1
    assert pool.namespaces() == []
    assert not list(tmp_path.glob("steward.ns-*.db"))
    assert pool.get(NS_A).note_search("visitor") == []  # fresh store
    assert len(pool.get().note_search("owner")) == 1


def test_reset_job_wipes_on_timer(monkeypatch):
    from steward import mcp_server
    tmp = tempfile.mkdtemp()
    monkeypatch.setattr(mcp_server, "pool",
                        StorePool(os.path.join(tmp, "steward.db")))
    mcp_server.pool.get(NS_A).prefs_set("k", "v")
    t = mcp_server.start_reset_job(0.5 / 3600)  # every half second
    try:
        deadline = time.time() + 5
        while time.time() < deadline and mcp_server.pool.namespaces():
            time.sleep(0.1)
        assert mcp_server.pool.namespaces() == []
    finally:
        t.stop.set()
    assert mcp_server.start_reset_job(0) is None


def test_demo_reset_hours_env(monkeypatch):
    from steward.mcp_server import demo_reset_hours
    monkeypatch.delenv("STEWARD_DEMO_RESET_HOURS", raising=False)
    assert demo_reset_hours() == 0
    monkeypatch.setenv("STEWARD_DEMO_RESET_HOURS", "6")
    assert demo_reset_hours() == 6
    monkeypatch.setenv("STEWARD_DEMO_RESET_HOURS", "junk")
    assert demo_reset_hours() == 0


# MCP server over real HTTP ----------------------------------------------------

def _tool_results(agent) -> list[str]:
    out = []
    for msg in agent._agent.messages:
        for block in msg.get("content", []) or []:
            res = block.get("toolResult")
            if res:
                out.append(" ".join(c.get("text", "")
                                    for c in res.get("content", [])))
    return out


def _run(url, script, session_id=""):
    with StewardAgent(ScriptedModel(script), url,
                      session_id=session_id) as agent:
        agent.run("go")
        return _tool_results(agent)


def test_mcp_health_route(mcp_server_url):
    base = mcp_server_url.rsplit("/mcp", 1)[0]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    body = opener.open(base + "/health", timeout=5).read().decode()
    assert '"ok":true' in body.replace(" ", "")


def test_mcp_sessions_do_not_share_data(mcp_server_url):
    _run(mcp_server_url, [("prefs_set", {"key": "iso_pet", "value": "cat"})],
         NS_A)
    _run(mcp_server_url, [("prefs_set", {"key": "iso_pet", "value": "dog"})],
         NS_B)
    _run(mcp_server_url, [("note_save", {"title": "A-only",
                                          "body": "visitor A secret"})], NS_A)

    a = _run(mcp_server_url, [("prefs_get", {"key": "iso_pet"}),
                              ("note_search", {"query": "secret"})], NS_A)
    b = _run(mcp_server_url, [("prefs_get", {"key": "iso_pet"}),
                              ("note_search", {"query": "secret"})], NS_B)
    shared = _run(mcp_server_url, [("prefs_get", {"key": "iso_pet"})])

    assert "cat" in a[0] and "dog" not in a[0]
    assert "A-only" in a[1]
    assert "dog" in b[0] and "cat" not in b[0]
    assert "A-only" not in b[1]
    # No header: the classic shared store, untouched by either visitor.
    assert "cat" not in shared[0] and "dog" not in shared[0]


# Web app sessions --------------------------------------------------------------

class SessionAgent:
    def __init__(self, sid):
        self.sid = sid
        self.closed = False

    @property
    def tool_names(self):
        return ["clock_now"]

    def run(self, task):
        return {"text": f"{self.sid}:{task}", "tool_calls": []}


def _demo_app(made):
    @contextmanager
    def factory(sid):
        agent = SessionAgent(sid)
        made.append(agent)
        try:
            yield agent
        finally:
            agent.closed = True
    return create_app(agent_factory=factory, demo=True)


def test_cheap_health_endpoint():
    app = create_app(agent_factory=lambda: None)
    r = TestClient(app).get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_demo_gives_each_browser_its_own_session():
    made = []
    app = _demo_app(made)
    alice, bob = TestClient(app), TestClient(app)

    r1 = alice.post("/api/chat", json={"message": "hi"})
    assert r1.status_code == 200
    sid_a = r1.cookies.get(SESSION_COOKIE) or alice.cookies.get(SESSION_COOKIE)
    assert valid_namespace(sid_a)
    assert "httponly" in r1.headers["set-cookie"].lower()

    r2 = alice.post("/api/chat", json={"message": "again"})
    assert r2.json()["reply"] == f"{sid_a}:again"  # same agent reused

    r3 = bob.post("/api/chat", json={"message": "hi"})
    sid_b = bob.cookies.get(SESSION_COOKIE)
    assert sid_b and sid_b != sid_a
    assert r3.json()["reply"] == f"{sid_b}:hi"
    assert [a.sid for a in made] == [sid_a, sid_b]

    h = alice.get("/api/health").json()
    assert "reset_hours" in h["demo"]


def test_demo_rejects_forged_cookie():
    made = []
    client = TestClient(_demo_app(made))
    client.cookies.set(SESSION_COOKIE, "../../etc")
    r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 200
    assert valid_namespace(made[0].sid) and made[0].sid != "../../etc"


def test_demo_caps_sessions_and_message_size(monkeypatch):
    monkeypatch.setenv("STEWARD_DEMO_MAX_SESSIONS", "2")
    monkeypatch.setenv("STEWARD_DEMO_MAX_CHARS", "10")
    made = []
    app = _demo_app(made)
    for _ in range(3):
        assert TestClient(app).post(
            "/api/chat", json={"message": "hi"}).status_code == 200
    assert len(app.state.sessions) == 2
    assert made[0].closed is True  # oldest evicted and closed
    r = TestClient(app).post("/api/chat", json={"message": "x" * 11})
    assert r.status_code == 413


def test_non_demo_mode_is_unchanged():
    made = []

    @contextmanager
    def factory():
        agent = SessionAgent("")
        made.append(agent)
        yield agent
    app = create_app(agent_factory=factory, demo=False)
    a, b = TestClient(app), TestClient(app)
    ra = a.post("/api/chat", json={"message": "one"})
    b.post("/api/chat", json={"message": "two"})
    assert SESSION_COOKIE not in ra.cookies
    assert len(made) == 1  # one shared agent, as before
    assert "demo" not in a.get("/api/health").json()
