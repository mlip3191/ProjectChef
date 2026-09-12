"""Postgres engine/session setup and the ORM tables.

The vault markdown files (see vault.py) are the source of truth for what a
recipe *is* - these tables are a rebuildable, queryable index built from
that content via ingest.py. Nothing here should ever be treated as more
authoritative than the vault.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://projectchef:projectchef@localhost:5432/projectchef",
)


class Base(DeclarativeBase):
    pass


class UserRecord(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    recipes: Mapped[list["RecipeRecord"]] = relationship(back_populates="owner")


class RecipeRecord(Base):
    """A parsed, queryable copy of one recipe.

    `vault_path` is set only for vault-backed recipes (currently: the app
    owner only, per PLAN.md's multi-user decision). NULL means the recipe
    lives in the DB only, for future non-owner users.
    """

    __tablename__ = "recipes"
    __table_args__ = (UniqueConstraint("owner_id", "title", name="uq_recipe_owner_title"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    title: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(32), default="recipe")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)

    servings: Mapped[int | None] = mapped_column(nullable=True)
    prep_time: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cook_time: Mapped[str | None] = mapped_column(String(32), nullable=True)
    total_time: Mapped[str | None] = mapped_column(String(32), nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(32), nullable=True)

    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created: Mapped[date | None] = mapped_column(Date, nullable=True)

    ai_filled: Mapped[list[str]] = mapped_column(JSON, default=list)
    ingredients: Mapped[list[str]] = mapped_column(JSON, default=list)
    steps: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    vault_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    owner: Mapped["UserRecord"] = relationship(back_populates="recipes")


_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(DATABASE_URL)
    return _engine


def init_db() -> None:
    Base.metadata.create_all(get_engine())


def get_sessionmaker() -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine())
    return _session_factory


@contextmanager
def session_scope() -> Session:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
