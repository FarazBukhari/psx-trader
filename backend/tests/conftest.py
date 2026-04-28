"""
pytest configuration for portfolio tests.

Each test that requests the `db_session` or `pm` fixture gets a completely
isolated async SQLite engine backed by a unique temp file.  The module-level
engine/session in app.db.database is monkeypatched so PortfolioManager always
uses the test engine — no production DB is touched.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from contextlib import asynccontextmanager

# Make sure the backend package is importable when running from /tmp
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.db.database as _db_module
from app.db.models import Base   # noqa: F401  — registers metadata
from app.portfolio.portfolio_manager import PortfolioManager


@pytest_asyncio.fixture
async def pm(tmp_path):
    """
    Yield a PortfolioManager wired to a fresh per-test SQLite DB.
    Patches app.db.database so all get_session() calls in PortfolioManager
    use this engine for the duration of the test.
    """
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"

    test_engine = create_async_engine(
        db_url,
        echo=False,
        connect_args={"check_same_thread": False},
    )
    test_session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )

    # Patch the module-level globals that get_session() closes over
    original_engine   = _db_module.async_engine
    original_factory  = _db_module.AsyncSessionLocal
    original_get_sess = _db_module.get_session

    _db_module.async_engine      = test_engine
    _db_module.AsyncSessionLocal = test_session_factory

    @asynccontextmanager
    async def _test_get_session():
        async with test_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    _db_module.get_session = _test_get_session

    # Create tables on the test engine
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    manager = PortfolioManager()
    await manager.ensure_default_portfolio()

    yield manager

    # Restore originals
    _db_module.async_engine      = original_engine
    _db_module.AsyncSessionLocal = original_factory
    _db_module.get_session       = original_get_sess

    await test_engine.dispose()
