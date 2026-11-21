"""Database engine, session factory and shared column types.

Postgres (Supabase) in production, SQLite for local development and tests.
Everything here is written to work on both.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

from .config import get_settings

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


def new_public_id() -> str:
    # Short, unguessable, URL-safe id for links sent over WhatsApp.
    return secrets.token_urlsafe(8)


class UTCDateTime(TypeDecorator):
    """Always store UTC and always return timezone-aware datetimes.

    SQLite drops tzinfo; without this, comparisons between aware and naive
    datetimes raise at runtime in exactly the code paths (reminders, leases)
    that must not fail.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime passed to UTCDateTime")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def normalize_db_url(url: str) -> str:
    """Hosts hand out postgres:// or postgresql://; SQLAlchemy needs the async driver named."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+asyncpg://" + url[len(prefix) :]
            break
    if url.startswith("postgresql+asyncpg://") and "?" in url:
        # asyncpg does not understand libpq options such as sslmode / channel_binding (Neon adds both); SSL is set in get_engine.
        from urllib.parse import parse_qsl, urlencode

        base, query = url.split("?", 1)
        keep = [(k, v) for k, v in parse_qsl(query) if k not in ("sslmode", "channel_binding")]
        url = base + ("?" + urlencode(keep) if keep else "")
    return url


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        url = normalize_db_url(get_settings().database_url)
        kwargs: dict = {"pool_pre_ping": True}
        connect_args: dict = {}
        if ":6543" in url:  # Supabase transaction pooler (pgbouncer) cannot reuse prepared statements
            connect_args["statement_cache_size"] = 0
        raw = get_settings().database_url
        if url.startswith("postgresql") and ("sslmode=require" in raw or "neon.tech" in raw or "supabase" in raw):
            connect_args["ssl"] = True
        if connect_args:
            kwargs["connect_args"] = connect_args
        if url.startswith("sqlite"):
            kwargs = {"connect_args": {"timeout": 30}}
        _engine = create_async_engine(url, **kwargs)
        if url.startswith("sqlite"):

            @event.listens_for(_engine.sync_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()

        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    async with sessionmaker()() as session:
        yield session


async def init_models() -> None:
    from . import models  # noqa: F401  (register tables)

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def is_postgres() -> bool:
    return get_settings().database_url.startswith("postgresql")
