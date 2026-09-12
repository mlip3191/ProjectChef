"""Tests for ingest.py using an isolated SQLite engine.

SQLite (not Postgres) is used here purely so these tests run without any
external service - the JSON columns and upsert logic behave the same on
both. Real Postgres integration is exercised separately (see the
scripts/dev_db smoke test), since it's the actual deployment target.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, RecipeRecord, UserRecord
from app.ingest import ingest_file, ingest_vault
from app.models import Recipe
from app.vault import recipe_to_markdown


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    user = UserRecord(username="mike", password_hash="x")
    session.add(user)
    session.flush()
    return session, user.id


def test_ingest_file_creates_record(tmp_path):
    session, owner_id = _session(tmp_path)
    recipe = Recipe(title="Test Soup", ingredients=["onion"], steps=["simmer"])
    path = tmp_path / "Test Soup.md"
    path.write_text(recipe_to_markdown(recipe))

    record = ingest_file(path, session, owner_id)
    session.commit()

    assert record.id is not None
    assert record.title == "Test Soup"
    assert record.vault_path == str(path)
    assert record.ingredients == ["onion"]


def test_ingest_file_upserts_on_rerun(tmp_path):
    session, owner_id = _session(tmp_path)
    recipe = Recipe(title="Test Soup", ingredients=["onion"], steps=["simmer"])
    path = tmp_path / "Test Soup.md"
    path.write_text(recipe_to_markdown(recipe))
    ingest_file(path, session, owner_id)
    session.commit()

    recipe.ingredients.append("garlic")
    path.write_text(recipe_to_markdown(recipe))
    ingest_file(path, session, owner_id)
    session.commit()

    matches = session.query(RecipeRecord).filter_by(owner_id=owner_id, title="Test Soup").all()
    assert len(matches) == 1
    assert "garlic" in matches[0].ingredients


def test_ingest_vault_bulk(tmp_path):
    session, owner_id = _session(tmp_path)
    recipes_dir = tmp_path / "vault" / "Recipes"
    recipes_dir.mkdir(parents=True)
    for title in ("Soup", "Salad"):
        recipe = Recipe(title=title, ingredients=["x"], steps=["y"])
        (recipes_dir / f"{title}.md").write_text(recipe_to_markdown(recipe))

    records = ingest_vault(tmp_path / "vault", session, owner_id)
    session.commit()

    assert {r.title for r in records} == {"Soup", "Salad"}
