"""
Signal Validation Engine — evaluates historical signals against forward price data.

Time horizons (timestamp-based, NOT tick-based):
  short  → signal_ts + 1800  seconds  (30 minutes)
  medium → signal_ts + 7200  seconds  (2 hours)
  long   → session-based:
             if (15:30 PKT − signal_time) < 2 hours  →  first price after 09:30 next trading day
             else                                     →  first price at or after 15:30 PKT same day

Price lookup pattern (always a forward-price SELECT, LIMIT 1):
  SELECT scraped_at, close FROM price_history
  WHERE  symbol = :sym AND scraped_at >= :target_ts
  ORDER  BY scraped_at ASC LIMIT 1

price_at_signal is fetched explicitly (NOT taken from signals_log.price):
  SELECT scraped_at, close FROM price_history
  WHERE  symbol = :sym AND scraped_at <= :signal_ts
  ORDER  BY scraped_at DESC LIMIT 1

Outcome logic:
  BUY        → correct if future > now,  incorrect if future < now,  neutral if |Δ| ≤ 0.2%
  SELL       → correct if future < now,  incorrect if future > now
  FORCE_SELL → same as SELL
  HOLD       → correct if |Δ| < 0.5%,   incorrect otherwise
  Any horizon where price data is not yet available → NULL (filled progressively on next run)

Progressive filling:
  Outcomes are stored as NULL (not 'pending') when price data is unavailable.
  Each run updates only the NULL fields using COALESCE upsert:
    ON CONFLICT(symbol, timestamp) DO UPDATE SET
      field = COALESCE(existing_value, new_value)
  This means once a field is resolved it is never overwritten.
  evaluated_at is always updated to the latest run timestamp.

All DB work is batched — no N+1 queries.
UNIQUE(symbol, timestamp) enforces idempotency.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..db.database import get_session
from ..db.models import PriceHistory, SignalLog, SignalOutcome
from ..market_hours import PKT, _next_business_open

logger = logging.getLogger("psx.signal_evaluator")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SHORT_OFFSET_S   = 1800   # 30 minutes
MEDIUM_OFFSET_S  = 7200   # 2 hours
LONG_THRESHOLD_S = 7200   # 2h before 15:30 PKT → use next session open instead

SESSION_CLOSE_HOUR   = 15
SESSION_CLOSE_MINUTE = 30


# ---------------------------------------------------------------------------
# Horizon helpers
# ---------------------------------------------------------------------------

def _long_target_ts(signal_ts: int) -> int:
    """
    Return the Unix timestamp of the long-horizon price target.

    Rule:
      if (15:30 PKT - signal_time) < 2 hours  →  next session open (09:30 next trading day)
      else                                     →  15:30 PKT same day as signal
    """
    signal_dt = datetime.fromtimestamp(signal_ts, tz=PKT)
    session_close = signal_dt.replace(
        hour=SESSION_CLOSE_HOUR,
        minute=SESSION_CLOSE_MINUTE,
        second=0,
        microsecond=0,
    )
    time_to_close = (session_close - signal_dt).total_seconds()

    if time_to_close < LONG_THRESHOLD_S:
        # Less than 2 hours to session close (or already past it) → next trading day open
        next_open = _next_business_open(signal_dt, skip_today=True)
        return int(next_open.timestamp())
    else:
        # Use 15:30 PKT same day
        return int(session_close.timestamp())


# ---------------------------------------------------------------------------
# Outcome classifier
# ---------------------------------------------------------------------------

def _classify(signal: str, price_now: Optional[float], price_future: Optional[float]) -> Optional[str]:
    """
    Return 'correct', 'incorrect', 'neutral', or None.

    None means price data is not yet available — stored as SQL NULL so COALESCE
    upserts can fill it in on a later run without overwriting resolved values.
    """
    if price_future is None or price_now is None or price_now == 0:
        return None   # ← NULL in DB, not the string 'pending'

    change_pct = (price_future - price_now) / price_now * 100.0
    sig = signal.upper()

    if sig == "BUY":
        if abs(change_pct) <= 0.2:
            return "neutral"
        return "correct" if change_pct > 0 else "incorrect"

    elif sig in ("SELL", "FORCE_SELL"):
        if change_pct < 0:
            return "correct"
        elif change_pct > 0:
            return "incorrect"
        else:
            return "neutral"

    elif sig == "HOLD":
        return "correct" if abs(change_pct) < 0.5 else "incorrect"

    # Unknown signal type → unresolvable
    return None


# ---------------------------------------------------------------------------
# Batch price fetcher
# ---------------------------------------------------------------------------

PricePoint = tuple[Optional[float], Optional[int]]   # (close, actual_scraped_at)


async def _fetch_prices_batch(
    session,
    symbol_ts_pairs: list[tuple[str, int]],
    direction: str,  # "forward" (>=) or "backward" (<=)
) -> dict[tuple[str, int], PricePoint]:
    """
    For each (symbol, target_ts) pair, fetch a single (close, actual_scraped_at)
    from price_history.

    direction="forward"  → first close WHERE scraped_at >= target_ts  ORDER BY ASC  LIMIT 1
    direction="backward" → last  close WHERE scraped_at <= target_ts  ORDER BY DESC LIMIT 1

    Returns dict keyed by (symbol, target_ts) → (close, actual_scraped_at) or (None, None).
    The actual_scraped_at enables latency calculation: actual_scraped_at - target_ts.
    """
    if not symbol_ts_pairs:
        return {}

    result: dict[tuple[str, int], PricePoint] = {p: (None, None) for p in symbol_ts_pairs}

    # Group by symbol to minimise query round-trips
    by_symbol: dict[str, list[int]] = {}
    for sym, ts in symbol_ts_pairs:
        by_symbol.setdefault(sym, []).append(ts)

    for sym, ts_list in by_symbol.items():
        for ts in ts_list:
            if direction == "forward":
                stmt = (
                    select(PriceHistory.scraped_at, PriceHistory.close)
                    .where(PriceHistory.symbol == sym)
                    .where(PriceHistory.scraped_at >= ts)
                    .order_by(PriceHistory.scraped_at.asc())
                    .limit(1)
                )
            else:
                stmt = (
                    select(PriceHistory.scraped_at, PriceHistory.close)
                    .where(PriceHistory.symbol == sym)
                    .where(PriceHistory.scraped_at <= ts)
                    .order_by(PriceHistory.scraped_at.desc())
                    .limit(1)
                )
            row = (await session.execute(stmt)).first()
            if row:
                result[(sym, ts)] = (row.close, row.scraped_at)

    return result


def _latency(actual_scraped_at: Optional[int], target_ts: int) -> Optional[int]:
    """
    Seconds between the target horizon timestamp and the actual tick that was found.
    Positive = data arrived after target (normal). Zero = exact match.
    Returns None if no price was found.
    """
    if actual_scraped_at is None:
        return None
    return max(0, actual_scraped_at - target_ts)


# ---------------------------------------------------------------------------
# Main evaluation function
# ---------------------------------------------------------------------------

async def evaluate_pending_signals(batch_size: int = 500) -> int:
    """
    Progressively evaluate signals and persist outcomes to signal_outcomes.

    Behaviour:
    - First pass: inserts a new row for any signal whose short horizon (30m) has elapsed.
      Fields for horizons not yet elapsed are stored as NULL.
    - Subsequent passes: fills in NULL price/outcome/latency fields as their horizons
      elapse, using COALESCE upsert so resolved values are never overwritten.
    - Only fetches prices for horizons whose target_ts <= now (no wasted queries).
    - Returns count of rows written (insert + update combined).
    """
    now_ts = int(time.time())

    async with get_session() as session:
        # ── Step 1: fetch signals that need work ───────────────────────────
        #
        # A signal needs work if:
        #   a) No row in signal_outcomes yet                        (new)
        #   b) An existing row has any NULL outcome field           (progressive fill)
        #
        # Minimum eligibility: short horizon (30m) must have elapsed.
        short_cutoff = now_ts - SHORT_OFFSET_S

        stmt = (
            select(
                SignalLog.id,
                SignalLog.symbol,
                SignalLog.signal,
                SignalLog.signal_sources,
                SignalLog.generated_at,
            )
            .outerjoin(
                SignalOutcome,
                (SignalLog.symbol == SignalOutcome.symbol)
                & (SignalLog.generated_at == SignalOutcome.timestamp),
            )
            .where(SignalLog.generated_at <= short_cutoff)
            .where(
                or_(
                    SignalOutcome.id.is_(None),           # case a: no row yet
                    SignalOutcome.outcome_short.is_(None),  # case b: short still NULL
                    SignalOutcome.outcome_medium.is_(None), # case b: medium still NULL
                    SignalOutcome.outcome_long.is_(None),   # case b: long still NULL
                )
            )
            .order_by(SignalLog.generated_at.asc())
            .limit(batch_size)
        )
        rows = (await session.execute(stmt)).all()

        if not rows:
            logger.debug("No signals need evaluation")
            return 0

        logger.info("Processing %d signals (new or incomplete)", len(rows))

        # ── Step 2: build target timestamps and gate by current time ──────
        #
        # Only request prices for horizons that have elapsed — avoids fetching
        # a forward price that doesn't exist yet and wasting a query.
        medium_cutoff = now_ts - MEDIUM_OFFSET_S

        backward_pairs: list[tuple[str, int]] = []
        forward_short_pairs:  list[tuple[str, int]] = []
        forward_medium_pairs: list[tuple[str, int]] = []
        forward_long_pairs:   list[tuple[str, int]] = []

        for row in rows:
            sym = row.symbol
            ts  = row.generated_at
            backward_pairs.append((sym, ts))                      # always needed

            # Short: always (caller already gated on short_cutoff)
            forward_short_pairs.append((sym, ts + SHORT_OFFSET_S))

            # Medium: only if 2h have elapsed
            if ts <= medium_cutoff:
                forward_medium_pairs.append((sym, ts + MEDIUM_OFFSET_S))

            # Long: only if target has elapsed
            long_target = _long_target_ts(ts)
            if long_target <= now_ts:
                forward_long_pairs.append((sym, long_target))

        # De-duplicate
        backward_pairs       = list(set(backward_pairs))
        forward_short_pairs  = list(set(forward_short_pairs))
        forward_medium_pairs = list(set(forward_medium_pairs))
        forward_long_pairs   = list(set(forward_long_pairs))

        # ── Step 3: batch-fetch all prices ────────────────────────────────
        backward_prices = await _fetch_prices_batch(session, backward_pairs,      "backward")
        short_prices    = await _fetch_prices_batch(session, forward_short_pairs,  "forward")
        medium_prices   = await _fetch_prices_batch(session, forward_medium_pairs, "forward")
        long_prices     = await _fetch_prices_batch(session, forward_long_pairs,   "forward")

        # ── Step 4: build upsert rows ──────────────────────────────────────
        upsert_rows: list[dict] = []
        evaluated_at = int(time.time())

        for row in rows:
            sym         = row.symbol
            ts          = row.generated_at
            sig         = row.signal
            long_target = _long_target_ts(ts)

            price_now,    _          = backward_prices.get((sym, ts),                      (None, None))
            price_short,  short_act  = short_prices.get((sym, ts + SHORT_OFFSET_S),        (None, None))
            price_medium, medium_act = medium_prices.get((sym, ts + MEDIUM_OFFSET_S),      (None, None))
            price_long,   long_act   = long_prices.get((sym, long_target),                 (None, None))

            upsert_rows.append({
                "symbol":              sym,
                "signal":              sig,
                "signal_sources":      row.signal_sources,
                "timestamp":           ts,
                "price_at_signal":     price_now,
                "price_short":         price_short,
                "price_medium":        price_medium,
                "price_long":          price_long,
                "outcome_short":       _classify(sig, price_now, price_short),
                "outcome_medium":      _classify(sig, price_now, price_medium),
                "outcome_long":        _classify(sig, price_now, price_long),
                "short_latency_sec":   _latency(short_act,  ts + SHORT_OFFSET_S),
                "medium_latency_sec":  _latency(medium_act, ts + MEDIUM_OFFSET_S),
                "long_latency_sec":    _latency(long_act,   long_target),
                "evaluated_at":        evaluated_at,
            })

        # ── Step 5: COALESCE upsert ────────────────────────────────────────
        #
        # ON CONFLICT(symbol, timestamp) DO UPDATE SET
        #   field = COALESCE(existing_value, new_value)
        #
        # Rules:
        #   - price/outcome/latency fields: COALESCE → once resolved, never overwritten
        #   - price_at_signal: same — the baseline price should never change
        #   - evaluated_at: always updated to reflect the latest run
        if upsert_rows:
            ins = sqlite_insert(SignalOutcome).values(upsert_rows)
            ins = ins.on_conflict_do_update(
                index_elements=["symbol", "timestamp"],
                set_={
                    "price_at_signal":    func.coalesce(SignalOutcome.price_at_signal,   ins.excluded.price_at_signal),
                    "price_short":        func.coalesce(SignalOutcome.price_short,        ins.excluded.price_short),
                    "price_medium":       func.coalesce(SignalOutcome.price_medium,       ins.excluded.price_medium),
                    "price_long":         func.coalesce(SignalOutcome.price_long,         ins.excluded.price_long),
                    "outcome_short":      func.coalesce(SignalOutcome.outcome_short,      ins.excluded.outcome_short),
                    "outcome_medium":     func.coalesce(SignalOutcome.outcome_medium,     ins.excluded.outcome_medium),
                    "outcome_long":       func.coalesce(SignalOutcome.outcome_long,       ins.excluded.outcome_long),
                    "short_latency_sec":  func.coalesce(SignalOutcome.short_latency_sec,  ins.excluded.short_latency_sec),
                    "medium_latency_sec": func.coalesce(SignalOutcome.medium_latency_sec, ins.excluded.medium_latency_sec),
                    "long_latency_sec":   func.coalesce(SignalOutcome.long_latency_sec,   ins.excluded.long_latency_sec),
                    "evaluated_at":       ins.excluded.evaluated_at,   # always reflect latest run
                },
            )
            await session.execute(ins)

        written = len(upsert_rows)
        logger.info("Upserted %d signal outcome rows", written)
        return written


# ---------------------------------------------------------------------------
# Aggregation helpers (used by analytics routes)
# ---------------------------------------------------------------------------

def _accuracy(outcomes: list[Optional[str]]) -> Optional[float]:
    """
    Return % correct, counting only resolved outcomes (correct/incorrect/neutral).
    NULL outcomes (not yet evaluated) are excluded from the denominator.
    Returns None if no resolved outcomes exist.
    """
    valid = [o for o in outcomes if o in ("correct", "incorrect", "neutral")]
    if not valid:
        return None
    correct = sum(1 for o in valid if o == "correct")
    return round(correct / len(valid) * 100, 1)


def _avg_latency(latencies: list[Optional[int]]) -> Optional[float]:
    """Return average latency in seconds, ignoring NULLs. None if no data."""
    valid = [x for x in latencies if x is not None]
    if not valid:
        return None
    return round(sum(valid) / len(valid), 1)


async def get_global_summary() -> dict:
    """Return aggregated accuracy metrics across all symbols and signal types."""
    async with get_session() as session:
        stmt = select(
            SignalOutcome.signal,
            SignalOutcome.outcome_short,
            SignalOutcome.outcome_medium,
            SignalOutcome.outcome_long,
            SignalOutcome.price_at_signal,
            SignalOutcome.price_short,
            SignalOutcome.price_medium,
            SignalOutcome.price_long,
            SignalOutcome.short_latency_sec,
            SignalOutcome.medium_latency_sec,
            SignalOutcome.long_latency_sec,
        )
        rows = (await session.execute(stmt)).all()

    if not rows:
        return {"total_signals": 0, "message": "No evaluated signals yet"}

    total = len(rows)

    # Per signal-type breakdown
    by_type: dict[str, dict] = {}
    for r in rows:
        sig = r.signal
        by_type.setdefault(sig, {"short": [], "medium": [], "long": []})
        by_type[sig]["short"].append(r.outcome_short)
        by_type[sig]["medium"].append(r.outcome_medium)
        by_type[sig]["long"].append(r.outcome_long)

    signal_accuracy = {
        sig: {
            "count":           len(v["short"]),
            "accuracy_short":  _accuracy(v["short"]),
            "accuracy_medium": _accuracy(v["medium"]),
            "accuracy_long":   _accuracy(v["long"]),
        }
        for sig, v in by_type.items()
    }

    # Average return % (short horizon as proxy)
    returns = []
    for r in rows:
        if r.price_at_signal and r.price_short and r.price_at_signal != 0:
            returns.append((r.price_short - r.price_at_signal) / r.price_at_signal * 100)

    return {
        "total_signals":         total,
        "accuracy_short":        _accuracy([r.outcome_short  for r in rows]),
        "accuracy_medium":       _accuracy([r.outcome_medium for r in rows]),
        "accuracy_long":         _accuracy([r.outcome_long   for r in rows]),
        "avg_return_pct":        round(sum(returns) / len(returns), 2) if returns else None,
        "avg_latency_short_sec": _avg_latency([r.short_latency_sec  for r in rows]),
        "avg_latency_medium_sec":_avg_latency([r.medium_latency_sec for r in rows]),
        "avg_latency_long_sec":  _avg_latency([r.long_latency_sec   for r in rows]),
        "by_signal_type":        signal_accuracy,
    }


async def get_symbol_summary(symbol: str) -> dict:
    """Return per-symbol accuracy breakdown."""
    sym = symbol.upper()
    async with get_session() as session:
        stmt = select(SignalOutcome).where(SignalOutcome.symbol == sym)
        rows = (await session.execute(stmt)).scalars().all()

    if not rows:
        return {"symbol": sym, "total_signals": 0, "message": "No evaluated signals for this symbol"}

    shorts  = [r.outcome_short  for r in rows]
    mediums = [r.outcome_medium for r in rows]
    longs   = [r.outcome_long   for r in rows]

    # Best/worst signal type by short accuracy
    by_type: dict[str, list] = {}
    for r in rows:
        by_type.setdefault(r.signal, []).append(r.outcome_short)

    type_acc = {sig: _accuracy(outs) for sig, outs in by_type.items() if _accuracy(outs) is not None}
    best_signal  = max(type_acc, key=type_acc.get) if type_acc else None
    worst_signal = min(type_acc, key=type_acc.get) if type_acc else None

    # Average return
    returns = []
    for r in rows:
        if r.price_at_signal and r.price_short and r.price_at_signal != 0:
            returns.append((r.price_short - r.price_at_signal) / r.price_at_signal * 100)

    return {
        "symbol":                sym,
        "total_signals":         len(rows),
        "accuracy_short":        _accuracy(shorts),
        "accuracy_medium":       _accuracy(mediums),
        "accuracy_long":         _accuracy(longs),
        "avg_return_pct":        round(sum(returns) / len(returns), 2) if returns else None,
        "best_signal":           best_signal,
        "worst_signal":          worst_signal,
        "avg_latency_short_sec": _avg_latency([r.short_latency_sec  for r in rows]),
        "avg_latency_medium_sec":_avg_latency([r.medium_latency_sec for r in rows]),
        "avg_latency_long_sec":  _avg_latency([r.long_latency_sec   for r in rows]),
    }


async def get_source_accuracy() -> dict:
    """
    Return per-indicator accuracy by deserialising signal_sources JSON.

    signal_sources is a JSON string like '["rsi", "sma_crossover"]'.
    We aggregate accuracy at each horizon per source (RSI, SMA, momentum, volume).
    NULL outcomes are excluded from accuracy calculation denominators.
    """
    async with get_session() as session:
        stmt = select(
            SignalOutcome.signal_sources,
            SignalOutcome.outcome_short,
            SignalOutcome.outcome_medium,
            SignalOutcome.outcome_long,
        ).where(SignalOutcome.signal_sources.isnot(None))
        rows = (await session.execute(stmt)).all()

    accumulator: dict[str, dict[str, list]] = {}

    for r in rows:
        try:
            sources = json.loads(r.signal_sources)
            if not isinstance(sources, list):
                continue
        except (json.JSONDecodeError, TypeError):
            continue

        for raw_src in sources:
            src = str(raw_src).lower().strip()
            # Normalise known variants
            if "sma" in src:
                src = "sma"
            elif "rsi" in src:
                src = "rsi"
            elif "momentum" in src:
                src = "momentum"
            elif "volume" in src:
                src = "volume"
            # else: keep as-is for unknown indicators

            accumulator.setdefault(src, {"short": [], "medium": [], "long": []})
            accumulator[src]["short"].append(r.outcome_short)
            accumulator[src]["medium"].append(r.outcome_medium)
            accumulator[src]["long"].append(r.outcome_long)

    result = {}
    for src, buckets in accumulator.items():
        result[src] = {
            "count":           len(buckets["short"]),
            "accuracy_short":  _accuracy(buckets["short"]),
            "accuracy_medium": _accuracy(buckets["medium"]),
            "accuracy_long":   _accuracy(buckets["long"]),
        }

    return {
        "sources":          result,
        "total_source_rows": sum(v["count"] for v in result.values()),
    }
