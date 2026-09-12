"""Tests for the propose/save turn guard in agent.CookingAgent.

Uses a fake Anthropic client so these run without a real API key or network
access - they check the *code-enforced* rule that save_recipe can only
succeed after a later user turn, not just the system-prompt wording.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent import CookingAgent
from app.db import Base, UserRecord, RecipeRecord
from app.vault import recipe_to_markdown
from app.models import Recipe

SAMPLE_RECIPE_INPUT = {
    "title": "Test Soup",
    "ingredients": ["1 onion", "2 cups broth"],
    "steps": ["Chop onion.", "Simmer in broth."],
    "ai_filled": [],
}


@dataclass
class FakeBlock:
    type: str
    text: str | None = None
    name: str | None = None
    input: dict | None = None
    id: str | None = None


@dataclass
class FakeResponse:
    content: list[FakeBlock]


class FakeMessages:
    def __init__(self, responses: list[FakeResponse]):
        self._responses = list(responses)

    def create(self, **kwargs: Any) -> FakeResponse:
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[FakeResponse]):
        self.messages = FakeMessages(responses)


def _tool_use(name: str, input_: dict, tool_id: str = "t1") -> FakeResponse:
    return FakeResponse([FakeBlock(type="tool_use", name=name, input=input_, id=tool_id)])


def _text(message: str) -> FakeResponse:
    return FakeResponse([FakeBlock(type="text", text=message)])


def test_save_in_same_turn_is_rejected(tmp_path):
    client = FakeClient(
        [
            _tool_use("propose_recipe", SAMPLE_RECIPE_INPUT),
            _tool_use("save_recipe", {}, tool_id="t2"),
            _text("Something went wrong saving - let me know if you'd like to try again."),
        ]
    )
    agent = CookingAgent(vault_dir=tmp_path, client=client)

    agent.send("Here's my soup recipe...")

    assert agent._pending is not None
    assert not (tmp_path / "Recipes" / "Test Soup.md").exists()


def test_save_after_later_turn_succeeds(tmp_path):
    client = FakeClient(
        [
            _tool_use("propose_recipe", SAMPLE_RECIPE_INPUT),
            _text("Here's what I've got - does this look right?"),
            _tool_use("save_recipe", {}, tool_id="t2"),
            _text("Saved!"),
        ]
    )
    agent = CookingAgent(vault_dir=tmp_path, client=client)

    agent.send("Here's my soup recipe...")
    reply = agent.send("Looks good, save it.")

    assert reply == "Saved!"
    assert agent._pending is None
    assert (tmp_path / "Recipes" / "Test Soup.md").exists()


def test_save_ingests_into_db_when_configured(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    user = UserRecord(username="mike", password_hash="x")
    session.add(user)
    session.flush()

    client = FakeClient(
        [
            _tool_use("propose_recipe", SAMPLE_RECIPE_INPUT),
            _text("Does this look right?"),
            _tool_use("save_recipe", {}, tool_id="t2"),
            _text("Saved!"),
        ]
    )
    agent = CookingAgent(vault_dir=tmp_path, client=client, db_session=session, owner_id=user.id)

    agent.send("Here's my soup recipe...")
    agent.send("Looks good, save it.")

    record = session.query(RecipeRecord).filter_by(owner_id=user.id, title="Test Soup").one()
    assert record.vault_path == str(tmp_path / "Recipes" / "Test Soup.md")


def test_pending_markdown_reflects_current_proposal(tmp_path):
    client = FakeClient(
        [_tool_use("propose_recipe", SAMPLE_RECIPE_INPUT), _text("Does this look right?")]
    )
    agent = CookingAgent(vault_dir=tmp_path, client=client)

    assert agent.pending_markdown() is None
    agent.send("Here's my soup recipe...")
    assert "Test Soup" in agent.pending_markdown()


def test_save_edited_writes_hand_edited_markdown_and_clears_pending(tmp_path):
    client = FakeClient(
        [_tool_use("propose_recipe", SAMPLE_RECIPE_INPUT), _text("Does this look right?")]
    )
    agent = CookingAgent(vault_dir=tmp_path, client=client)
    agent.send("Here's my soup recipe...")

    edited = recipe_to_markdown(Recipe(title="Test Soup", ingredients=["1 onion", "3 cups broth"], steps=["Chop.", "Simmer longer."]))
    result = agent.save_edited(edited)

    assert result["status"] == "saved"
    saved_path = tmp_path / "Recipes" / "Test Soup.md"
    assert saved_path.exists()
    assert "3 cups broth" in saved_path.read_text()
    assert agent._pending is None


def test_save_edited_works_without_a_prior_proposal(tmp_path):
    client = FakeClient([])
    agent = CookingAgent(vault_dir=tmp_path, client=client)

    edited = recipe_to_markdown(Recipe(title="Manual Entry", ingredients=["x"], steps=["y"]))
    result = agent.save_edited(edited)

    assert result["status"] == "saved"
    assert (tmp_path / "Recipes" / "Manual Entry.md").exists()


def test_list_and_search_recipes(tmp_path):
    (tmp_path / "Recipes").mkdir(parents=True)
    (tmp_path / "Recipes" / "Test Soup.md").write_text("onion broth")

    client = FakeClient([_text("n/a")])
    agent = CookingAgent(vault_dir=tmp_path, client=client)

    assert agent._list_recipes() == {"recipes": ["Test Soup"]}
    assert agent._search_recipes("onion") == {"matches": ["Test Soup"]}
    assert agent._search_recipes("garlic") == {"matches": []}
