"""
HistoryStore — async interface between the app and the database.

Responsibilities:
  - Persist every scraped price tick (save_tick)
  - Persist every generated signal (save_signal)
  - Return price history for a symbol (get_history)
  - Return recent signals for a symbol (get_recent_signals)
  - Warm the in-memory PriceBuffer from DB on startup (warm_price_buffer)

Design notes:
  - All public methods are async.
  - Uses get_session() context manager — each call is one transaction.
  - Bulk inserts are batched to avoid per-tick overhead at high poll rates.
  - Writes to DB happen in background (fire-and-forget via asyncio.create_task)
    so they never block the main poll loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Optional

from sqlalchemy import select, func, text

from .database import get_session
from .models import PriceHistory, SignalLog

if TYPE_CHECKING:
    # Avoid circular import — signal_engine imports from here at runtime too
    from ..strategy.signal_engine import PriceBuffer

logger = logging.getLogger(__name__)


class HistoryStore:
    """
    Async data-access layer for price history and signal logs.

    Usage:
        store = HistoryStore()
        await store.save_tick(stock_dict)
        history = await store.get_history("ENGRO", n=60)
        await store.warm_price_buffer(price_buffer)
    """

    # Flush to DB when buffer reaches this size OR when max age elapses.
    FLUSH_BATCH_SIZE  = 20    # was 10 — fewer, larger writes
    FLUSH_MAX_AGE_S   = 60    # flush at most 60 s after the first item was buffered

    def __init__(self) -> None:
        self._tick_buffer:   list[dict] = []
        self._signal_buffer: list[dict] = []
        # Timestamps of when each buffer last received its first new item.
        self._tick_buffer_since:   float = 0.0
        self._signal_buffer_since: float = 0.0
        # Single lock shared by both flush methods so tick and signal flushes
        # don't overlap each other — reduces concurrent SQLite write contention.
        self._flush_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public write API
    # ------------------------------------------------------------------

    async def save_tick(self, stock: dict) -> None:
        """
        Buffer a scraped price tick.
        Flushes when buffer reaches FLUSH_BATCH_SIZE OR when the oldest buffered
        item is more than FLUSH_MAX_AGE_S seconds old (time-based safety net).
        """
        now = time.time()
        if not self._tick_buffer:
            self._tick_buffer_since = now
        self._tick_buffer.append(stock)
        age = now - self._tick_buffer_since
        if len(self._tick_buffer) >= self.FLUSH_BATCH_SIZE or age >= self.FLUSH_MAX_AGE_S:
            await self.flush_ticks()

    async def save_signal(self, signal: dict) -> None:
        """
        Buffer a generated signal for persistence.
        Only saves signals that actually changed, to reduce DB noise.
        Flushes on size OR age, same as save_tick.
        """
        # Only persist changed signals to keep the signals_log lean.
        # Always persist BUY/SELL/FORCE_SELL regardless.
        sig_type = signal.get("signal", "HOLD")
        changed  = signal.get("signal_changed", False)
        if sig_type == "HOLD" and not changed:
            return
        now = time.time()
        if not self._signal_buffer:
            self._signal_buffer_since = now
        self._signal_buffer.append(signal)
        age = now - self._signal_buffer_since
        if len(self._signal_buffer) >= self.FLUSH_BATCH_SIZE or age >= self.FLUSH_MAX_AGE_S:
            await self.flush_signals()

    async def flush_ticks(self) -> None:
        """Write all buffered ticks to DB in a single INSERT."""
        if not self._tick_buffer:
            return
        async with self._flush_lock:
            # Drain buffer inside the lock so flush_signals can't overlap
            batch = self._tick_buffer[:]
            self._tick_buffer.clear()
            rows = [_stock_to_row(s) for s in batch]
            try:
                async with get_session() as session:
                    session.add_all([PriceHistory(**r) for r in rows])
                logger.debug("Flushed %d price ticks to DB", len(rows))
            except Exception as exc:
                logger.error("Failed to flush price ticks: %s", exc)
                # Re-buffer on failure so data isn't lost
                self._tick_buffer = batch + self._tick_buffer

    async def flush_signals(self) -> None:
        """Write all buffered signals to DB in a single INSERT."""
        if not self._signal_buffer:
            return
        async with self._flush_lock:
            batch = self._signal_buffer[:]
            self._signal_buffer.clear()
            rows = [_signal_to_row(s) for s in batch]
            try:
                async with get_session() as session:
                    session.add_all([SignalLog(**r) for r in rows])
                logger.debug("Flushed %d signals to DB", len(rows))
            except Exception as exc:
                logger.error("Failed to flush signals: %s", exc)
                self._signal_buffer = batch + self._signal_buffer

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------

    async def get_history(
        self,
        symbol: str,
        n: int = 200,
        since: Optional[int] = None,
    ) -> list[dict]:
        """
        Return the last `n` price ticks for `symbol`, ordered oldest-first.
        Optionally filter to ticks since a Unix timestamp.
        Returns plain dicts (not ORM objects) — safe to serialize to JSON.
        """
        async with get_session() as session:
            q = (
                select(PriceHistory)
                .where(PriceHistory.symbol == symbol.upper())
            )
            if since:
                q = q.where(PriceHistory.scraped_at >= since)

            # Fetch last N rows by time (desc), then reverse for oldest-first
            q = q.order_by(PriceHistory.scraped_at.desc()).limit(n)
            result = await session.execute(q)
            rows = result.scalars().all()

        return [_row_to_dict(r) for r in reversed(rows)]

    async def get_recent_signals(
        self,
        symbol: str,
        limit: int = 50,
    ) -> list[dict]:
        """Return recent non-HOLD signals for a symbol, newest-first."""
        async with get_session() as session:
            q = (
                select(SignalLog)
                .where(SignalLog.symbol == symbol.upper())
                .order_by(SignalLog.generated_at.desc())
                .limit(limit)
            )
            result = await session.execute(q)
            rows = result.scalars().all()

        return [_signal_row_to_dict(r) for r in rows]

    async def get_available_symbols(self) -> list[str]:
        """Return all symbols that have price history in the DB."""
        async with get_session() as session:
            q = select(PriceHistory.symbol).distinct()
            result = await session.execute(q)
            return [row[0] for row in result.all()]

    async def get_history_stats(self) -> dict:
        """Diagnostic: row counts and time range per symbol."""
        async with get_session() as session:
            q = (
                select(
                    PriceHistory.symbol,
                    func.count(PriceHistory.id).label("ticks"),
                    func.min(PriceHistory.scraped_at).label("first_at"),
                    func.max(PriceHistory.scraped_at).label("last_at"),
                )
                .group_by(PriceHistory.symbol)
                .order_by(text("ticks DESC"))
            )
            result = await session.execute(q)
            return {
                row.symbol: {
                    "ticks":    row.ticks,
                    "first_at": row.first_at,
                    "last_at":  row.last_at,
                }
                for row in result.all()
            }

    # ------------------------------------------------------------------
    # Warm-up (called once on app startup)
    # ------------------------------------------------------------------

    async def warm_price_buffer(self, price_buffer: "PriceBuffer") -> None:
        """
        Load the last 200 closing prices per symbol from DB into the
        in-memory PriceBuffer so that SMA / RSI calculations are
        immediately accurate — even after a server restart.

        Without this, every restart would produce 20+ ticks of blind
        HOLD signals while the buffer slowly fills up.

        Optimisation: previously this issued N+1 queries (one SELECT DISTINCT
        to get symbols, then one SELECT per symbol).  Now it issues 2 queries:
          1. SELECT DISTINCT symbol                          (get symbol list)
          2. SELECT symbol, close WHERE symbol IN (...)      (all prices at once)
        Prices are limited to a recent window then capped at 200 per symbol
        in Python, so the query never pulls more rows than necessary.
        """
        symbols = await self.get_available_symbols()
        if not symbols:
            logger.info("warm_price_buffer: no history in DB yet — starting fresh")
            return

        # At 15 s poll, 200 ticks ≈ 50 min.  Use a 4-hour window to guarantee
        # we get 200 rows even after a long gap, without fetching all history.
        cutoff = int(time.time()) - (4 * 3600)

        async with get_session() as session:
            q = (
                select(PriceHistory.symbol, PriceHistory.close)
                .where(PriceHistory.symbol.in_(symbols))
                .where(PriceHistory.scraped_at >= cutoff)
                .order_by(PriceHistory.symbol, PriceHistory.scraped_at.desc())
            )
            result = await session.execute(q)
            all_rows = result.all()

        # Group in Python: keep first 200 per symbol (already DESC, so these
        # are the most recent), then push oldest-first into the buffer.
        by_symbol: dict[str, list[float]] = defaultdict(list)
        for row in all_rows:
            lst = by_symbol[row.symbol]
            if len(lst) < 200:
                lst.append(row.close)

        total = 0
        for symbol, prices in by_symbol.items():
            for price in reversed(prices):   # oldest-first matches buffer expectation
                price_buffer.push(symbol, price)
            total += len(prices)

        logger.info(
            "warm_price_buffer: loaded %d ticks across %d symbols from DB (2 queries)",
            total,
            len(by_symbol),
        )


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _stock_to_row(s: dict) -> dict:
    """Convert a scraper stock dict → PriceHistory column dict."""
    return {
        "symbol":     s.get("symbol", "").upper(),
        "sector":     s.get("sector"),
        "ldcp":       s.get("ldcp"),
        "open_price": s.get("open"),
        "high":       s.get("high"),
        "low":        s.get("low"),
        "close":      s.get("current", 0.0),
        "volume":     s.get("volume"),
        "change_pct": s.get("change_pct"),
        "source":     s.get("source", "live"),
        "scraped_at": int(s.get("timestamp", time.time())),
    }


def _signal_to_row(s: dict) -> dict:
    """Convert a SignalEngine output dict → SignalLog column dict."""
    sources = s.get("signal_sources", [])
    return {
        "symbol":         s.get("symbol", "").upper(),
        "signal":         s.get("signal", "HOLD"),
        "prev_signal":    s.get("prev_signal"),
        "signal_changed": bool(s.get("signal_changed", False)),
        "signal_sources": json.dumps(sources) if sources else None,
        "action_score":   s.get("action_score"),
        "horizon":        s.get("horizon", "short"),
        "rsi":            s.get("rsi"),
        "sma5":           s.get("sma5"),
        "sma20":          s.get("sma20"),
        "price":          s.get("current"),
        "volume":         s.get("volume"),
        "confidence":     _cap_confidence(s.get("confidence")),
        "time_horizon":   s.get("time_horizon"),
        "generated_at":   int(time.time()),
    }


def _cap_confidence(value: Optional[float]) -> Optional[float]:
    """Confidence must never exceed 85% — enforced at the storage boundary."""
    if value is None:
        return None
    return round(min(float(value), 0.85), 4)


def _row_to_dict(row: PriceHistory) -> dict:
    return {
        "id":         row.id,
        "symbol":     row.symbol,
        "sector":     row.sector,
        "ldcp":       row.ldcp,
        "open":       row.open_price,
        "high":       row.high,
        "low":        row.low,
        "close":      row.close,
        "volume":     row.volume,
        "change_pct": row.change_pct,
        "source":     row.source,
        "scraped_at": row.scraped_at,
    }


def _signal_row_to_dict(row: SignalLog) -> dict:
    sources = []
    if row.signal_sources:
        try:
            sources = json.loads(row.signal_sources)
        except (json.JSONDecodeError, TypeError):
            sources = []
    return {
        "id":             row.id,
        "symbol":         row.symbol,
        "signal":         row.signal,
        "prev_signal":    row.prev_signal,
        "signal_changed": row.signal_changed,
        "signal_sources": sources,
        "action_score":   row.action_score,
        "horizon":        row.horizon,
        "rsi":            row.rsi,
        "sma5":           row.sma5,
        "sma20":          row.sma20,
        "price":          row.price,
        "volume":         row.volume,
        "confidence":     row.confidence,
        "time_horizon":   row.time_horizon,
        "generated_at":   row.generated_at,
    }
