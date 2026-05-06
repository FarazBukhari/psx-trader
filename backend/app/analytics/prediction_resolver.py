"""
Prediction Outcome Resolver — closes the feedback loop on prediction_log.

Resolves prediction_log.outcome by comparing predicted direction against
the actual price at the predicted horizon timestamp.

Resolution logic
----------------
  target_ts = predicted_at + (time_horizon_days × 86 400)

  Forward price lookup: first price_history tick WHERE scraped_at >= target_ts

  direction == "up"  → correct   if price_future > price_at_prediction
                        neutral   if |change| ≤ 0.2 %
                        incorrect otherwise
  direction == "down"→ correct   if price_future < price_at_prediction
                        neutral   if |change| ≤ 0.2 %
                        incorrect otherwise

Eligibility (all must be true):
  1. outcome == "pending"
  2. predicted_direction IS NOT NULL
  3. time_horizon_days IS NOT NULL AND > 0
  4. predicted_at + time_horizon_days × 86 400 <= now_ts

Batched. Idempotent. Safe to run repeatedly.
"""

from __future__ import annotations

import bisect
import logging
import time
from typing import Optional

from sqlalchemy import select

from ..db.database import get_session
from ..db.models import PredictionLog, PriceHistory

logger = logging.getLogger("psx.prediction_resolver")

_SEARCH_WINDOW_S = 24 * 3600   # search up to 24 h forward for a matching tick
_NEUTRAL_BAND    = 0.2          # ±% — classify as neutral if price barely moved


# ---------------------------------------------------------------------------
# Outcome classifier
# ---------------------------------------------------------------------------

def _classify(
    direction: str,
    price_now: Optional[float],
    price_future: Optional[float],
) -> Optional[str]:
    """
    Return 'correct', 'incorrect', 'neutral', or None (no data yet).
    None means the price hasn't arrived — row stays pending.
    """
    if price_future is None or price_now is None or price_now <= 0:
        return None

    change_pct = (price_future - price_now) / price_now * 100.0

    if abs(change_pct) <= _NEUTRAL_BAND:
        return "neutral"

    if direction == "up":
        return "correct" if change_pct > 0 else "incorrect"
    if direction == "down":
        return "correct" if change_pct < 0 else "incorrect"

    return None  # unknown direction


# ---------------------------------------------------------------------------
# Main resolution function
# ---------------------------------------------------------------------------

async def resolve_pending_predictions(batch_size: int = 200) -> int:
    """
    Resolve pending prediction_log rows whose time horizon has elapsed.

    Returns count of rows updated (outcome set to correct/incorrect/neutral).
    Rows whose forward price is not yet available remain 'pending'.
    """
    now_ts = int(time.time())

    async with get_session() as session:

        # ── Step 1: fetch pending rows with a defined horizon ──────────────
        stmt = (
            select(PredictionLog)
            .where(PredictionLog.outcome == "pending")
            .where(PredictionLog.predicted_direction.isnot(None))
            .where(PredictionLog.time_horizon_days.isnot(None))
            .where(PredictionLog.time_horizon_days > 0)
            .order_by(PredictionLog.predicted_at.asc())
            .limit(batch_size)
        )
        all_rows = list((await session.execute(stmt)).scalars().all())

        if not all_rows:
            logger.debug("prediction_resolver: no pending predictions found")
            return 0

        # Filter: only those whose horizon has actually elapsed
        eligible = [
            r for r in all_rows
            if (r.predicted_at + r.time_horizon_days * 86_400) <= now_ts
        ]

        if not eligible:
            logger.debug("prediction_resolver: no horizons elapsed yet")
            return 0

        logger.info(
            "prediction_resolver: %d eligible rows (of %d pending)",
            len(eligible), len(all_rows),
        )

        # ── Step 2: batch-fetch forward prices (one query per symbol) ──────
        # Same bisect pattern as signal_evaluator for O(N) instead of O(N×T).
        by_symbol: dict[str, list[tuple[int, int]]] = {}  # sym → [(pred_id, target_ts)]
        for r in eligible:
            target_ts = r.predicted_at + r.time_horizon_days * 86_400
            by_symbol.setdefault(r.symbol, []).append((r.id, target_ts))

        # pred_id → (outcome_price, actual_scraped_at)
        price_map: dict[int, tuple[Optional[float], Optional[int]]] = {}

        for sym, id_ts_pairs in by_symbol.items():
            ts_list = [ts for _, ts in id_ts_pairs]
            lo      = min(ts_list)
            hi      = max(ts_list) + _SEARCH_WINDOW_S

            ph_stmt = (
                select(PriceHistory.scraped_at, PriceHistory.close)
                .where(PriceHistory.symbol == sym)
                .where(PriceHistory.scraped_at.between(lo, hi))
                .order_by(PriceHistory.scraped_at.asc())
            )
            ph_rows = (await session.execute(ph_stmt)).all()

            if not ph_rows:
                for pred_id, _ in id_ts_pairs:
                    price_map[pred_id] = (None, None)
                continue

            ats    = [row.scraped_at for row in ph_rows]
            closes = [row.close      for row in ph_rows]

            for pred_id, target_ts in id_ts_pairs:
                idx = bisect.bisect_left(ats, target_ts)
                if idx < len(ats):
                    price_map[pred_id] = (closes[idx], ats[idx])
                else:
                    price_map[pred_id] = (None, None)

        # ── Step 3: update resolved rows ───────────────────────────────────
        updated = 0
        for r in eligible:
            outcome_price, outcome_at = price_map.get(r.id, (None, None))
            outcome = _classify(r.predicted_direction, r.price_at_prediction, outcome_price)

            if outcome is None:
                continue  # Forward price not yet available — leave pending

            r.outcome       = outcome
            r.outcome_price = outcome_price
            r.outcome_at    = outcome_at
            updated += 1

        logger.info("prediction_resolver: resolved %d prediction outcomes", updated)
        return updated
