"""Access-control tests (Oct 2026 audit): API key, rate limit, bind guard."""
import asyncio
import json
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from steward import security
from steward.security import (ApiKeyASGIMiddleware, RateLimiter, check_bind,
                              extract_key, is_loopback, key_matches)
from steward.web import create_app


class FakeAgent:
    tool_names = ["clock_now"]

    def __init__(self):
        self.ran = []

    def run(self, task):
        self.ran.append(task)
        return {"text": "done", "tool_calls": []}


def _app(agent=None, **kw):
    agent = agent or FakeAgent()

    @contextmanager
    def cm():
        yield agent

    return create_app(agent_factory=cm, **kw), agent


# ------------------------------------------------------------- web app auth

def test_chat_requires_key_when_configured():
    app, agent = _app(api_key="s3cret")
    c = TestClient(app)
    r = c.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert c.post("/api/chat", json={"message": "hi"},
                  headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.post("/api/chat", json={"message": "hi"},
                  headers={"X-Steward-Key": "s3cret "}).status_code == 200
    assert c.post("/api/chat", json={"message": "hi"},
                  headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert agent.ran == ["hi", "hi"]  # unauthenticated calls never reached the agent


def test_tools_requires_key_when_configured():
    app, _ = _app(api_key="s3cret")
    c = TestClient(app)
    assert c.get("/api/tools").status_code == 401
    r = c.get("/api/tools", headers={"X-Steward-Key": "s3cret"})
    assert r.status_code == 200 and r.json() == {"tools": ["clock_now"]}


def test_no_key_configured_keeps_local_dev_open():
    app, _ = _app(api_key="")
    assert TestClient(app).post("/api/chat", json={"message": "hi"}).status_code == 200


def test_key_read_from_environment(monkeypatch):
    monkeypatch.setenv("STEWARD_API_KEY", "from-env")
    app, _ = _app()
    c = TestClient(app)
    assert c.post("/api/chat", json={"message": "hi"}).status_code == 401
    assert c.post("/api/chat", json={"message": "hi"},
                  headers={"X-Steward-Key": "from-env"}).status_code == 200
    assert c.get("/api/health").json()["auth_required"] is True


def test_health_endpoints_are_public_and_minimal():
    app, _ = _app(api_key="s3cret")
    c = TestClient(app)
    assert c.get("/health").json() == {"ok": True}
    body = c.get("/api/health").json()
    assert body["ok"] is True and body["auth_required"] is True
    assert "mcp_url" not in body


def test_chat_rate_limited_per_ip():
    app, agent = _app(api_key="", limiter=RateLimiter(2, 60.0))
    c = TestClient(app)
    codes = [c.post("/api/chat", json={"message": "hi"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = c.post("/api/chat", json={"message": "hi"})
    assert r.headers["retry-after"] == "60"
    assert len(agent.ran) == 2


def test_rate_limit_from_environment(monkeypatch):
    monkeypatch.setenv("STEWARD_RATE_LIMIT_PER_MIN", "1")
    app, _ = _app(api_key="")
    c = TestClient(app)
    assert c.post("/api/chat", json={"message": "a"}).status_code == 200
    assert c.post("/api/chat", json={"message": "b"}).status_code == 429


def test_message_length_cap():
    app, agent = _app(api_key="", max_message_chars=10)
    c = TestClient(app)
    assert c.post("/api/chat", json={"message": "x" * 10}).status_code == 200
    r = c.post("/api/chat", json={"message": "x" * 11})
    assert r.status_code == 413 and "max 10" in r.json()["detail"]
    assert agent.ran == ["x" * 10]


def test_message_length_cap_default(monkeypatch):
    monkeypatch.delenv("STEWARD_MAX_MESSAGE_CHARS", raising=False)
    app, _ = _app(api_key="")
    c = TestClient(app)
    assert c.post("/api/chat", json={"message": "x" * 2000}).status_code == 200
    assert c.post("/api/chat", json={"message": "x" * 2001}).status_code == 413


@pytest.mark.parametrize("payload", [{"message": 5}, {"message": None}, {}, {"message": ["a"]}])
def test_non_string_message_is_400(payload):
    app, _ = _app(api_key="")
    assert TestClient(app).post("/api/chat", json=payload).status_code == 400


def test_agent_errors_do_not_leak_details():
    class Boom(FakeAgent):
        def run(self, task):
            raise RuntimeError("secret-internal-url http://10.0.0.5")

    app, _ = _app(agent=Boom(), api_key="")
    r = TestClient(app).post("/api/chat", json={"message": "hi"})
    assert r.status_code == 502
    assert r.json()["detail"] == "agent run failed"


def test_tools_unavailable_does_not_leak_details():
    def factory():
        raise RuntimeError("connect to http://10.0.0.5:8899 refused")

    r = TestClient(create_app(agent_factory=factory, api_key="")).get("/api/tools")
    assert r.status_code == 503 and r.json()["detail"] == "agent unavailable"


def test_agent_turns_are_serialized():
    import threading
    import time

    active = []
    overlap = []

    class Slow(FakeAgent):
        def run(self, task):
            active.append(task)
            if len(active) > 1:
                overlap.append(task)
            time.sleep(0.05)
            active.remove(task)
            return {"text": task, "tool_calls": []}

    app, _ = _app(agent=Slow(), api_key="", limiter=RateLimiter(100, 60))
    c = TestClient(app)
    ts = [threading.Thread(target=c.post, args=("/api/chat",), kwargs={"json": {"message": str(i)}})
          for i in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert overlap == []


# --------------------------------------------------------------- helpers

def test_extract_key_variants():
    assert extract_key({"Authorization": "Bearer abc"}) == "abc"
    assert extract_key({"authorization": "bearer  abc "}) == "abc"
    assert extract_key({"X-Steward-Key": " k "}) == "k"
    assert extract_key({"Authorization": "Basic abc"}) == ""
    assert extract_key({}) == ""


def test_key_matches():
    assert key_matches("a", "a")
    assert not key_matches("a", "b")
    assert not key_matches("", "")
    assert not key_matches("a", "")
    assert not key_matches("", "a")


@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", True), ("::1", True), ("[::1]", True), ("localhost", True),
    ("127.0.0.2", True), ("0.0.0.0", False), ("::", False), ("10.0.0.5", False),
    ("example.com", False), ("", False),
])
def test_is_loopback(host, expected):
    assert is_loopback(host) is expected


def test_check_bind(monkeypatch):
    monkeypatch.delenv("STEWARD_ALLOW_OPEN", raising=False)
    check_bind("127.0.0.1", "", "svc")
    check_bind("0.0.0.0", "key", "svc")
    with pytest.raises(SystemExit) as exc:
        check_bind("0.0.0.0", "", "svc")
    assert "STEWARD_API_KEY" in str(exc.value) and "svc" in str(exc.value)
    monkeypatch.setenv("STEWARD_ALLOW_OPEN", "1")
    check_bind("0.0.0.0", "", "svc")
    monkeypatch.setenv("STEWARD_ALLOW_OPEN", "no")
    with pytest.raises(SystemExit):
        check_bind("0.0.0.0", "", "svc")


def test_web_main_refuses_public_bind_without_key(monkeypatch):
    import sys

    from steward import web

    monkeypatch.delenv("STEWARD_API_KEY", raising=False)
    monkeypatch.delenv("STEWARD_ALLOW_OPEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["steward.web", "--host", "0.0.0.0"])
    with pytest.raises(SystemExit):
        web.main()


def test_mcp_main_refuses_public_bind_without_key(monkeypatch):
    import sys

    from steward import mcp_server

    for var in ("STEWARD_API_KEY", "STEWARD_MCP_API_KEY", "STEWARD_ALLOW_OPEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sys, "argv", ["steward.mcp_server", "--host", "0.0.0.0"])
    with pytest.raises(SystemExit):
        mcp_server.main()


def test_key_env_resolution(monkeypatch):
    monkeypatch.setenv("STEWARD_API_KEY", " web ")
    monkeypatch.delenv("STEWARD_MCP_API_KEY", raising=False)
    assert security.web_api_key() == "web"
    assert security.mcp_api_key() == "web"
    monkeypatch.setenv("STEWARD_MCP_API_KEY", "mcp")
    assert security.mcp_api_key() == "mcp"


def test_rate_limiter_window_and_eviction():
    now = [0.0]
    rl = RateLimiter(2, 10.0, clock=lambda: now[0], max_keys=2)
    assert rl.allow("a") and rl.allow("a") and not rl.allow("a")
    now[0] = 9.9
    assert not rl.allow("a")
    now[0] = 10.0
    assert rl.allow("a")
    assert rl.allow("b")
    now[0] = 30.0
    assert rl.allow("c")  # evicts idle keys instead of growing unbounded
    assert len(rl._hits) <= 2
    assert RateLimiter(0).allow("x")


def test_rate_limiter_evicts_oldest_when_all_active():
    now = [0.0]
    rl = RateLimiter(5, 100.0, clock=lambda: now[0], max_keys=2)
    rl.allow("a")
    now[0] = 1.0
    rl.allow("b")
    now[0] = 2.0
    rl.allow("c")
    assert set(rl._hits) == {"b", "c"}


# ------------------------------------------------------- MCP ASGI middleware

def _call(mw, path="/mcp", headers=(), client=("1.2.3.4", 1), scope_type="http"):
    sent = []
    reached = []

    async def app(scope, receive, send):
        reached.append(scope["path"] if scope_type == "http" else scope_type)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw.app = app

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request"}

    scope = {"type": scope_type, "path": path, "client": client,
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers]}
    asyncio.run(mw(scope, receive, send))
    status = sent[0]["status"] if sent else None
    body = json.loads(sent[1]["body"]) if sent and status != 200 else None
    return status, body, reached, sent


def test_mcp_middleware_requires_bearer_key():
    mw = ApiKeyASGIMiddleware(None, key="k")
    status, body, reached, sent = _call(mw)
    assert status == 401 and body == {"detail": "missing or invalid API key"} and reached == []
    hdrs = dict(sent[0]["headers"])
    assert hdrs[b"www-authenticate"] == b"Bearer"
    assert hdrs[b"content-type"] == b"application/json"
    assert int(hdrs[b"content-length"]) == len(sent[1]["body"])
    assert _call(mw, headers=[("Authorization", "Bearer bad")])[0] == 401
    assert _call(mw, headers=[("Authorization", "Bearer k")])[0] == 200


def test_mcp_middleware_passes_lifespan_and_exempt_paths():
    mw = ApiKeyASGIMiddleware(None, key="k", exempt_paths=["/health"])
    assert _call(mw, path="/health")[0] == 200
    _, _, reached, _ = _call(mw, scope_type="lifespan")
    assert reached == ["lifespan"]


def test_mcp_middleware_rate_limit():
    mw = ApiKeyASGIMiddleware(None, key="", limiter=RateLimiter(1, 30.0))
    assert _call(mw)[0] == 200
    status, body, _, sent = _call(mw)
    assert status == 429 and body == {"detail": "rate limit exceeded"}
    assert dict(sent[0]["headers"])[b"retry-after"] == b"30"
    assert _call(mw, client=("5.6.7.8", 1))[0] == 200  # per IP
    assert _call(mw, client=None)[0] == 200


def test_mcp_server_endpoint_rejects_missing_key(mcp_server_url_with_key):
    import httpx

    url, key = mcp_server_url_with_key
    r = httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                   headers={"Accept": "application/json, text/event-stream"})
    assert r.status_code == 401


def test_mcp_server_endpoint_accepts_key(mcp_server_url_with_key):
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    import httpx

    url, key = mcp_server_url_with_key

    async def go():
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}) as http:
            async with streamable_http_client(url, http_client=http) as (r, w, *_):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return [t.name for t in (await s.list_tools()).tools]

    assert "clock_now" in asyncio.run(go())


def test_agent_sends_mcp_key(monkeypatch):
    from steward import agent as agent_mod

    seen = {}

    class FakeClient:
        def __init__(self, url=None, headers=None):
            seen["url"], seen["headers"] = url, headers

        def stop(self, *a):
            pass

    class FakeAgentCls:
        def __init__(self, **kw):
            raise RuntimeError("stop here")

    monkeypatch.setattr(agent_mod, "MCPClient", FakeClient)
    monkeypatch.setattr(agent_mod, "Agent", FakeAgentCls)
    monkeypatch.setenv("STEWARD_MCP_API_KEY", "mk")
    with pytest.raises(RuntimeError):
        agent_mod.StewardAgent(model=object(), mcp_url="http://x/mcp").__enter__()
    assert seen == {"url": "http://x/mcp", "headers": {"Authorization": "Bearer mk"}}
    monkeypatch.delenv("STEWARD_MCP_API_KEY")
    monkeypatch.delenv("STEWARD_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        agent_mod.StewardAgent(model=object(), mcp_url="http://x/mcp").__enter__()
    assert seen["headers"] is None


def test_key_matches_returns_strict_bools():
    assert key_matches("a", "a") is True
    assert key_matches("a", "b") is False
    assert key_matches("", "") is False
    assert key_matches("a", "") is False
    assert key_matches("", "a") is False


def test_default_chat_rate_limit_is_20_per_minute(monkeypatch):
    monkeypatch.delenv("STEWARD_RATE_LIMIT_PER_MIN", raising=False)
    app, _ = _app(api_key="")
    c = TestClient(app)
    codes = [c.post("/api/chat", json={"message": "hi"}).status_code for _ in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429
    assert c.post("/api/chat", json={"message": "hi"}).headers["retry-after"] == "60"


def test_rate_limiter_boundary_eviction():
    now = [0.0]
    rl = RateLimiter(5, 10.0, clock=lambda: now[0], max_keys=1)
    rl.allow("a")
    now[0] = 10.0  # exactly one window later: "a" is idle and evictable
    assert rl.allow("b")
    assert set(rl._hits) == {"b"}


@pytest.mark.parametrize("module,argv_host,env_key", [
    ("web", "127.0.0.1", "STEWARD_API_KEY"),
    ("mcp_server", "0.0.0.0", "STEWARD_MCP_API_KEY"),
])
def test_main_passes_host_and_port_to_uvicorn(monkeypatch, module, argv_host, env_key):
    import importlib
    import sys

    import uvicorn

    mod = importlib.import_module(f"steward.{module}")
    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port, **kw: seen.update(host=host, port=port, app=app))
    monkeypatch.setenv(env_key, "k")
    monkeypatch.setattr(sys, "argv", [module, "--host", argv_host, "--port", "9123"])
    mod.main()
    assert seen["host"] == argv_host and seen["port"] == 9123
    if module == "mcp_server":
        assert isinstance(seen["app"], ApiKeyASGIMiddleware) and seen["app"].key == "k"
