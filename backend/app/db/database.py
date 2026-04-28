"""
Database engine + session factory.

Uses SQLite (via aiosqlite for async) by default.
Switch to PostgreSQL by setting DATABASE_URL env var:
  DATABASE_URL=postgresql+asyncpg://user:pass@localhost/psx

Async-first: all DB access in the app uses AsyncSession.
The sync `engine` is only used by Alembic migrations.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Resolve DB path
# ---------------------------------------------------------------------------

# Default: psx_trader.db lives at the project root (next to /backend, /config)
_BACKEND_DIR = Path(__file__).resolve().parents[2]   # …/psx-trader/backend/
_DEFAULT_DB  = f"sqlite+aiosqlite:///{_BACKEND_DIR / 'psx_trader.db'}"

DATABASE_URL      = os.getenv("DATABASE_URL", _DEFAULT_DB)
DATABASE_URL_SYNC = (
    DATABASE_URL
    .replace("sqlite+aiosqlite", "sqlite")
    .replace("postgresql+asyncpg", "postgresql")
)

logger.debug("Database URL: %s", DATABASE_URL)

# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

# Async engine — used by the FastAPI app
async_engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    # SQLite-specific: needed for async multi-threaded access
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

# Sync engine — used ONLY by Alembic CLI migrations
engine = create_engine(
    DATABASE_URL_SYNC,
    echo=False,
    connect_args={"check_same_thread": False} if DATABASE_URL_SYNC.startswith("sqlite") else {},
)

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager for DB sessions. Auto-commits on clean exit, rolls back on error."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Table creation (used on startup if not using Alembic)
# ---------------------------------------------------------------------------

async def init_db() -> None:
    """Create all tables that don't already exist. Safe to call on every startup."""
    # Import models so their metadata is registered on Base
    from . import models  # noqa: F401

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database initialised at %s", DATABASE_URL)
