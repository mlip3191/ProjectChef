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
    def __init__(self):
        self.saved_markdown = None

    def send(self, message: str) -> str:
        if "soup" in message.lower():
            self._pending_markdown = "---\ntitle: Test Soup\n---\n\n## Steps\n1. Simmer.\n"
        return f"echo: {message}"

    def pending_markdown(self) -> str | None:
        return getattr(self, "_pending_markdown", None)

    def save_edited(self, markdown_text: str) -> dict:
        self.saved_markdown = markdown_text
        return {"status": "saved", "path": "/fake/vault/Recipes/Test Soup.md", "title": "Test Soup"}


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
            ws.send_json({"type": "chat", "text": "hello"})
            reply = ws.receive_json()
            assert reply == {"type": "reply", "text": "echo: hello", "proposal_markdown": None}


def test_websocket_chat_includes_proposal_markdown(tmp_path, monkeypatch):
    main_module = _fresh_main_module(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "create_agent", lambda session, owner_id: StubAgent())

    with TestClient(main_module.app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "chat", "text": "here's my soup recipe"})
            reply = ws.receive_json()
            assert reply["type"] == "reply"
            assert "Test Soup" in reply["proposal_markdown"]


def test_websocket_save_edited(tmp_path, monkeypatch):
    main_module = _fresh_main_module(tmp_path, monkeypatch)
    stub = StubAgent()
    monkeypatch.setattr(main_module, "create_agent", lambda session, owner_id: stub)

    with TestClient(main_module.app) as client:
        with client.websocket_connect("/ws") as ws:
            edited = "---\ntitle: Test Soup\n---\n\n## Steps\n1. Simmer longer.\n"
            ws.send_json({"type": "save_edited", "markdown": edited})
            reply = ws.receive_json()
            assert reply == {
                "type": "saved",
                "path": "/fake/vault/Recipes/Test Soup.md",
                "title": "Test Soup",
            }
            assert stub.saved_markdown == edited


def test_websocket_bootstraps_default_user_once(tmp_path, monkeypatch):
    main_module = _fresh_main_module(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "create_agent", lambda session, owner_id: StubAgent())

    with TestClient(main_module.app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "chat", "text": "hi"})
            ws.receive_json()
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "chat", "text": "hi again"})
            ws.receive_json()

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


def test_list_recipes_endpoint(tmp_path, monkeypatch):
    main_module = _fresh_main_module(tmp_path, monkeypatch)

    with TestClient(main_module.app) as client:
        session = main_module.get_sessionmaker()()
        owner = main_module._get_or_create_default_user(session)
        session.add(
            main_module.RecipeRecord(
                owner_id=owner.id, title="Chili", tags=["meal/dinner"], ingredients=[], steps=[]
            )
        )
        session.commit()
        session.close()

        response = client.get("/api/recipes")

        assert response.status_code == 200
        assert response.json() == [{"title": "Chili", "tags": ["meal/dinner"]}]


def test_get_recipe_markdown_endpoint(tmp_path, monkeypatch):
    main_module = _fresh_main_module(tmp_path, monkeypatch)
    recipes_dir = tmp_path / "vault" / "Recipes"
    recipes_dir.mkdir(parents=True)
    (recipes_dir / "Chili.md").write_text("---\ntitle: Chili\n---\n\n## Steps\n1. Simmer.\n")

    client = TestClient(main_module.app)

    response = client.get("/api/recipes/Chili")
    assert response.status_code == 200
    assert "Simmer" in response.json()["markdown"]

    missing = client.get("/api/recipes/Nonexistent")
    assert missing.status_code == 404
