"""FastAPI app: WebSocket chat endpoint + static chat UI.

Auth doesn't exist yet (see PLAN.md build-order step 7) - every WebSocket
connection currently talks to a single default "owner" user record, which
matches the single-user scope of this phase. Swapping in real per-user
auth later only touches `_get_or_create_default_user` below, not the
agent/ingest/vault code it wires together.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .agent import CookingAgent
from .db import UserRecord, get_sessionmaker, init_db

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


@app.websocket("/ws")
async def websocket_chat(websocket: WebSocket) -> None:
    await websocket.accept()
    session = get_sessionmaker()()
    try:
        owner = _get_or_create_default_user(session)
        agent = create_agent(session, owner.id)
        while True:
            message = await websocket.receive_text()
            reply = await run_in_threadpool(agent.send, message)
            await websocket.send_text(reply)
    except WebSocketDisconnect:
        pass
    finally:
        session.close()


# Registered last so /health and /ws (above) are matched first.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
