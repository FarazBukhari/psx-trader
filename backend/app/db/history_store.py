"""
HistoryStore — async interface between the app and the database.

Responsibilities:
  - Persist every scraped price tick (save_tick → intraday_ticks)
  - Persist every EOD price row (save_eod_tick → eod_prices)
  - Persist every generated signal (save_signal → signals_log)
  - Return intraday price history for a symbol (get_history)
  - Return EOD price history for a symbol (get_eod_history)
  - Return recent signals for a symbol (get_recent_signals)
  - Warm the in-memory PriceBuffer from DB on startup (warm_price_buffer)

Table routing:
  - Live scrape ticks              → intraday_ticks   (new)
  - Historical / EOD download rows → eod_prices        (new)
  - price_history                  → legacy table, kept for reads during transition;
                                     HistoryStore no longer writes to it.

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
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Optional

from sqlalchemy import select, func, text

from .database import get_session
from .models import EODPrice, IntradayTick, PriceHistory, SignalLog

if TYPE_CHECKING:
    # Avoid circular import — signal_engine imports from here at runtime too
    from ..strategy.signal_engine import PriceBuffer, SignalEngine, VolumeSpikeStrategy

logger = logging.getLogger(__name__)

# PKT = UTC+5
_PKT = timezone(timedelta(hours=5))


def _ts_to_date_key(ts: int) -> int:
    """Convert a Unix timestamp to an integer YYYYMMDD date key in PKT."""
    dt = datetime.fromtimestamp(ts, tz=_PKT)
    return dt.year * 10_000 + dt.month * 100 + dt.day


class HistoryStore:
    """
    Async data-access layer for price history and signal logs.

    Usage:
        store = HistoryStore()
        await store.save_tick(stock_dict)          # intraday live tick
        await store.save_eod_tick(stock_dict)      # EOD historical row
        history     = await store.get_history("ENGRO", n=60)
        eod_history = await store.get_eod_history("ENGRO", n=200)
        await store.warm_price_buffer(price_buffer)
    """

    # Flush to DB when buffer reaches this size OR when max age elapses.
    FLUSH_BATCH_SIZE  = 20    # fewer, larger writes
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
        # Last saved close price per symbol — used to deduplicate unchanged ticks.
        self._last_tick_price: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Public write API
    # ------------------------------------------------------------------

    async def save_tick(self, stock: dict) -> None:
        """
        Buffer a scraped price tick → intraday_ticks.

        Skips the tick if the close price is identical to the last saved value
        for this symbol — avoids filling the DB with no-op rows during flat markets.
        Flushes when buffer reaches FLUSH_BATCH_SIZE OR when the oldest buffered
        item is more than FLUSH_MAX_AGE_S seconds old (time-based safety net).
        """
        symbol = stock.get("symbol", "").upper()
        close  = stock.get("current", 0.0)
        if self._last_tick_price.get(symbol) == close:
            return   # price unchanged — skip
        self._last_tick_price[symbol] = close

        now = time.time()
        if not self._tick_buffer:
            self._tick_buffer_since = now
        self._tick_buffer.append(stock)
        age = now - self._tick_buffer_since
        if len(self._tick_buffer) >= self.FLUSH_BATCH_SIZE or age >= self.FLUSH_MAX_AGE_S:
            await self.flush_ticks()

    async def save_eod_tick(self, stock: dict) -> None:
        """
        Persist a single EOD price row to eod_prices immediately (no buffering).

        Called by scripts/fetch_historical.py and the nightly sync job.
        Uses INSERT OR IGNORE so duplicate (symbol, date_key) pairs are silently
        skipped — the first row written per day is kept.
        """
        row = _stock_to_eod_row(stock)
        try:
            async with get_session() as session:
                # Check if this (symbol, date_key) already exists
                existing = await session.execute(
                    select(EODPrice.id)
                    .where(EODPrice.symbol == row["symbol"])
                    .where(EODPrice.date_key == row["date_key"])
                )
                if existing.scalar() is None:
                    session.add(EODPrice(**row))
        except Exception as exc:
            logger.error("Failed to save EOD tick %s %s: %s", row.get("symbol"), row.get("date_key"), exc)

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
        """Write all buffered ticks to intraday_ticks in a single INSERT."""
        if not self._tick_buffer:
            return
        async with self._flush_lock:
            batch = self._tick_buffer[:]
            self._tick_buffer.clear()
            rows = [_stock_to_intraday_row(s) for s in batch]
            try:
                async with get_session() as session:
                    session.add_all([IntradayTick(**r) for r in rows])
                logger.debug("Flushed %d intraday ticks to DB", len(rows))
            except Exception as exc:
                logger.error("Failed to flush intraday ticks: %s", exc)
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
        Return the last `n` intraday ticks for `symbol`, ordered oldest-first.

        Reads from intraday_ticks first. Falls back to price_history for
        symbols whose data predates the migration.
        """
        rows = await self._get_intraday_rows(symbol.upper(), n=n, since=since)
        if not rows:
            # Legacy fallback — price_history still has older data
            rows = await self._get_legacy_rows(symbol.upper(), n=n, since=since)
        return rows

    async def get_eod_history(
        self,
        symbol: str,
        n: int = 500,
        start_ts: Optional[int] = None,
        end_ts:   Optional[int] = None,
    ) -> list[dict]:
        """
        Return EOD daily rows for `symbol` ordered oldest-first.

        Used by the backtester and FeatureEngine. Falls back to price_history
        (source='historical') if eod_prices has no data for this symbol yet.
        """
        async with get_session() as session:
            q = (
                select(EODPrice)
                .where(EODPrice.symbol == symbol.upper())
            )
            if start_ts:
                start_key = _ts_to_date_key(start_ts)
                q = q.where(EODPrice.date_key >= start_key)
            if end_ts:
                end_key = _ts_to_date_key(end_ts)
                q = q.where(EODPrice.date_key <= end_key)
            q = q.order_by(EODPrice.date_key.asc()).limit(n)
            result  = await session.execute(q)
            eod_rows = result.scalars().all()

        if eod_rows:
            return [_eod_row_to_dict(r) for r in eod_rows]

        # Fallback: legacy price_history with source='historical'
        return await self._get_legacy_eod_rows(symbol.upper(), n=n, start_ts=start_ts, end_ts=end_ts)

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
        """Return all symbols that have intraday tick history in the DB."""
        async with get_session() as session:
            # Check intraday_ticks first (new table)
            q = select(IntradayTick.symbol).distinct()
            result = await session.execute(q)
            symbols = [row[0] for row in result.all()]

        if not symbols:
            # Fall back to legacy price_history during transition
            async with get_session() as session:
                q = select(PriceHistory.symbol).distinct()
                result = await session.execute(q)
                symbols = [row[0] for row in result.all()]

        return symbols

    async def get_history_stats(self) -> dict:
        """Diagnostic: row counts and time range per symbol across both tables."""
        async with get_session() as session:
            q = (
                select(
                    IntradayTick.symbol,
                    func.count(IntradayTick.id).label("ticks"),
                    func.min(IntradayTick.scraped_at).label("first_at"),
                    func.max(IntradayTick.scraped_at).label("last_at"),
                )
                .group_by(IntradayTick.symbol)
                .order_by(text("ticks DESC"))
            )
            result = await session.execute(q)
            intraday_stats = {
                row.symbol: {
                    "intraday_ticks": row.ticks,
                    "first_at":       row.first_at,
                    "last_at":        row.last_at,
                }
                for row in result.all()
            }

            q2 = (
                select(
                    EODPrice.symbol,
                    func.count(EODPrice.id).label("days"),
                    func.min(EODPrice.date_key).label("first_date"),
                    func.max(EODPrice.date_key).label("last_date"),
                )
                .group_by(EODPrice.symbol)
                .order_by(text("days DESC"))
            )
            result2 = await session.execute(q2)
            for row in result2.all():
                sym_stats = intraday_stats.setdefault(row.symbol, {})
                sym_stats["eod_days"]   = row.days
                sym_stats["first_date"] = row.first_date
                sym_stats["last_date"]  = row.last_date

        return intraday_stats

    # ------------------------------------------------------------------
    # Warm-up (called once on app startup)
    # ------------------------------------------------------------------

    async def warm_price_buffer(self, price_buffer: "PriceBuffer") -> None:
        """
        Load the last 200 closing prices per symbol from intraday_ticks into
        the in-memory PriceBuffer so SMA/RSI calculations are immediately
        accurate — even after a server restart.

        Falls back to price_history (legacy) if intraday_ticks is empty.
        """
        symbols = await self.get_available_symbols()
        if not symbols:
            logger.info("warm_price_buffer: no history in DB yet — starting fresh")
            return

        # At 15 s poll, 200 ticks ≈ 50 min. Use a 4-hour window to guarantee
        # we get 200 rows even after a long gap, without fetching all history.
        cutoff = int(time.time()) - (4 * 3600)

        # Try intraday_ticks first
        async with get_session() as session:
            q = (
                select(IntradayTick.symbol, IntradayTick.close)
                .where(IntradayTick.symbol.in_(symbols))
                .where(IntradayTick.scraped_at >= cutoff)
                .order_by(IntradayTick.symbol, IntradayTick.scraped_at.desc())
            )
            result = await session.execute(q)
            all_rows = result.all()

        if not all_rows:
            # Fall back to legacy price_history
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
            "warm_price_buffer: loaded %d ticks across %d symbols",
            total,
            len(by_symbol),
        )

    async def warm_prev_signals(self, engine: "SignalEngine") -> None:
        """
        Populate engine._prev_signals from the most recent signal per symbol
        in signals_log.

        Without this, every server restart resets _prev_signals to {}, so
        signal_changed is False for ALL symbols on the first poll tick after
        a restart — even for signals that were already active.
        """
        async with get_session() as session:
            subq = (
                select(
                    SignalLog.symbol,
                    func.max(SignalLog.generated_at).label("max_ts"),
                )
                .group_by(SignalLog.symbol)
                .subquery()
            )
            stmt = (
                select(SignalLog.symbol, SignalLog.signal)
                .join(
                    subq,
                    (SignalLog.symbol == subq.c.symbol)
                    & (SignalLog.generated_at == subq.c.max_ts),
                )
            )
            rows = (await session.execute(stmt)).all()

        if not rows:
            logger.info("warm_prev_signals: no signals in DB yet — starting fresh")
            return

        for row in rows:
            engine._prev_signals[row.symbol] = row.signal

        logger.info(
            "warm_prev_signals: restored %d symbol states into _prev_signals",
            len(rows),
        )

    async def warm_volume_baselines(self, volume_strategy: "VolumeSpikeStrategy") -> None:
        """
        Pre-fill VolumeSpikeStrategy's per-symbol rolling volume buffer from
        the most recent intraday_ticks rows.

        Uses last 20 non-null, non-zero volume readings per symbol within a
        7-day lookback window.  Falls back to price_history if intraday_ticks
        is empty.
        """
        symbols = await self.get_available_symbols()
        if not symbols:
            logger.info("warm_volume_baselines: no history in DB yet — skipping")
            return

        cutoff = int(time.time()) - (7 * 24 * 3600)

        # Try intraday_ticks first
        async with get_session() as session:
            q = (
                select(IntradayTick.symbol, IntradayTick.volume)
                .where(IntradayTick.symbol.in_(symbols))
                .where(IntradayTick.scraped_at >= cutoff)
                .where(IntradayTick.volume.isnot(None))
                .where(IntradayTick.volume > 0)
                .order_by(IntradayTick.symbol, IntradayTick.scraped_at.desc())
            )
            result = await session.execute(q)
            all_rows = result.all()

        if not all_rows:
            # Fall back to legacy
            async with get_session() as session:
                q = (
                    select(PriceHistory.symbol, PriceHistory.volume)
                    .where(PriceHistory.symbol.in_(symbols))
                    .where(PriceHistory.scraped_at >= cutoff)
                    .where(PriceHistory.volume.isnot(None))
                    .where(PriceHistory.volume > 0)
                    .order_by(PriceHistory.symbol, PriceHistory.scraped_at.desc())
                )
                result = await session.execute(q)
                all_rows = result.all()

        by_symbol: dict[str, list[int]] = {}
        for row in all_rows:
            lst = by_symbol.setdefault(row.symbol, [])
            if len(lst) < 20:
                lst.append(row.volume)

        total = 0
        for symbol, volumes in by_symbol.items():
            for vol in reversed(volumes):   # oldest-first into rolling buffer
                volume_strategy.push_volume(symbol, vol)
            total += len(volumes)

        logger.info(
            "warm_volume_baselines: loaded %d volume readings across %d symbols",
            total, len(by_symbol),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get_intraday_rows(
        self,
        symbol: str,
        n: int = 200,
        since: Optional[int] = None,
    ) -> list[dict]:
        """Fetch from intraday_ticks, oldest-first."""
        async with get_session() as session:
            q = select(IntradayTick).where(IntradayTick.symbol == symbol)
            if since:
                q = q.where(IntradayTick.scraped_at >= since)
            q = q.order_by(IntradayTick.scraped_at.desc()).limit(n)
            result = await session.execute(q)
            rows = result.scalars().all()
        return [_intraday_row_to_dict(r) for r in reversed(rows)]

    async def _get_legacy_rows(
        self,
        symbol: str,
        n: int = 200,
        since: Optional[int] = None,
    ) -> list[dict]:
        """Fallback: fetch from price_history, oldest-first."""
        async with get_session() as session:
            q = select(PriceHistory).where(PriceHistory.symbol == symbol)
            if since:
                q = q.where(PriceHistory.scraped_at >= since)
            q = q.order_by(PriceHistory.scraped_at.desc()).limit(n)
            result = await session.execute(q)
            rows = result.scalars().all()
        return [_legacy_row_to_dict(r) for r in reversed(rows)]

    async def _get_legacy_eod_rows(
        self,
        symbol: str,
        n: int = 500,
        start_ts: Optional[int] = None,
        end_ts:   Optional[int] = None,
    ) -> list[dict]:
        """Fallback: fetch EOD rows from price_history where source='historical'."""
        async with get_session() as session:
            q = (
                select(PriceHistory)
                .where(PriceHistory.symbol == symbol)
                .where(PriceHistory.source == "historical")
            )
            if start_ts:
                q = q.where(PriceHistory.scraped_at >= start_ts)
            if end_ts:
                q = q.where(PriceHistory.scraped_at <= end_ts)
            q = q.order_by(PriceHistory.scraped_at.asc()).limit(n)
            result = await session.execute(q)
            rows = result.scalars().all()
        return [_legacy_row_to_dict(r) for r in rows]


# ------------------------------------------------------------------
# Private row-conversion helpers
# ------------------------------------------------------------------

def _stock_to_intraday_row(s: dict) -> dict:
    """Convert a scraper stock dict → IntradayTick column dict."""
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


def _stock_to_eod_row(s: dict) -> dict:
    """Convert a stock dict → EODPrice column dict."""
    ts = int(s.get("timestamp", time.time()))
    return {
        "symbol":     s.get("symbol", "").upper(),
        "date_key":   _ts_to_date_key(ts),
        "sector":     s.get("sector"),
        "open_price": s.get("open"),
        "high":       s.get("high"),
        "low":        s.get("low"),
        "close":      s.get("current", s.get("close", 0.0)),
        "volume":     s.get("volume"),
        "change_pct": s.get("change_pct"),
        "ldcp":       s.get("ldcp"),
        "scraped_at": ts,
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
        "horizon":        "default",   # DB column kept; value no longer signal-derived
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


def _intraday_row_to_dict(row: IntradayTick) -> dict:
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


def _eod_row_to_dict(row: EODPrice) -> dict:
    return {
        "id":         row.id,
        "symbol":     row.symbol,
        "date_key":   row.date_key,
        "sector":     row.sector,
        "open":       row.open_price,
        "high":       row.high,
        "low":        row.low,
        "close":      row.close,
        "volume":     row.volume,
        "change_pct": row.change_pct,
        "ldcp":       row.ldcp,
        "scraped_at": row.scraped_at,
    }


def _legacy_row_to_dict(row: PriceHistory) -> dict:
    """Shared converter for PriceHistory fallback rows."""
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
