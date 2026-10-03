"""Web layer tests: FastAPI TestClient with injected agent doubles."""
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from steward.models import ConfigurationError
from steward.web import create_app


class FakeAgent:
    def __init__(self, reply="ok", calls=None):
        self._reply = reply
        self._calls = calls or []
        self.ran = []

    @property
    def tool_names(self):
        return ["clock_now", "reminder_add"]

    def run(self, task):
        self.ran.append(task)
        return {"text": self._reply, "tool_calls": self._calls}


@contextmanager
def _agent_cm(agent):
    yield agent


def _app_with(agent=None, broken=False):
    def factory():
        if broken:
            raise ConfigurationError("no model configured")
        return _agent_cm(agent or FakeAgent())
    return create_app(agent_factory=factory)


def test_health():
    app = _app_with()
    r = TestClient(app).get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_index_serves_ui():
    app = _app_with()
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert "Steward" in r.text


def test_chat_happy_path():
    agent = FakeAgent(reply="Reminder set.",
                      calls=[{"name": "reminder_add",
                              "input": {"text": "x"}}])
    app = _app_with(agent)
    r = TestClient(app).post("/api/chat", json={"message": "remind me"})
    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == "Reminder set."
    assert body["tool_calls"][0]["name"] == "reminder_add"
    assert agent.ran == ["remind me"]


def test_chat_rejects_empty_message():
    app = _app_with()
    r = TestClient(app).post("/api/chat", json={"message": "   "})
    assert r.status_code == 400


def test_chat_503_when_no_model():
    app = _app_with(broken=True)
    r = TestClient(app).post("/api/chat", json={"message": "hi"})
    assert r.status_code == 503
    assert "model configured" in r.json()["detail"].lower()


def test_tools_endpoint():
    app = _app_with()
    r = TestClient(app).get("/api/tools")
    assert r.status_code == 200
    assert r.json()["tools"] == ["clock_now", "reminder_add"]
