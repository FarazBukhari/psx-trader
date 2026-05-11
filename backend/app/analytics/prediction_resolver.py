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
from ..db.models import IntradayTick, PredictionLog, PriceHistory

logger = logging.getLogger("psx.prediction_resolver")

_SEARCH_WINDOW_S  = 24 * 3600   # search up to 24 h forward for a matching tick
_NEUTRAL_BAND     = 0.2          # ±% — classify as neutral if price barely moved
_EXPIRE_MULTIPLIER = 3           # mark "expired" after 3× the horizon has passed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _horizon_bucket(days: int) -> str:
    """Classify time_horizon_days into a training bucket."""
    if days <= 1:
        return "short"
    elif days <= 3:
        return "medium"
    else:
        return "long"


def _classify(
    direction: str,
    price_now: Optional[float],
    price_future: Optional[float],
) -> Optional[str]:
    """
    Return 'correct', 'incorrect', 'neutral', or None (no data yet).

    None is returned when:
      - price_future is missing (data not yet available — row stays pending)
      - price_now is missing or ≤ 0 (corrupt/zero entry — skip row safely)
      - direction is not 'up' or 'down' (unknown — caller should already filter)

    direction must already be normalised to lowercase before calling.
    """
    if price_future is None or price_now is None or price_now <= 0:
        return None   # guard: division-by-zero / missing data

    change_pct = (price_future - price_now) / price_now * 100.0

    if abs(change_pct) <= _NEUTRAL_BAND:
        return "neutral"

    if direction == "up":
        return "correct" if change_pct > 0 else "incorrect"
    if direction == "down":
        return "correct" if change_pct < 0 else "incorrect"

    return None  # unknown direction — should not reach here after caller normalises


# ---------------------------------------------------------------------------
# Main resolution function
# ---------------------------------------------------------------------------

async def resolve_pending_predictions(batch_size: int = 500) -> int:
    """
    Resolve pending prediction_log rows whose time horizon has elapsed.

    Returns count of rows updated (outcome set to correct/incorrect/neutral/expired).
    Rows whose forward price is not yet available remain 'pending'.

    Outcomes:
      correct   — direction was right (beyond neutral band)
      incorrect — direction was wrong
      neutral   — price moved ≤ 0.2% either way
      expired   — still pending after 3× the horizon; price data will never arrive
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
            logger.debug("prediction_resolver: no pending predictions")
            return 0

        # Separate expired rows (3× horizon elapsed, price data will never arrive)
        # from eligible rows (1× horizon elapsed but within the expiry window).
        expired_rows: list[PredictionLog] = []
        eligible: list[PredictionLog]     = []
        pending_not_yet: int              = 0

        for r in all_rows:
            horizon_s  = r.time_horizon_days * 86_400
            target_ts  = r.predicted_at + horizon_s
            expire_ts  = r.predicted_at + horizon_s * _EXPIRE_MULTIPLIER

            if now_ts > expire_ts:
                expired_rows.append(r)
            elif now_ts >= target_ts:
                eligible.append(r)
            else:
                pending_not_yet += 1

        logger.info(
            "prediction_resolver: %d eligible, %d expired, %d not-yet-due (of %d pending)",
            len(eligible), len(expired_rows), pending_not_yet, len(all_rows),
        )

        # ── Mark expired rows ──────────────────────────────────────────────
        for r in expired_rows:
            r.outcome         = "expired"
            r.outcome_at      = now_ts
            r.horizon_bucket  = _horizon_bucket(r.time_horizon_days)

        if not eligible:
            return len(expired_rows)

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

            # Try intraday_ticks first, fall back to legacy price_history
            ph_stmt = (
                select(IntradayTick.scraped_at, IntradayTick.close)
                .where(IntradayTick.symbol == sym)
                .where(IntradayTick.scraped_at.between(lo, hi))
                .order_by(IntradayTick.scraped_at.asc())
            )
            ph_rows = (await session.execute(ph_stmt)).all()

            if not ph_rows:
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

        # ── Step 3: classify and update resolved rows ──────────────────────
        # Note: "expired" rows must be excluded from accuracy metrics in analytics
        # (they represent data availability failures, not prediction quality).
        # Track expiry rate per symbol / horizon as a data quality signal instead.
        resolved        = 0
        skipped_no_price = 0
        skipped_invalid  = 0   # zero/null entry price or unknown direction

        for r in eligible:
            # Normalize direction once at read time — guards against upstream casing changes
            direction = (r.predicted_direction or "").lower().strip()
            if direction not in {"up", "down"}:
                skipped_invalid += 1
                continue

            # Guard: zero or null entry price → corrupt row, skip safely
            if not r.price_at_prediction or r.price_at_prediction <= 0:
                skipped_invalid += 1
                continue

            outcome_price, outcome_scraped_at = price_map.get(r.id, (None, None))
            outcome = _classify(direction, r.price_at_prediction, outcome_price)

            if outcome is None:
                skipped_no_price += 1
                continue  # Forward price not yet available — leave pending

            pct_change = (outcome_price - r.price_at_prediction) / r.price_at_prediction * 100.0

            r.outcome             = outcome
            r.outcome_price       = outcome_price
            r.outcome_at          = outcome_scraped_at
            r.pct_change          = pct_change
            r.horizon_bucket      = _horizon_bucket(r.time_horizon_days)
            r.evaluated_lag_sec   = (outcome_scraped_at - r.predicted_at) if outcome_scraped_at else None
            resolved += 1

        total = resolved + len(expired_rows)
        logger.info(
            "prediction_resolver: resolved=%d expired=%d skipped_no_price=%d skipped_invalid=%d",
            resolved, len(expired_rows), skipped_no_price, skipped_invalid,
        )
        return total
