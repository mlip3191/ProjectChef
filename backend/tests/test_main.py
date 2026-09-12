"""Tests for the FastAPI chat app.

`create_agent` is monkeypatched to a stub so these run without a real
Anthropic API key or network access - the point here is verifying the
WebSocket plumbing and default-user bootstrap, not the agent's chat logic
(covered separately in test_agent.py).
"""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def _fresh_main_module(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))

    from app import db as db_module

    importlib.reload(db_module)
    from app import main as main_module

    importlib.reload(main_module)
    return main_module


class StubAgent:
    def send(self, message: str) -> str:
        return f"echo: {message}"


def test_health_endpoint(tmp_path, monkeypatch):
    main_module = _fresh_main_module(tmp_path, monkeypatch)
    client = TestClient(main_module.app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_websocket_chat_roundtrip(tmp_path, monkeypatch):
    main_module = _fresh_main_module(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "create_agent", lambda session, owner_id: StubAgent())

    with TestClient(main_module.app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text("hello")
            assert ws.receive_text() == "echo: hello"


def test_websocket_bootstraps_default_user_once(tmp_path, monkeypatch):
    main_module = _fresh_main_module(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "create_agent", lambda session, owner_id: StubAgent())

    with TestClient(main_module.app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text("hi")
            ws.receive_text()
        with client.websocket_connect("/ws") as ws:
            ws.send_text("hi again")
            ws.receive_text()

        session = main_module.get_sessionmaker()()
        users = session.query(main_module.UserRecord).filter_by(username="owner").all()
        session.close()
        assert len(users) == 1


def test_static_index_served(tmp_path, monkeypatch):
    main_module = _fresh_main_module(tmp_path, monkeypatch)
    client = TestClient(main_module.app)

    response = client.get("/")

    assert response.status_code == 200
    assert "ProjectChef" in response.text
