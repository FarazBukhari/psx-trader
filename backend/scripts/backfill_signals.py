#!/usr/bin/env python3
"""
PSX Historical Signal Backfill
================================
Replays RSI + SMA crossover signal logic against eod_prices to generate
training data without needing the live server to run during market hours.

Writes directly to BOTH:
  - signals_log      (one row per signal, keyed on symbol + generated_at)
  - signal_outcomes  (outcome resolved immediately using forward EOD closes)

This bypasses the signal_evaluator's intraday-tick dependency, which doesn't
apply to historical daily data.

Signal logic (matches live signal_engine RSI + SMA crossover strategies):
  RSI(14) < 30                    → BUY   (oversold)
  RSI(14) > 70                    → SELL  (overbought)
  SMA(5) crosses above SMA(20)    → BUY   (golden cross)
  SMA(5) crosses below SMA(20)    → SELL  (death cross)
  Both RSI + SMA agree            → higher action_score
  Otherwise                       → HOLD

Outcome resolution (matches signal_evaluator._classify):
  BUY  → correct if forward_close > entry_close by > 0.2%
          neutral if |change| ≤ 0.2%
          incorrect otherwise
  SELL → correct if forward_close < entry_close
          neutral  if no change
          incorrect if price rose
  HOLD → correct if |change| < 0.5%, incorrect otherwise

Horizons:
  short  → next trading day close  (T+1)
  medium → T+2 close
  long   → T+5 close

Idempotent: skips (symbol, timestamp) pairs already in signal_outcomes.

Usage (from backend/ directory):
  python -m scripts.backfill_signals                          # all symbols
  python -m scripts.backfill_signals --symbols ENGRO LUCK     # specific
  python -m scripts.backfill_signals --symbols ENGRO --verbose
  python -m scripts.backfill_signals --changed-only           # only signal-change rows (fewer, higher quality)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_signals")

_PKT = timezone(timedelta(hours=5))

# Signal thresholds — match live engine defaults
RSI_PERIOD      = 14
RSI_OVERSOLD    = 30.0
RSI_OVERBOUGHT  = 70.0
SMA_SHORT       = 5
SMA_LONG        = 20
NEUTRAL_BAND    = 0.2   # % — matches signal_evaluator
HOLD_BAND       = 0.5   # % — correct for HOLD

# Forward-price horizons (in trading days)
SHORT_DAYS  = 1
MEDIUM_DAYS = 2
LONG_DAYS   = 5


# ---------------------------------------------------------------------------
# RSI (simple, matches core.py + feature_engine.py)
# ---------------------------------------------------------------------------

def _rsi_series(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta  = close.diff()
    gains  = delta.clip(lower=0)
    losses = (-delta).clip(lower=0)
    avg_g  = gains.rolling(window=period, min_periods=period).mean()
    avg_l  = losses.rolling(window=period, min_periods=period).mean()
    rs     = avg_g / avg_l.replace(0, np.nan)
    rsi    = 100.0 - 100.0 / (1.0 + rs)
    return rsi.where(avg_l != 0, other=100.0)


# ---------------------------------------------------------------------------
# Outcome classifier (mirrors signal_evaluator._classify)
# ---------------------------------------------------------------------------

def _classify(signal: str, price_now: float, price_future: Optional[float]) -> Optional[str]:
    if price_future is None or price_now <= 0:
        return None
    change_pct = (price_future - price_now) / price_now * 100.0
    sig = signal.upper()
    if sig == "BUY":
        if abs(change_pct) <= NEUTRAL_BAND:
            return "neutral"
        return "correct" if change_pct > 0 else "incorrect"
    elif sig in ("SELL", "FORCE_SELL"):
        if abs(change_pct) <= NEUTRAL_BAND:
            return "neutral"
        return "correct" if change_pct < 0 else "incorrect"
    elif sig == "HOLD":
        return "correct" if abs(change_pct) < HOLD_BAND else "incorrect"
    return None


# ---------------------------------------------------------------------------
# Signal generation for one symbol
# ---------------------------------------------------------------------------

def _generate_signals(df: pd.DataFrame, changed_only: bool) -> pd.DataFrame:
    """
    Given a symbol's EOD DataFrame (sorted by date_key asc),
    return a DataFrame of signal rows.

    Columns: date_key, scraped_at, close, signal, prev_signal,
             signal_changed, signal_sources, action_score, rsi, sma5, sma20
    """
    df = df.copy().reset_index(drop=True)
    close = df["close"].astype(float)

    rsi  = _rsi_series(close, RSI_PERIOD)
    sma5 = close.rolling(SMA_SHORT).mean()
    sma20 = close.rolling(SMA_LONG).mean()

    # SMA crossover direction
    sma_cross = np.sign(sma5 - sma20)
    sma_cross_prev = sma_cross.shift(1)

    rows = []
    prev_signal = "HOLD"

    for i in range(len(df)):
        r  = float(rsi.iloc[i])  if not np.isnan(rsi.iloc[i])  else None
        s5 = float(sma5.iloc[i]) if not np.isnan(sma5.iloc[i]) else None
        s20= float(sma20.iloc[i])if not np.isnan(sma20.iloc[i])else None

        if r is None or s5 is None or s20 is None:
            continue

        sources = []
        signal  = "HOLD"
        score   = 0.0

        # RSI signal
        rsi_sig = None
        if r < RSI_OVERSOLD:
            rsi_sig = "BUY"
            sources.append("rsi")
            score += 0.4
        elif r > RSI_OVERBOUGHT:
            rsi_sig = "SELL"
            sources.append("rsi")
            score += 0.4

        # SMA crossover signal
        sma_sig = None
        sc  = sma_cross.iloc[i]
        scp = sma_cross_prev.iloc[i]
        if not np.isnan(scp):
            if scp <= 0 and sc > 0:
                sma_sig = "BUY"
                sources.append("sma_crossover")
                score += 0.4
            elif scp >= 0 and sc < 0:
                sma_sig = "SELL"
                sources.append("sma_crossover")
                score += 0.4

        # Resolve final signal
        if rsi_sig and sma_sig and rsi_sig == sma_sig:
            signal = rsi_sig
            score  = min(score, 0.85)
        elif rsi_sig:
            signal = rsi_sig
            score  = min(score, 0.55)
        elif sma_sig:
            signal = sma_sig
            score  = min(score, 0.55)
        else:
            signal = "HOLD"
            score  = 0.1

        changed = (signal != prev_signal)

        if changed_only and not changed:
            prev_signal = signal
            continue

        rows.append({
            "date_key":      int(df["date_key"].iloc[i]),
            "scraped_at":    int(df["scraped_at"].iloc[i]),
            "close":         float(close.iloc[i]),
            "signal":        signal,
            "prev_signal":   prev_signal,
            "signal_changed":changed,
            "signal_sources":json.dumps(sources),
            "action_score":  round(score, 4),
            "rsi":           round(r, 4),
            "sma5":          round(s5, 4),
            "sma20":         round(s20, 4),
        })
        prev_signal = signal

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Forward price lookup (next T+n trading day close within the eod_df)
# ---------------------------------------------------------------------------

def _forward_close(
    closes: np.ndarray,
    idx: int,
    n_days: int,
) -> Optional[float]:
    """Return the close n_days ahead of idx, or None if not available."""
    target = idx + n_days
    if target < len(closes):
        return float(closes[target])
    return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _load_eod(symbols: Optional[list[str]]) -> dict[str, pd.DataFrame]:
    """Load eod_prices keyed by symbol, sorted by date_key asc."""
    from sqlalchemy import select
    from app.db.database import get_session
    from app.db.models import EODPrice

    async with get_session() as session:
        q = select(
            EODPrice.symbol,
            EODPrice.date_key,
            EODPrice.scraped_at,
            EODPrice.close,
            EODPrice.sector,
        ).order_by(EODPrice.symbol, EODPrice.date_key)
        if symbols:
            q = q.where(EODPrice.symbol.in_(symbols))
        rows = (await session.execute(q)).all()

    if not rows:
        return {}

    df = pd.DataFrame(rows, columns=["symbol", "date_key", "scraped_at", "close", "sector"])
    return {sym: grp.reset_index(drop=True) for sym, grp in df.groupby("symbol")}


async def _existing_outcome_keys(symbols: Optional[list[str]]) -> set[tuple[str, int]]:
    """Return set of (symbol, timestamp) already in signal_outcomes."""
    from sqlalchemy import select
    from app.db.database import get_session
    from app.db.models import SignalOutcome

    async with get_session() as session:
        q = select(SignalOutcome.symbol, SignalOutcome.timestamp)
        if symbols:
            q = q.where(SignalOutcome.symbol.in_(symbols))
        rows = (await session.execute(q)).all()
    return {(r.symbol, r.timestamp) for r in rows}


async def _insert_batch(
    sig_rows: list[dict],
    out_rows: list[dict],
) -> tuple[int, int]:
    """
    Bulk-insert into signals_log and signal_outcomes.
    Returns (n_signals_inserted, n_outcomes_inserted).
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from app.db.database import get_session
    from app.db.models import SignalLog, SignalOutcome

    if not sig_rows:
        return 0, 0

    n_sig = n_out = 0

    for i in range(0, len(sig_rows), 500):
        sb = sig_rows[i:i + 500]
        ob = out_rows[i:i + 500]

        async with get_session() as session:
            # signals_log has no unique constraint — check via SELECT first
            # (done upstream by filtering against existing_outcome_keys)
            await session.execute(
                sqlite_insert(SignalLog).values(sb)
            )
            n_sig += len(sb)

            result = await session.execute(
                sqlite_insert(SignalOutcome)
                .values(ob)
                .on_conflict_do_nothing()
            )
            n_out += result.rowcount or 0

    return n_sig, n_out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(
    symbols: Optional[list[str]],
    changed_only: bool,
    verbose: bool,
) -> None:
    from app.db import init_db

    await init_db()

    if verbose:
        logger.setLevel(logging.DEBUG)

    logger.info("Loading EOD prices …")
    eod_by_sym = await _load_eod(symbols)
    if not eod_by_sym:
        logger.warning("No EOD price data found — run fetch_historical first.")
        return

    logger.info("Fetching existing signal_outcomes keys …")
    existing = await _existing_outcome_keys(symbols)
    logger.info("  %d existing outcome rows (will skip)", len(existing))

    now_ts = int(time.time())
    total_sig = total_out = 0

    for symbol, df in eod_by_sym.items():
        sig_df = _generate_signals(df, changed_only=changed_only)
        if sig_df.empty:
            logger.debug("  %s: no signals generated", symbol)
            continue

        closes = df["close"].values
        sector = df["sector"].iloc[0] if "sector" in df.columns else None

        sig_rows: list[dict] = []
        out_rows: list[dict] = []

        for i, row in sig_df.iterrows():
            # generated_at = scraped_at of that EOD row (actual PSX API timestamp)
            gen_at = int(row["scraped_at"])

            # Skip if already resolved
            if (symbol, gen_at) in existing:
                continue

            # Find position in the original df by date_key match
            pos_matches = df.index[df["date_key"] == row["date_key"]].tolist()
            if not pos_matches:
                continue
            pos = pos_matches[0]

            # Forward closes
            f_short  = _forward_close(closes, pos, SHORT_DAYS)
            f_medium = _forward_close(closes, pos, MEDIUM_DAYS)
            f_long   = _forward_close(closes, pos, LONG_DAYS)

            entry = float(row["close"])

            out_short  = _classify(row["signal"], entry, f_short)
            out_medium = _classify(row["signal"], entry, f_medium)
            out_long   = _classify(row["signal"], entry, f_long)

            # signals_log row
            sig_rows.append({
                "symbol":         symbol,
                "signal":         row["signal"],
                "prev_signal":    row["prev_signal"],
                "signal_changed": bool(row["signal_changed"]),
                "signal_sources": row["signal_sources"],
                "action_score":   float(row["action_score"]),
                "horizon":        "short",
                "rsi":            float(row["rsi"]),
                "sma5":           float(row["sma5"]),
                "sma20":          float(row["sma20"]),
                "price":          entry,
                "volume":         None,
                "confidence":     None,
                "time_horizon":   None,
                "generated_at":   gen_at,
            })

            # signal_outcomes row
            out_rows.append({
                "symbol":           symbol,
                "signal":           row["signal"],
                "signal_sources":   row["signal_sources"],
                "timestamp":        gen_at,
                "price_at_signal":  entry,
                "price_short":      f_short,
                "price_medium":     f_medium,
                "price_long":       f_long,
                "outcome_short":    out_short,
                "outcome_medium":   out_medium,
                "outcome_long":     out_long,
                "short_latency_sec":  None,
                "medium_latency_sec": None,
                "long_latency_sec":   None,
                "evaluated_at":     now_ts,
            })

        if not sig_rows:
            logger.debug("  %s: all rows already in signal_outcomes", symbol)
            continue

        n_sig, n_out = await _insert_batch(sig_rows, out_rows)
        total_sig += n_sig
        total_out += n_out
        logger.info(
            "  %s: %d signals, %d outcomes inserted",
            symbol, n_sig, n_out,
        )

    logger.info(
        "\nDone. %d signal rows, %d outcome rows inserted.",
        total_sig, total_out,
    )
    if total_out > 0:
        logger.info(
            "Next steps:\n"
            "  python -m scripts.build_features   # attach targets to feature store\n"
            "  python -m scripts.train_model       # train models"
        )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Backfill signals_log + signal_outcomes from EOD price history"
    )
    p.add_argument(
        "--symbols", nargs="+", default=None,
        help="PSX symbols to backfill (default: all in eod_prices)",
    )
    p.add_argument(
        "--changed-only", action="store_true",
        help="Only write rows where the signal changed from the previous day (fewer rows, higher quality)",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(
        symbols=args.symbols,
        changed_only=args.changed_only,
        verbose=args.verbose,
    ))
