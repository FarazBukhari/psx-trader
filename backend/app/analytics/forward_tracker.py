"""
Forward Testing Engine — tracks live signal performance in real market conditions.

NOT backtesting. This observes what happens AFTER a signal fires.

Design contract
---------------
* Stateless + DB-driven: safe to restart — OPEN trades persist across restarts.
* Called from the poll loop via asyncio.create_task() — never blocks the loop.
* All heavy DB work is batched per tick (one SELECT for all OPEN trades, one
  bulk UPDATE) to stay inside the existing latency budget.
* Idempotency: UNIQUE(symbol, entry_time) prevents duplicate entries from a
  repeated signal at the same timestamp.

Exit thresholds
---------------
  TAKE_PROFIT_PCT = 2.0   → BUY: price >= entry×1.02  / SELL: price <= entry×0.98
  STOP_LOSS_PCT   = 1.5   → BUY: price <= entry×0.985 / SELL: price >= entry×1.015
  MAX_HOLD        = 1 trading day (checked via market_hours session boundaries)

Outcome classification (applied at close)
-----------------------------------------
  WIN     → pnl_pct >  0.3
  LOSS    → pnl_pct < -0.3
  NEUTRAL → otherwise

MFE/MAE  (computed from max_price_seen / min_price_seen at close)
-----------------------------------------------------------------
  BUY:  mfe = (max - entry) / entry * 100
        mae = (min - entry) / entry * 100   ← typically negative
  SELL: mfe = (entry - min) / entry * 100
        mae = (entry - max) / entry * 100   ← typically negative
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..db.database import get_session
from ..db.models import ForwardTrade
from ..strategy.signal_engine import price_buffer

logger = logging.getLogger("psx.forward_tracker")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Fallback thresholds used when price history is too shallow to compute volatility
_TP_DEFAULT = 2.0    # %
_SL_DEFAULT = 1.5    # %

# Volatility-based clamp ranges
_TP_MIN, _TP_MAX = 1.5, 5.0   # %
_SL_MIN, _SL_MAX = 1.0, 3.0   # %

ACTIONABLE = {"BUY", "SELL", "FORCE_SELL"}

# Seconds in one PSX trading day (09:30–15:30 = 6 h = 21 600 s)
TRADING_DAY_SECONDS = 6 * 3600

# Outcome boundary constants
_BREAKEVEN_BAND = 0.2   # ± % — if |pnl| ≤ this → BREAKEVEN
_WEAK_WIN_MIN   = 0.2   # pnl must exceed this to be any kind of WIN


# ---------------------------------------------------------------------------
# P3 — Volatility helpers
# ---------------------------------------------------------------------------

def _calc_volatility(prices: list[float]) -> float:
    """(max − min) / mean × 100 on the supplied price series. Returns 0 if < 2 prices."""
    if len(prices) < 2:
        return 0.0
    mn, mx = min(prices), max(prices)
    mean = sum(prices) / len(prices)
    if mean == 0:
        return 0.0
    return (mx - mn) / mean * 100


def _calc_tp_sl(symbol: str) -> tuple[float, float]:
    """
    Derive per-symbol TP / SL thresholds from recent price volatility.

    TP = volatility × 0.8, clamped to [1.5 %, 5.0 %]
    SL = volatility × 0.6, clamped to [1.0 %, 3.0 %]

    Falls back to (_TP_DEFAULT, _SL_DEFAULT) when the buffer is too shallow.
    """
    prices = price_buffer.get(symbol)        # full buffer, most recent last
    vol = _calc_volatility(prices)
    if vol == 0.0:
        return _TP_DEFAULT, _SL_DEFAULT
    tp = max(_TP_MIN, min(_TP_MAX, vol * 0.8))
    sl = max(_SL_MIN, min(_SL_MAX, vol * 0.6))
    return round(tp, 4), round(sl, 4)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _pnl_pct(signal: str, entry: float, exit_p: float) -> float:
    """Signed P&L percentage from the signal's perspective."""
    if signal == "BUY":
        return (exit_p - entry) / entry * 100
    else:  # SELL / FORCE_SELL
        return (entry - exit_p) / entry * 100


def _classify_outcome(pnl: float, tp_pct: float = _TP_DEFAULT) -> str:
    """
    Four-bucket outcome classification (P8).

    STRONG_WIN  pnl ≥ tp_pct         (full take-profit achieved)
    WEAK_WIN    _WEAK_WIN_MIN < pnl < tp_pct
    BREAKEVEN   |pnl| ≤ _BREAKEVEN_BAND
    LOSS        pnl < −_BREAKEVEN_BAND
    """
    if pnl >= tp_pct:
        return "STRONG_WIN"
    if pnl > _WEAK_WIN_MIN:
        return "WEAK_WIN"
    if pnl >= -_BREAKEVEN_BAND:
        return "BREAKEVEN"
    return "LOSS"


def _compute_mfe_mae(
    signal: str,
    entry: float,
    max_seen: float,
    min_seen: float,
) -> tuple[float, float]:
    """Returns (mfe_pct, mae_pct). MFE is positive; MAE is negative."""
    if signal == "BUY":
        mfe = (max_seen - entry) / entry * 100
        mae = (min_seen - entry) / entry * 100
    else:
        mfe = (entry - min_seen) / entry * 100
        mae = (entry - max_seen) / entry * 100
    return round(mfe, 4), round(mae, 4)


def _tp_hit(signal: str, entry: float, current: float, tp_pct: float) -> bool:
    if signal == "BUY":
        return current >= entry * (1 + tp_pct / 100)
    return current <= entry * (1 - tp_pct / 100)


def _sl_hit(signal: str, entry: float, current: float, sl_pct: float) -> bool:
    if signal == "BUY":
        return current <= entry * (1 - sl_pct / 100)
    return current >= entry * (1 + sl_pct / 100)


def _time_exit(entry_time: int, now_ts: int) -> bool:
    """True if the trade has been open for more than one trading day's worth of seconds."""
    return (now_ts - entry_time) >= TRADING_DAY_SECONDS


# ---------------------------------------------------------------------------
# Entry creation  (called once per new actionable signal)
# ---------------------------------------------------------------------------

async def create_trade_on_signal(signal_dict: dict) -> None:
    """
    Insert a new ForwardTrade row when a BUY / SELL / FORCE_SELL signal fires.

    Guards (applied in order):
      Actionable check — signal must be BUY / SELL / FORCE_SELL
      Stale guard      — skip if data is stale (main.py already filters; defence-in-depth)
      P1               — skip if an OPEN trade already exists for this symbol
      UNIQUE constraint — final idempotency backstop via ON CONFLICT DO NOTHING

    Intentionally NOT filtered by signal_changed or confidence:
      • signal_changed resets to False on every server restart (in-memory state), so
        signals that are already active at startup would never create a trade.
      • Deduplication is handled correctly by the P1 OPEN-trade existence check.
      • confidence is None for most signals (prediction engine is advisory); filtering
        on it would suppress virtually all forward trades.

    Args:
        signal_dict: one enriched signal dict from the poll loop
                     Required keys: symbol, signal, current
                     Optional: generated_at, stale
    """
    sig = signal_dict.get("signal", "").upper()
    if sig not in ACTIONABLE:
        return

    symbol = signal_dict["symbol"]

    # Stale guard — defence-in-depth (main.py only calls us on live ticks)
    if signal_dict.get("stale", False):
        logger.debug("forward_tracker: stale data for %s — skipping", symbol)
        return

    price      = signal_dict.get("current") or signal_dict.get("price")
    entry_time = signal_dict.get("generated_at") or int(time.time())

    if not price:
        logger.warning("forward_tracker: no price for %s %s — skipping", sig, symbol)
        return

    try:
        async with get_session() as session:
            # P1 — only one OPEN trade per symbol at a time
            existing = await session.execute(
                select(ForwardTrade.id).where(
                    ForwardTrade.symbol == symbol,
                    ForwardTrade.status == "OPEN",
                ).limit(1)
            )
            if existing.scalar_one_or_none() is not None:
                logger.debug(
                    "forward_tracker: OPEN trade already exists for %s — skipping", symbol
                )
                return

            stmt = (
                sqlite_insert(ForwardTrade)
                .values(
                    symbol           = symbol,
                    signal           = sig,
                    entry_price      = price,
                    entry_time       = entry_time,
                    max_price_seen   = price,
                    min_price_seen   = price,
                    status           = "OPEN",
                    outcome          = "BREAKEVEN",
                    mfe_pct          = 0.0,
                    mae_pct          = 0.0,
                    duration_minutes = 0.0,
                )
                .on_conflict_do_nothing(index_elements=["symbol", "entry_time"])
            )
            await session.execute(stmt)
        logger.info("ForwardTrade created: %s %s @ %.4f", sig, symbol, price)
    except Exception as exc:
        logger.warning("forward_tracker create_trade failed for %s: %s", symbol, exc)


# ---------------------------------------------------------------------------
# Tick update  (called every poll cycle for ALL symbols in one batch)
# ---------------------------------------------------------------------------

async def update_open_trades(stocks: list[dict], signals: list[dict] | None = None) -> None:
    """
    Batch-update all OPEN forward trades for this tick.

    Algorithm (per-trade, evaluated in Python after a single SELECT):
      1. P3 — compute per-symbol volatility-based TP / SL thresholds.
      2. Update max_price_seen / min_price_seen.
      3. P2 — if current signal is OPPOSITE direction, close immediately.
      4. Check TP / SL / time-exit with dynamic thresholds.
      5. P5 — compute duration_minutes on close.
      6. P8 — apply four-bucket outcome classification.

    No per-trade queries.  One SELECT for all OPEN trades, then minimal UPDATEs.

    Args:
        stocks:  tick data list (required — provides prices)
        signals: current signal list from the engine (optional — enables P2 opposite-close)
    """
    if not stocks:
        return

    # Build symbol→price lookup from this tick
    price_map: dict[str, float] = {
        s["symbol"]: s["current"]
        for s in stocks
        if s.get("current") is not None
    }
    if not price_map:
        return

    # P2 — build symbol→current-signal map (present only when caller passes signals)
    signal_map: dict[str, str] = {
        s["symbol"]: s.get("signal", "").upper()
        for s in (signals or [])
        if s.get("symbol")
    }

    now_ts = int(time.time())

    try:
        async with get_session() as session:
            # ── Fetch all OPEN trades for symbols present in this tick ──────
            result = await session.execute(
                select(ForwardTrade).where(
                    ForwardTrade.status == "OPEN",
                    ForwardTrade.symbol.in_(list(price_map.keys())),
                )
            )
            open_trades: list[ForwardTrade] = list(result.scalars().all())

            if not open_trades:
                return

            trades_to_close: list[ForwardTrade] = []
            trades_to_update: list[ForwardTrade] = []

            for trade in open_trades:
                current = price_map.get(trade.symbol)
                if current is None:
                    continue

                # P3 — per-symbol dynamic TP / SL
                tp_pct, sl_pct = _calc_tp_sl(trade.symbol)

                # Update extremes
                new_max = max(trade.max_price_seen, current)
                new_min = min(trade.min_price_seen, current)

                # P2 — opposite-signal close
                cur_signal = signal_map.get(trade.symbol, "")
                opposite_close = False
                if cur_signal in ACTIONABLE:
                    if trade.signal == "BUY" and cur_signal in ("SELL", "FORCE_SELL"):
                        opposite_close = True
                    elif trade.signal in ("SELL", "FORCE_SELL") and cur_signal == "BUY":
                        opposite_close = True

                # Check all exit conditions
                exit_triggered = (
                    opposite_close
                    or _tp_hit(trade.signal, trade.entry_price, current, tp_pct)
                    or _sl_hit(trade.signal, trade.entry_price, current, sl_pct)
                    or _time_exit(trade.entry_time, now_ts)
                )

                if exit_triggered:
                    trade.max_price_seen   = new_max
                    trade.min_price_seen   = new_min
                    trade.exit_price       = current
                    trade.exit_time        = now_ts
                    trade.status           = "CLOSED"
                    # P5 — duration
                    trade.duration_minutes = round((now_ts - trade.entry_time) / 60, 2)

                    pnl = _pnl_pct(trade.signal, trade.entry_price, current)
                    # P8 — four-bucket classification
                    trade.outcome = _classify_outcome(pnl, tp_pct)
                    trade.mfe_pct, trade.mae_pct = _compute_mfe_mae(
                        trade.signal,
                        trade.entry_price,
                        new_max,
                        new_min,
                    )
                    trades_to_close.append(trade)
                else:
                    trade.max_price_seen = new_max
                    trade.min_price_seen = new_min
                    trades_to_update.append(trade)

            # All mutations sit on ORM objects tracked by the session;
            # get_session()'s commit on exit flushes them all in one round-trip.
            if trades_to_close:
                syms = [f"{t.symbol}→{t.outcome}" for t in trades_to_close]
                logger.info("ForwardTrade closed: %s", ", ".join(syms))

    except Exception as exc:
        logger.warning("forward_tracker update_open_trades failed: %s", exc)


# ---------------------------------------------------------------------------
# Startup recovery — re-open trades that were OPEN when the server stopped
# ---------------------------------------------------------------------------

async def recover_open_trades() -> None:
    """
    Called once at startup.  Fetches OPEN trades whose entry_time is older than
    one trading day and force-closes them at null price (we have no exit price).
    Trades within the window are left OPEN and will close naturally.
    """
    now_ts  = int(time.time())
    cutoff  = now_ts - TRADING_DAY_SECONDS

    try:
        async with get_session() as session:
            result = await session.execute(
                select(ForwardTrade).where(
                    ForwardTrade.status == "OPEN",
                    ForwardTrade.entry_time < cutoff,
                )
            )
            stale: list[ForwardTrade] = list(result.scalars().all())
            for trade in stale:
                # Close at last-seen price (entry_price is best we have)
                fallback = trade.exit_price or trade.entry_price
                pnl      = _pnl_pct(trade.signal, trade.entry_price, fallback)
                mfe, mae = _compute_mfe_mae(
                    trade.signal,
                    trade.entry_price,
                    trade.max_price_seen,
                    trade.min_price_seen,
                )
                trade.exit_price       = fallback
                trade.exit_time        = now_ts
                trade.status           = "CLOSED"
                # P8 — four-bucket; use default tp for recovery (no live price buffer)
                trade.outcome          = _classify_outcome(pnl)
                trade.mfe_pct          = mfe
                trade.mae_pct          = mae
                # P5 — duration from persisted timestamps
                trade.duration_minutes = round((now_ts - trade.entry_time) / 60, 2)

            if stale:
                logger.info(
                    "forward_tracker recovery: force-closed %d stale OPEN trades", len(stale)
                )
    except Exception as exc:
        logger.warning("forward_tracker recovery failed: %s", exc)


# ---------------------------------------------------------------------------
# Read helpers  (used by performance_routes.py)
# ---------------------------------------------------------------------------

async def get_open_trades() -> list[dict]:
    """Return all OPEN forward trades as dicts, newest-first."""
    try:
        async with get_session() as session:
            result = await session.execute(
                select(ForwardTrade)
                .where(ForwardTrade.status == "OPEN")
                .order_by(ForwardTrade.entry_time.desc())
            )
            trades = result.scalars().all()
            return [_to_dict(t) for t in trades]
    except Exception as exc:
        logger.warning("forward_tracker get_open_trades failed: %s", exc)
        return []


async def get_closed_trades(
    limit: int = 50,
    offset: int = 0,
    symbol: Optional[str] = None,
) -> tuple[list[dict], int]:
    """
    Return paginated CLOSED trades (newest-first) and total count.
    Optionally filtered by symbol.
    """
    from sqlalchemy import func

    try:
        async with get_session() as session:
            base_where = [ForwardTrade.status == "CLOSED"]
            if symbol:
                base_where.append(ForwardTrade.symbol == symbol.upper())

            # Total count
            count_result = await session.execute(
                select(func.count()).select_from(ForwardTrade).where(*base_where)
            )
            total = count_result.scalar_one()

            # Paginated rows
            rows_result = await session.execute(
                select(ForwardTrade)
                .where(*base_where)
                .order_by(ForwardTrade.exit_time.desc())
                .limit(limit)
                .offset(offset)
            )
            trades = rows_result.scalars().all()
            return [_to_dict(t) for t in trades], total
    except Exception as exc:
        logger.warning("forward_tracker get_closed_trades failed: %s", exc)
        return [], 0


async def get_performance_summary() -> dict:
    """
    Aggregate forward-test performance across all CLOSED trades.

    Returns:
        win_rate      — % of closed trades that are WINs
        avg_win_pct   — average P&L % of winning trades
        avg_loss_pct  — average P&L % of losing trades (negative)
        expectancy    — win_rate × avg_win − (1−win_rate) × |avg_loss|
        avg_mfe       — average max favourable excursion %
        avg_mae       — average max adverse excursion %
        total_closed  — total CLOSED trades evaluated
        total_open    — total currently OPEN trades
    """
    from sqlalchemy import func

    try:
        async with get_session() as session:
            # All CLOSED trades
            result = await session.execute(
                select(ForwardTrade).where(ForwardTrade.status == "CLOSED")
            )
            closed: list[ForwardTrade] = list(result.scalars().all())

            open_count_res = await session.execute(
                select(func.count()).select_from(ForwardTrade).where(ForwardTrade.status == "OPEN")
            )
            open_count = open_count_res.scalar_one()

        total = len(closed)
        if total == 0:
            return {
                "win_rate":              0.0,
                "avg_win_pct":           0.0,
                "avg_loss_pct":          0.0,
                "expectancy":            0.0,
                "avg_mfe":               0.0,
                "avg_mae":               0.0,
                "avg_return_per_trade":  0.0,
                "avg_return_per_hour":   0.0,
                "total_closed":          0,
                "total_open":            open_count,
            }

        # P8 — wins = STRONG_WIN + WEAK_WIN; losses = LOSS; BREAKEVEN is neutral
        wins   = [t for t in closed if t.outcome in ("STRONG_WIN", "WEAK_WIN")]
        losses = [t for t in closed if t.outcome == "LOSS"]

        win_rate = round(len(wins) / total * 100, 2)
        avg_win  = round(
            sum(_pnl_pct(t.signal, t.entry_price, t.exit_price) for t in wins) / len(wins), 4
        ) if wins else 0.0
        avg_loss = round(
            sum(_pnl_pct(t.signal, t.entry_price, t.exit_price) for t in losses) / len(losses), 4
        ) if losses else 0.0
        avg_mfe  = round(sum(t.mfe_pct for t in closed) / total, 4)
        avg_mae  = round(sum(t.mae_pct for t in closed) / total, 4)

        wr_frac    = win_rate / 100
        expectancy = round(wr_frac * avg_win - (1 - wr_frac) * abs(avg_loss), 4)

        # P6 — normalised metrics
        all_pnls = [_pnl_pct(t.signal, t.entry_price, t.exit_price) for t in closed]
        avg_return_per_trade = round(sum(all_pnls) / total, 4)

        # avg_return_per_hour: exclude zero-duration trades to avoid division by zero
        per_hour_samples = [
            pnl / (t.duration_minutes / 60)
            for t, pnl in zip(closed, all_pnls)
            if t.duration_minutes > 0
        ]
        avg_return_per_hour = round(
            sum(per_hour_samples) / len(per_hour_samples), 4
        ) if per_hour_samples else 0.0

        return {
            "win_rate":              win_rate,
            "avg_win_pct":           avg_win,
            "avg_loss_pct":          avg_loss,
            "expectancy":            expectancy,
            "avg_mfe":               avg_mfe,
            "avg_mae":               avg_mae,
            "avg_return_per_trade":  avg_return_per_trade,
            "avg_return_per_hour":   avg_return_per_hour,
            "total_closed":          total,
            "total_open":            open_count,
        }
    except Exception as exc:
        logger.warning("forward_tracker get_performance_summary failed: %s", exc)
        return {
            "win_rate": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
            "expectancy": 0.0, "avg_mfe": 0.0, "avg_mae": 0.0,
            "avg_return_per_trade": 0.0, "avg_return_per_hour": 0.0,
            "total_closed": 0, "total_open": 0,
        }


# ---------------------------------------------------------------------------
# Serialiser
# ---------------------------------------------------------------------------

def _to_dict(t: ForwardTrade) -> dict:
    return {
        "id":               t.id,
        "symbol":           t.symbol,
        "signal":           t.signal,
        "entry_price":      t.entry_price,
        "entry_time":       t.entry_time,
        "max_price_seen":   t.max_price_seen,
        "min_price_seen":   t.min_price_seen,
        "exit_price":       t.exit_price,
        "exit_time":        t.exit_time,
        "status":           t.status,
        "outcome":          t.outcome,
        "mfe_pct":          t.mfe_pct,
        "mae_pct":          t.mae_pct,
        "duration_minutes": t.duration_minutes,
    }
