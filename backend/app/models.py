"""Pydantic schema for a recipe.

This is the single definition of what a recipe "is" in ProjectChef. It maps
1:1 onto the vault markdown format documented in vault-config/README.md:
frontmatter fields become the model fields below, and the markdown body's
"## Steps" / "## Notes" sections become `steps` / `notes`.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class Recipe(BaseModel):
    title: str
    type: str = "recipe"
    tags: list[str] = Field(default_factory=list)

    servings: int | None = None
    prep_time: str | None = None
    cook_time: str | None = None
    total_time: str | None = None
    difficulty: str | None = None

    source: str | None = None
    created: date | None = None

    # Fields Claude filled in rather than the user providing directly.
    # Always populated by the agent, never guessed silently.
    ai_filled: list[str] = Field(default_factory=list)

    ingredients: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    notes: str | None = None
