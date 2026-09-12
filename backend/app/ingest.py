"""Parse vault recipe files and upsert them into the Postgres index.

The vault markdown is always authoritative; this module only ever reads
from it and writes into the DB, never the other direction. Re-running
ingest on the same file is idempotent - it upserts by (owner_id, title).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import RecipeRecord
from .models import Recipe
from .vault import markdown_to_recipe

_COPIED_FIELDS = (
    "type",
    "tags",
    "servings",
    "prep_time",
    "cook_time",
    "total_time",
    "difficulty",
    "source",
    "created",
    "ai_filled",
    "ingredients",
    "steps",
    "notes",
)


def ingest_file(path: Path, session: Session, owner_id: int) -> RecipeRecord:
    recipe = markdown_to_recipe(Path(path).read_text())
    return _upsert(recipe, session, owner_id, vault_path=str(path))


def ingest_vault(vault_dir: Path, session: Session, owner_id: int) -> list[RecipeRecord]:
    recipes_dir = Path(vault_dir) / "Recipes"
    return [ingest_file(path, session, owner_id) for path in sorted(recipes_dir.glob("*.md"))]


def _upsert(recipe: Recipe, session: Session, owner_id: int, vault_path: str | None) -> RecipeRecord:
    existing = session.scalar(
        select(RecipeRecord).where(RecipeRecord.owner_id == owner_id, RecipeRecord.title == recipe.title)
    )
    record = existing or RecipeRecord(owner_id=owner_id, title=recipe.title)

    for field in _COPIED_FIELDS:
        setattr(record, field, getattr(recipe, field))
    record.vault_path = vault_path

    session.add(record)
    session.flush()
    return record
