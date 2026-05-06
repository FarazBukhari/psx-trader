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

from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

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

# ---------------------------------------------------------------------------
# SQLite tuning
# ---------------------------------------------------------------------------
# Applied on every new connection via a sync event listener.
# Works with aiosqlite because SQLAlchemy's async engine wraps a real sqlite3
# connection under the hood — the sync "connect" event still fires.
#
# WAL mode    — writers don't block readers; concurrent write attempts queue
#               up rather than hard-failing with "database is locked".
# busy_timeout — SQLite will retry for up to 15 000 ms before raising an error,
#               giving queued async tasks time to drain.
# synchronous=NORMAL — safe with WAL; faster than FULL with no meaningful
#               durability trade-off for this workload.

def _apply_sqlite_pragmas(dbapi_conn, _connection_record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=15000")   # ms — wait up to 15s before failing
    cur.execute("PRAGMA cache_size=-8000")     # 8 MB page cache per connection
    cur.close()


# Async engine — used by the FastAPI app
#
# SQLite: use NullPool — connections are cheap file-handles and do not benefit
# from a connection pool.  Pooling SQLite actually hurts: it serialises
# concurrent async tasks waiting for a slot, causing the QueuePool exhaustion
# seen when many forward_tracker tasks fire in the same tick.
# PostgreSQL: keep the default pool (QueuePool) with generous limits.
_is_sqlite = DATABASE_URL.startswith("sqlite")

async_engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args=(
        {"check_same_thread": False, "timeout": 30}
        if _is_sqlite else {}
    ),
    **( {"poolclass": NullPool} if _is_sqlite else {"pool_size": 10, "max_overflow": 20} ),
)

# Sync engine — used ONLY by Alembic CLI migrations
engine = create_engine(
    DATABASE_URL_SYNC,
    echo=False,
    connect_args=(
        {"check_same_thread": False, "timeout": 20}
        if DATABASE_URL_SYNC.startswith("sqlite") else {}
    ),
)

# Register the pragma hook on both engines
if DATABASE_URL.startswith("sqlite"):
    event.listen(async_engine.sync_engine, "connect", _apply_sqlite_pragmas)
    event.listen(engine,                   "connect", _apply_sqlite_pragmas)

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
        # Idempotent column migrations — safe to run on every startup.
        # Each entry is (label, sql). Errors are suppressed — they mean the
        # column/index already exists, which is the expected case after first run.
        for label, ddl in _MIGRATIONS:
            try:
                await conn.execute(text(ddl))
                logger.debug("migration OK: %s", label)
            except Exception:
                pass   # already exists — no action needed

    # Multi-step table migrations run separately (can't use the try/except loop
    # for dependent DDL steps — partial failure would leave the schema broken).
    await _migrate_forward_trades_outcome()

    logger.info("Database initialised at %s", DATABASE_URL)


async def _migrate_forward_trades_outcome() -> None:
    """
    Widen forward_trades.outcome from VARCHAR(8) → VARCHAR(16) to accommodate
    four-bucket outcomes: STRONG_WIN | WEAK_WIN | BREAKEVEN | LOSS.

    SQLite has no ALTER COLUMN, so we use the rename/recreate pattern.
    The migration is idempotent: guarded by checking whether forward_trades_old
    already exists (already migrated) and whether forward_trades exists at all
    (fresh DB — create_all already used the new schema).

    NOTE: SQLite ignores VARCHAR(N) length constraints at the storage level, so
    no existing data is ever truncated.  This migration is cosmetically correct
    and keeps the declared column type accurate for tooling / future Postgres use.
    """
    async with async_engine.begin() as conn:
        # Check if migration is needed
        tables = (await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='forward_trades'")
        )).fetchall()
        if not tables:
            return   # fresh DB — create_all already used String(16), nothing to do

        # Check if already migrated (outcome column already declared TEXT/VARCHAR(16))
        # We detect this by looking for forward_trades_old (left from a previous run)
        # or by checking the column declaration via PRAGMA.
        pragma = (await conn.execute(text("PRAGMA table_info(forward_trades)"))).fetchall()
        col_types = {row[1]: row[2] for row in pragma}   # name → type
        outcome_type = col_types.get("outcome", "")

        # Already widened (TEXT has no N, VARCHAR(16) is what we want)
        if outcome_type in ("TEXT", "VARCHAR(16)"):
            logger.debug("_migrate_forward_trades_outcome: already migrated (%s) — skipping", outcome_type)
            return

        logger.info("_migrate_forward_trades_outcome: widening outcome column %s → VARCHAR(16)", outcome_type)
        try:
            await conn.execute(text("ALTER TABLE forward_trades RENAME TO forward_trades_old"))
            await conn.execute(text("""
                CREATE TABLE forward_trades (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol           TEXT    NOT NULL,
                    signal           TEXT    NOT NULL,
                    entry_price      REAL    NOT NULL,
                    entry_time       INTEGER NOT NULL,
                    max_price_seen   REAL    NOT NULL,
                    min_price_seen   REAL    NOT NULL,
                    exit_price       REAL,
                    exit_time        INTEGER,
                    status           TEXT    NOT NULL DEFAULT 'OPEN',
                    outcome          TEXT    NOT NULL DEFAULT 'BREAKEVEN',
                    mfe_pct          REAL    NOT NULL DEFAULT 0.0,
                    mae_pct          REAL    NOT NULL DEFAULT 0.0,
                    duration_minutes REAL    NOT NULL DEFAULT 0.0,
                    UNIQUE (symbol, entry_time)
                )
            """))
            await conn.execute(text("INSERT INTO forward_trades SELECT * FROM forward_trades_old"))
            await conn.execute(text("DROP TABLE forward_trades_old"))
            # Recreate indexes dropped by the rename
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ft_symbol_status ON forward_trades (symbol, status)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ft_entry_time    ON forward_trades (entry_time)"))
            logger.info("_migrate_forward_trades_outcome: migration complete")
        except Exception as exc:
            logger.error("_migrate_forward_trades_outcome: failed — %s", exc)
            raise   # propagate — broken schema is worse than startup failure


# Column migrations for existing databases.
# create_all only creates new tables — it won't add columns to existing ones.
# Add new (label, sql) entries here whenever a column is added to a model.
_MIGRATIONS: list[tuple[str, str]] = [
    # Phase 3: ML training fields on prediction_log
    ("prediction_log.horizon_bucket",    "ALTER TABLE prediction_log ADD COLUMN horizon_bucket TEXT"),
    ("prediction_log.pct_change",        "ALTER TABLE prediction_log ADD COLUMN pct_change REAL"),
    ("prediction_log.evaluated_lag_sec", "ALTER TABLE prediction_log ADD COLUMN evaluated_lag_sec INTEGER"),
    # Indexes
    ("ix_pred_outcome_time",             "CREATE INDEX IF NOT EXISTS ix_pred_outcome_time ON prediction_log (outcome, predicted_at)"),
    # Phase 7: signal provenance on forward_trades
    ("forward_trades.signal_sources",    "ALTER TABLE forward_trades ADD COLUMN signal_sources TEXT"),
]
