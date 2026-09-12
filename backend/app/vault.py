"""Round-trip a Recipe to/from the Obsidian vault markdown format.

File shape (see vault-config/README.md for the full spec):

    ---
    <frontmatter fields matching models.Recipe, minus steps/notes>
    ---

    ## Steps
    1. First step.
    2. Second step.

    ## Notes
    Free text notes.

`recipe_to_markdown` / `markdown_to_recipe` must be exact inverses of each
other for every field on Recipe - this is enforced by
tests/test_vault.py::test_round_trip.
"""

from __future__ import annotations

import re

import frontmatter

from .models import Recipe

_STEPS_HEADING = "## Steps"
_NOTES_HEADING = "## Notes"

# One frontmatter field per Recipe field except `steps` and `notes`, which
# live in the markdown body instead.
_FRONTMATTER_FIELDS = [
    "title",
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
]


def recipe_to_markdown(recipe: Recipe) -> str:
    data = recipe.model_dump(
        include=set(_FRONTMATTER_FIELDS),
        exclude_none=True,
        mode="json",
    )
    post = frontmatter.Post("", **data)

    body_parts = [_STEPS_HEADING]
    body_parts += [f"{i}. {step}" for i, step in enumerate(recipe.steps, start=1)]

    if recipe.notes:
        body_parts += ["", _NOTES_HEADING, recipe.notes]

    post.content = "\n".join(body_parts) + "\n"
    return frontmatter.dumps(post) + "\n"


def markdown_to_recipe(text: str) -> Recipe:
    post = frontmatter.loads(text)
    data = dict(post.metadata)

    steps, notes = _parse_body(post.content)
    data["steps"] = steps
    if notes:
        data["notes"] = notes

    return Recipe(**data)


def _parse_body(body: str) -> tuple[list[str], str | None]:
    steps_match = re.search(
        rf"{re.escape(_STEPS_HEADING)}\n(.*?)(?:\n{re.escape(_NOTES_HEADING)}\n(.*))?\Z",
        body.strip(),
        re.DOTALL,
    )
    if not steps_match:
        return [], None

    steps_block, notes_block = steps_match.group(1), steps_match.group(2)

    steps = []
    for line in steps_block.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        steps.append(re.sub(r"^\d+\.\s*", "", line))

    notes = notes_block.strip() if notes_block else None
    return steps, notes
