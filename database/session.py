from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

engine: AsyncEngine | None = None
SessionLocal: async_sessionmaker[AsyncSession] | None = None


def init_engine(url: str) -> AsyncEngine:
    global engine, SessionLocal
    if url.startswith("sqlite"):
        engine = create_async_engine(url, poolclass=StaticPool, connect_args={"check_same_thread": False})
    else:
        engine = create_async_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    return engine


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """One atomic transaction: commits on success, rolls back on any exception."""
    assert SessionLocal is not None, "init_engine() was not called"
    async with SessionLocal() as session:
        async with session.begin():
            yield session
