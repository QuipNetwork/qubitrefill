"""Async engine + session factory.

A single process-wide async engine and ``async_sessionmaker``. ``init_engine``
is idempotent (it no-ops if already configured) so the API lifespan can call it
on startup while tests pre-initialise it against a throwaway database with
``force=True``. Both the FastAPI dependency (``get_session``) and the
non-request callers (auth middleware, MTM scheduler) go through ``session_scope``
/ ``get_sessionmaker`` so they all share the same engine.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .. import config

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(url: str | None = None, *, force: bool = False, poolclass: type | None = None) -> None:
    """Create the engine + sessionmaker. No-op if already created unless ``force``.

    ``poolclass`` lets tests pass ``NullPool`` so connections are never reused
    across event loops (TestClient's loop vs. the truncation fixture's loop).
    """
    global _engine, _sessionmaker
    if _engine is not None and not force:
        return
    resolved = url or os.environ.get("DATABASE_URL") or config.DATABASE_URL
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if poolclass is not None:
        kwargs["poolclass"] = poolclass
    _engine = create_async_engine(resolved, **kwargs)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)


def get_engine() -> AsyncEngine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        init_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Standalone session for non-request callers (middleware, scheduler)."""
    async with get_sessionmaker()() as session:
        yield session


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Commits on success, rolls back on any exception."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
