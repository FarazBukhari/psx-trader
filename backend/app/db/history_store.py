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

    # How many ticks to buffer before flushing to DB in one INSERT.
    # Reduces write amplification during high-frequency polling.
    FLUSH_BATCH_SIZE = 10

    def __init__(self) -> None:
        self._tick_buffer:   list[dict] = []
        self._signal_buffer: list[dict] = []
        self._flush_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public write API
    # ------------------------------------------------------------------

    async def save_tick(self, stock: dict) -> None:
        """
        Buffer a scraped price tick.
        Flush automatically when buffer reaches FLUSH_BATCH_SIZE.
        Callers can also call flush_ticks() explicitly (e.g. on shutdown).
        """
        self._tick_buffer.append(stock)
        if len(self._tick_buffer) >= self.FLUSH_BATCH_SIZE:
            await self.flush_ticks()

    async def save_signal(self, signal: dict) -> None:
        """
        Buffer a generated signal for persistence.
        Only saves signals that actually changed, to reduce DB noise.
        Pass all signals — filtering happens here.
        """
        # Only persist changed signals to keep the signals_log lean.
        # Always persist BUY/SELL/FORCE_SELL regardless.
        sig_type = signal.get("signal", "HOLD")
        changed  = signal.get("signal_changed", False)
        if sig_type == "HOLD" and not changed:
            return
        self._signal_buffer.append(signal)
        if len(self._signal_buffer) >= self.FLUSH_BATCH_SIZE:
            await self.flush_signals()

    async def flush_ticks(self) -> None:
        """Write all buffered ticks to DB in a single INSERT."""
        if not self._tick_buffer:
            return
        async with self._flush_lock:
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
        """
        symbols = await self.get_available_symbols()
        if not symbols:
            logger.info("warm_price_buffer: no history in DB yet — starting fresh")
            return

        total = 0
        for symbol in symbols:
            rows = await self.get_history(symbol, n=200)
            for row in rows:
                price_buffer.push(symbol, row["close"])
            total += len(rows)

        logger.info(
            "warm_price_buffer: loaded %d ticks across %d symbols from DB",
            total,
            len(symbols),
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
