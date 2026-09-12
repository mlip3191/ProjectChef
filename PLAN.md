# ProjectChef — Personal Cooking Assistant

## Context

The user wants a personal, chat-based cooking assistant that: (1) learns recipes fed to it, (2) uses Claude to intelligently fill in gaps (missing quantities, times, etc.) but only writes anything after explicit human approval, and (3) feeds the approved recipe into a web application/database for search and future features (meal planning, "what can I make with X," etc.). The user has Python, Claude API, Claude Code, and Obsidian experience, wants this inexpensive, robust, and secure, and will self-host it rather than pay for cloud hosting.

Through clarifying questions, the following decisions were made:

- **Chat surfaces:** a custom web chat app, and a personal Discord bot. No chat-via-GitHub.
- **Recipe source of truth:** the user's existing **Obsidian vault** (markdown + YAML frontmatter), not the database. The database is a rebuildable index derived from the vault.
- **Vault versioning:** the vault is its own **git repo**, independent of the app's code repo, pushed to a **private GitHub repo purely as backup/history** (no GitHub chat/Action integration for now).
- **Ingestion trigger:** explicit — approving a recipe in chat writes the vault file, commits+pushes it, and ingests it into the DB in one atomic step (no background file-watcher).
- **Multi-user:** only the user is vault-backed. The schema is built to support other users from day one, but their recipes are DB-only (no vault requirement for them).
- **Infrastructure:** Docker Compose on a **separate self-hosted box** on the user's home network (not this Mac). Obsidian itself keeps running on the Mac.
- **Cross-machine vault sync:** since the vault is already git-backed to GitHub, the box holds a working clone that the app writes/commits/pushes to; the Mac's Obsidian vault stays in sync via the community **Obsidian Git** plugin (pull on open / on a schedule) — no extra sync software needed.
- **Remote access:** reachable from anywhere via a **Cloudflare Tunnel** (free, no port-forwarding, no exposed ports), optionally gated further by Cloudflare Access.
- **Cost target:** ~$0/month infra (self-hosted + free-tier Cloudflare + free GitHub private repo); the only real recurring cost is Claude API usage (variable, controllable) and an optional ~$10-15/yr custom domain.

## Recommended Tech Stack

**Backend (core, shared by web + Discord):**
- Python 3.12, **FastAPI** for the HTTP/WebSocket API — matches the user's existing Python experience.
- **Anthropic Python SDK**, using tool calling (function calling) so Claude can call structured tools like `propose_recipe`, `save_recipe`, `search_recipes` rather than parsing freeform text — this is what makes "fill in the blanks, then ask before saving" reliable.
- Use **Claude Haiku** for cheap structured extraction/ingestion parsing, **Claude Sonnet** for the actual conversational chat — controls API spend.
- **Pydantic** models for the `Recipe` schema (title, servings, times, ingredients, steps, tags, source, and an `ai_filled: [...]` field recording exactly which fields Claude guessed, so the user can always see what was inferred vs. what they provided).
- `python-frontmatter` (or a small hand-rolled YAML+markdown renderer) to convert a `Recipe` object to/from an Obsidian-style `.md` file with YAML frontmatter.
- `GitPython` (or plain `subprocess` calls to `git`) to commit+push vault changes as part of the save step.

**Chat interfaces:**
- **Web:** a small React+Vite single-page chat UI, built and served as static files by the same FastAPI app (keeps deployment to one container/image). Streams responses over WebSocket/SSE.
- **Discord:** `discord.py` bot that calls the *same* core agent module as the web app (no duplicated logic) — run as its own asyncio task or a second lightweight container in the compose stack.

**Storage:**
- **Obsidian vault:** markdown files with YAML frontmatter, its own git repo, remote = private GitHub repo (backup/history only).
- **Postgres** (not SQLite) for the app database — chosen because "multi-user eventually" is a stated goal and Postgres handles that, plus full-text search (`tsvector`/`pg_trgm`) for recipe search, far better than SQLite while still being free to self-host.
- DB schema includes `users` and `recipes` (with `owner_id`, nullable `vault_path` — populated only for the user's own vault-backed recipes) from the start, so adding real multi-user auth later doesn't require a schema migration of core tables.

**Infrastructure (Docker Compose on the separate box):**
- `app` — FastAPI backend + built frontend static files.
- `discord-bot` — the Discord bot process (or merged into `app` as a background task if the user prefers one fewer container; two containers is more crash-isolated).
- `db` — Postgres.
- `cloudflared` — Cloudflare Tunnel container, so the box needs **no open inbound ports** and gets free TLS.
- A bind-mounted **vault working directory** (a git clone of the GitHub backup repo) mounted into `app`, so the same code path that writes the file also `git commit`+`git push`es it.
- `.env` (never committed) for `ANTHROPIC_API_KEY`, `DISCORD_BOT_TOKEN`, `POSTGRES_PASSWORD`, `JWT_SECRET`, and a fine-grained, repo-scoped `GITHUB_TOKEN` used only to push to the backup repo.

**Auth/security (built for multi-user from day one, but only the owner uses it now):**
- `argon2` password hashing (via `passlib` or `argon2-cffi`), JWT session tokens in HTTPS-only cookies.
- Cloudflare Tunnel removes the need to expose any port; optionally add Cloudflare Access in front for a second, zero-maintenance auth layer (email OTP/Google login) before requests even reach the app.
- Rate limiting on chat endpoints (`slowapi`) to cap runaway Claude API spend from bugs, retries, or (later) other users.
- Pydantic validation on all inputs; sanitize any user text before it's interpolated into YAML frontmatter (avoid frontmatter/YAML injection when writing vault files).
- Non-root users in Dockerfiles; Postgres not exposed outside the Docker network; secrets via `.env`/Docker secrets, never logged, never sent to the frontend (all Claude calls happen server-side only).
- Nightly Postgres dump to a local backups folder on the box — the DB is rebuildable from the vault anyway, but a dump avoids re-ingesting everything after a crash.

## Vault Schema & Tag Taxonomy

**Directory layout inside the vault repo:**
```
Recipes/
  Chicken Tikka Masala.md
  Weeknight Fried Rice.md
  ...
Templates/
  Recipe Template.md        # Obsidian core Templates / Templater snippet for new recipes
Tags.md                     # human-readable doc listing the tag taxonomy below
```

**Frontmatter schema (one file per recipe):**
```yaml
---
title: Chicken Tikka Masala
type: recipe
tags:
  - cuisine/indian
  - meal/dinner
  - diet/gluten-free
  - method/stovetop
servings: 4
prep_time: 20m
cook_time: 35m
total_time: 55m
difficulty: medium
source: "family recipe, transcribed 2026-09-11"
created: 2026-09-11
ai_filled: [cook_time, difficulty]
ingredients:
  - 500g chicken thigh, cubed
  - 200g plain yogurt
  - 2 tbsp garam masala
  - 400g crushed tomatoes
  - ...
---

## Steps
1. Marinate chicken in yogurt + spices, 2+ hours (overnight is better).
2. Sear chicken in batches, set aside.
3. Build sauce: onion, garlic, ginger, tomatoes, simmer 15 min.
4. Return chicken to sauce, simmer 10 min, finish with cream.

## Notes
Freezes well. Great with basmati rice or naan.
```

**Why hierarchical tags (`cuisine/indian`, not just `indian`):** Obsidian natively understands `/` in tags as nesting, so your tag pane shows a collapsible tree (`cuisine` → `indian`, `italian`, `mexican`, ...) instead of one flat alphabetical soup. It also means a Dataview/search query can match an entire branch (`tag:#cuisine`) or one leaf (`tag:#cuisine/indian`).

**Starter tag taxonomy** (documented in `Tags.md`, extend freely over time):
- `cuisine/…` — indian, italian, mexican, american, thai, ...
- `meal/…` — breakfast, lunch, dinner, dessert, snack
- `diet/…` — vegetarian, vegan, gluten-free, dairy-free
- `method/…` — stovetop, oven, grill, slow-cooker, no-cook
- `season/…` — summer, winter, holiday

**How this maps to code:** the `ingredients`/`tags`/times fields above are exactly the fields on the `Recipe` Pydantic model in `models.py`; `vault.py` is what serializes/parses this exact YAML+markdown shape. Claude's `propose_recipe` tool call fills in any of these fields it can reasonably infer (e.g. `difficulty`, `total_time`) and always lists them in `ai_filled` so you can see what was guessed versus what you provided, before anything is written to a file.

## Implementation Plan (initial repo layout)

Build this out in `~/ProjectChef` (already git-initialized, pushed to `github.com/mlip3191/ProjectChef`):

```
ProjectChef/
  backend/
    app/
      main.py              # FastAPI app: HTTP + WebSocket chat routes
      agent.py             # Core Claude agent: system prompt + tool definitions
                            #   (propose_recipe, save_recipe, search_recipes, list_recipes)
      models.py            # Pydantic Recipe schema (+ users/recipes ORM models)
      vault.py             # Recipe <-> markdown/frontmatter conversion, git commit+push
      ingest.py            # Parses a vault .md file into Recipe, upserts into Postgres
      db.py                # SQLAlchemy/Postgres session setup
      auth.py              # argon2 hashing, JWT issuing/verification
    discord_bot/
      bot.py                # discord.py client, forwards messages to agent.py
    tests/
  frontend/
    (Vite + React chat UI, built to backend/app/static for single-image deploy)
  vault-config/
    README.md              # documents the expected vault git-repo layout, e.g. `Recipes/*.md`
                            #   with the frontmatter schema, for the user's Obsidian vault
  docker-compose.yml         # app, discord-bot, db, cloudflared services
  Dockerfile                # backend image (multi-stage: build frontend, then Python runtime)
  .env.example               # documents required secrets without real values
  README.md                  # updated with setup instructions
```

Suggested build order:
1. `models.py` (Recipe schema) + `vault.py` (markdown round-trip) — get the recipe format solid first, since everything else depends on it.
2. `agent.py` with Claude tool-calling for propose/approve/save, tested via a simple CLI script before wiring up any chat surface.
3. `ingest.py` + Postgres schema/`db.py` — wire "save" to also ingest.
4. FastAPI `main.py` (chat endpoint over WebSocket) + minimal web frontend.
5. `discord_bot/bot.py` reusing the same `agent.py`.
6. `docker-compose.yml` + Cloudflare Tunnel + `.env` secrets, deploy to the box.
7. Auth (`auth.py`) — can trail slightly behind since it's single-user initially, but schema for it should already exist per the DB design above.

## Verification

- Unit tests (`pytest`) for `vault.py` round-tripping: `Recipe -> markdown -> Recipe` should be lossless, including the `ai_filled` field.
- Manual test of the approval flow via a CLI/script hitting `agent.py` directly: feed a partial recipe, confirm Claude proposes filled-in fields and does **not** write to disk until an explicit "approve" call.
- After approval, verify: the file exists in the vault working copy, `git log` shows a new commit, `git push` succeeded against the GitHub backup repo, and the recipe row appears in Postgres (`SELECT * FROM recipes`).
- End-to-end test through the web chat UI and separately through the Discord bot, confirming both produce identical saved recipes via the shared `agent.py`.
- Confirm the box is reachable via the Cloudflare Tunnel URL from a device off the home network (e.g. cellular), and unreachable via any directly-exposed port (no port forwarding configured on the router).
- On the Mac, confirm the Obsidian Git plugin pulls the newly pushed recipe and it renders correctly as a normal Obsidian note.
