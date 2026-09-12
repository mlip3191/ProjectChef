"""Core Claude agent: parses recipes, proposes fills, saves on approval.

Conversation flow:
  1. The user gives Claude a recipe (complete or partial). Claude calls
     `propose_recipe` with its best structured version, filling in
     reasonable gaps (e.g. cook_time, difficulty) and listing every field
     it filled in under `ai_filled`. This does NOT write anything to disk.
  2. Claude shows the user the proposal and asks if it looks good, then
     stops and waits for a reply.
  3. Only if the user approves in a *later* message does Claude call
     `save_recipe`, which writes the recipe into the vault.

The same-turn-vs-later-turn distinction is enforced in code (see
`_save_recipe`'s turn check), not just by prompting - a proposal can only
be saved once at least one further user message has been sent.
"""

from __future__ import annotations

import json
from pathlib import Path

import anthropic
from sqlalchemy.orm import Session

from .ingest import ingest_file
from .models import Recipe
from .vault import markdown_to_recipe, recipe_to_markdown

CHAT_MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """\
You are ProjectChef, a personal cooking assistant. You help the user build up \
a vault of their own recipes.

Rules:
- When the user gives you a recipe (complete or partial), call `propose_recipe` \
with your best structured version of it. Fill in reasonable missing metadata \
yourself (e.g. cook_time, difficulty, total_time) but list every field you \
filled in under `ai_filled`. Do not invent or change ingredients or steps the \
user didn't give you - only fill in supporting details that don't change what \
the dish actually is.
- Never call `save_recipe` in the same turn as `propose_recipe`. After \
proposing, summarize what you filled in and ask the user if it looks good, \
then wait for their reply.
- Only call `save_recipe` after the user has explicitly approved the proposal \
in a later message (e.g. "looks good", "yes", "save it").
- Use `search_recipes` or `list_recipes` to help the user find recipes already \
saved in their vault.
"""

TOOLS = [
    {
        "name": "propose_recipe",
        "description": (
            "Present a structured recipe to the user for approval. "
            "Does not save anything to the vault."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "servings": {"type": "integer"},
                "prep_time": {"type": "string"},
                "cook_time": {"type": "string"},
                "total_time": {"type": "string"},
                "difficulty": {"type": "string"},
                "source": {"type": "string"},
                "ingredients": {"type": "array", "items": {"type": "string"}},
                "steps": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string"},
                "ai_filled": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Names of fields you filled in rather than the user providing.",
                },
            },
            "required": ["title", "ingredients", "steps"],
        },
    },
    {
        "name": "save_recipe",
        "description": (
            "Save the most recently proposed recipe to the vault. "
            "Only call this after the user has approved it in a later message."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_recipes",
        "description": "Search saved recipes by a title, tag, or ingredient substring.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "list_recipes",
        "description": "List the titles of all saved recipes.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


class CookingAgent:
    def __init__(
        self,
        vault_dir: Path | str,
        client: anthropic.Anthropic | None = None,
        db_session: Session | None = None,
        owner_id: int | None = None,
    ):
        self.vault_dir = Path(vault_dir)
        (self.vault_dir / "Recipes").mkdir(parents=True, exist_ok=True)
        self.client = client or anthropic.Anthropic()
        self.history: list[dict] = []

        # Both must be set for save_recipe to also ingest into the DB index.
        # Left unset, the agent still works vault-only (e.g. in tests).
        self.db_session = db_session
        self.owner_id = owner_id

        self._pending: Recipe | None = None
        self._pending_turn: int = -1
        self._turn: int = 0

    def send(self, user_message: str) -> str:
        """Send a user message, running any tool calls, and return Claude's reply text."""
        self._turn += 1
        self.history.append({"role": "user", "content": user_message})

        while True:
            response = self.client.messages.create(
                model=CHAT_MODEL,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=self.history,
            )
            self.history.append({"role": "assistant", "content": response.content})

            tool_uses = [block for block in response.content if block.type == "tool_use"]
            if not tool_uses:
                return "".join(block.text for block in response.content if block.type == "text")

            tool_results = []
            for block in tool_uses:
                result = self._run_tool(block.name, block.input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    }
                )
            self.history.append({"role": "user", "content": tool_results})

    def _run_tool(self, name: str, input_: dict) -> dict:
        if name == "propose_recipe":
            return self._propose_recipe(input_)
        if name == "save_recipe":
            return self._save_recipe()
        if name == "search_recipes":
            return self._search_recipes(input_.get("query", ""))
        if name == "list_recipes":
            return self._list_recipes()
        return {"status": "error", "message": f"unknown tool {name!r}"}

    def pending_markdown(self) -> str | None:
        """Markdown preview of the currently proposed (not-yet-saved) recipe, if any."""
        return recipe_to_markdown(self._pending) if self._pending is not None else None

    def save_edited(self, markdown_text: str) -> dict:
        """Save recipe markdown the user edited directly in the editor panel.

        This is a separate path from `save_recipe` (the LLM tool): clicking
        "commit to archive" on hand-edited text *is* the explicit human
        approval, so there's no same-turn guard to apply here - unlike
        `_save_recipe`, this isn't Claude trying to save its own proposal.
        """
        recipe = markdown_to_recipe(markdown_text)
        result = self._write_and_ingest(recipe)
        if recipe is self._pending or (self._pending and recipe.title == self._pending.title):
            self._pending = None
            self._pending_turn = -1
        return result

    def _propose_recipe(self, input_: dict) -> dict:
        recipe = Recipe(**input_)
        self._pending = recipe
        self._pending_turn = self._turn
        return {"status": "proposed", "recipe": recipe.model_dump(mode="json")}

    def _save_recipe(self) -> dict:
        if self._pending is None:
            return {"status": "error", "message": "No proposed recipe to save."}
        if self._pending_turn == self._turn:
            return {
                "status": "error",
                "message": (
                    "Cannot save a recipe in the same turn it was proposed in. "
                    "Ask the user to confirm first, then wait for their next message."
                ),
            }

        result = self._write_and_ingest(self._pending)
        self._pending = None
        self._pending_turn = -1
        return result

    def _write_and_ingest(self, recipe: Recipe) -> dict:
        path = self.vault_dir / "Recipes" / f"{recipe.title}.md"
        path.write_text(recipe_to_markdown(recipe))

        if self.db_session is not None and self.owner_id is not None:
            ingest_file(path, self.db_session, self.owner_id)
            self.db_session.commit()

        return {"status": "saved", "path": str(path)}

    def _search_recipes(self, query: str) -> dict:
        query = query.lower()
        matches = [
            path.stem
            for path in sorted((self.vault_dir / "Recipes").glob("*.md"))
            if query in path.read_text().lower()
        ]
        return {"matches": matches}

    def _list_recipes(self) -> dict:
        titles = [path.stem for path in sorted((self.vault_dir / "Recipes").glob("*.md"))]
        return {"recipes": titles}
