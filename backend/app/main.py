"""FastAPI app: WebSocket chat endpoint + static chat UI.

Auth doesn't exist yet (see PLAN.md build-order step 7) - every WebSocket
connection currently talks to a single default "owner" user record, which
matches the single-user scope of this phase. Swapping in real per-user
auth later only touches `_get_or_create_default_user` below, not the
agent/ingest/vault code it wires together.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .agent import CookingAgent
from .db import RecipeRecord, UserRecord, get_sessionmaker, init_db

VAULT_DIR = Path(os.environ.get("VAULT_DIR", "./vault_dev"))
DEFAULT_USERNAME = "owner"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="ProjectChef", lifespan=lifespan)


def create_agent(session: Session, owner_id: int) -> CookingAgent:
    return CookingAgent(vault_dir=VAULT_DIR, db_session=session, owner_id=owner_id)


def _get_or_create_default_user(session: Session) -> UserRecord:
    user = session.query(UserRecord).filter_by(username=DEFAULT_USERNAME).one_or_none()
    if user is None:
        user = UserRecord(username=DEFAULT_USERNAME, password_hash="")
        session.add(user)
        session.commit()
    return user


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/recipes")
def list_recipes() -> list[dict]:
    session = get_sessionmaker()()
    try:
        owner = _get_or_create_default_user(session)
        records = (
            session.query(RecipeRecord)
            .filter_by(owner_id=owner.id)
            .order_by(RecipeRecord.title)
            .all()
        )
        return [{"title": r.title, "tags": r.tags} for r in records]
    finally:
        session.close()


@app.get("/api/recipes/{title}")
def get_recipe_markdown(title: str) -> dict:
    path = VAULT_DIR / "Recipes" / f"{title}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"No recipe named {title!r}")
    return {"title": title, "markdown": path.read_text()}


@app.websocket("/ws")
async def websocket_chat(websocket: WebSocket) -> None:
    """Protocol: client sends {"type": "chat", "text": ...} or
    {"type": "save_edited", "markdown": ...}. Server replies with
    {"type": "reply", "text": ..., "proposal_markdown": str | null} for chat,
    or {"type": "saved", "path": ...} / {"type": "error", "message": ...} for
    save_edited.
    """
    await websocket.accept()
    session = get_sessionmaker()()
    try:
        owner = _get_or_create_default_user(session)
        agent = create_agent(session, owner.id)
        while True:
            raw = await websocket.receive_text()
            message = json.loads(raw)

            if message["type"] == "chat":
                reply = await run_in_threadpool(agent.send, message["text"])
                await websocket.send_json(
                    {"type": "reply", "text": reply, "proposal_markdown": agent.pending_markdown()}
                )
            elif message["type"] == "save_edited":
                try:
                    result = await run_in_threadpool(agent.save_edited, message["markdown"])
                    await websocket.send_json(
                        {
                            "type": "saved",
                            "path": result["path"],
                            "title": result.get("title"),
                            "git": result.get("git"),
                        }
                    )
                except Exception as exc:  # malformed markdown from manual edits
                    await websocket.send_json({"type": "error", "message": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        session.close()


# Registered last so /health and /ws (above) are matched first.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
