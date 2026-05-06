"""
strategy/core.py — pure signal computation, no I/O, no state.

Single source of truth for RSI, SMA crossover, change-% momentum, and
inline volume-spike indicator logic shared between the live signal engine
and the backtester.

All functions are:
  - Synchronous
  - Stateless (no globals mutated)
  - Free of DB access, app_state, and price_buffer references
  - Safe to call from any context (async loop, test, backtest)

Performance note: pure Python is used deliberately over numpy so both the
live engine (which patches in from price_buffer) and the backtester (which
slices historical arrays) produce bit-identical results for the same inputs.
numpy floats and Python floats produce the same values for these simple
mean operations, but using one implementation removes the class of divergence
entirely.
"""

from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Priority merge
# ---------------------------------------------------------------------------

# Canonical signal priority — order is a CONTRACT, do not reorder.
# FORCE_SELL must always beat SELL, SELL must always beat BUY.
# Any code that resolves competing signals must use this list, not a local copy.
SIGNAL_PRIORITY: list[str] = ["FORCE_SELL", "SELL", "BUY", "HOLD"]

# Minimum price history required before any signal is emitted.
# Below this threshold indicators are too noisy to be useful and early ticks
# would pollute the forward tracker with low-confidence trades.
MIN_PRICES: int = 20


def resolve_signals(signals: list[str]) -> str:
    """Merge multiple strategy opinions → highest-priority signal wins."""
    if not signals:
        return "HOLD"
    for p in SIGNAL_PRIORITY:
        if p in signals:
            return p
    return "HOLD"


# ---------------------------------------------------------------------------
# Indicator helpers (pure, O(n) over the tail of prices)
# ---------------------------------------------------------------------------

def compute_rsi(prices: list[float], period: int) -> Optional[float]:
    """
    Simple (non-Wilder) RSI over the last (period+1) closing prices.

    Returns None when there are insufficient prices.
    Matches the formula used in backtester._rsi() and
    signal_engine.RSIStrategy (np.mean on period deltas, not EMA).
    """
    if len(prices) < period + 1:
        return None
    recent  = prices[-(period + 1):]
    deltas  = [recent[i] - recent[i - 1] for i in range(1, len(recent))]
    avg_gain = sum(d for d in deltas if d > 0) / period
    avg_loss = sum(-d for d in deltas if d < 0) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def compute_sma(prices: list[float], n: int) -> Optional[float]:
    """SMA of the last n closing prices. Returns None when len(prices) < n."""
    if len(prices) < n:
        return None
    return sum(prices[-n:]) / n


def compute_sma_prev(prices: list[float], n: int) -> Optional[float]:
    """
    SMA of prices[-(n+1):-1] — the SMA one tick before the current tick.
    Used for crossover detection (did short cross long between prev and now?).
    Returns None when there are insufficient prices.
    """
    if len(prices) < n + 1:
        return None
    return sum(prices[-(n + 1):-1]) / n


# ---------------------------------------------------------------------------
# Unified signal generator
# ---------------------------------------------------------------------------

def generate_signal(
    prices:               list[float],
    volumes:              list[int],
    *,
    # RSI
    rsi_period:           int   = 14,
    rsi_oversold:         float = 30.0,
    rsi_overbought:       float = 70.0,
    # SMA crossover
    sma_short:            int   = 5,
    sma_long:             int   = 20,
    # Change %
    change_pct:           Optional[float] = None,
    change_pct_threshold: float = 3.0,
    clamp_change_pct:     bool  = False,   # True in backtester (PSX ±7.5% limit)
    # Volume spike (inline, from the volumes list; 0 = disabled)
    volume_spike_x:       float = 0.0,
    # Stop-loss (position-level; 0 = disabled)
    avg_cost:             float = 0.0,
    stop_loss_pct:        float = 0.0,
) -> dict:
    """
    Compute a trading signal from a closing-price series.

    Pure function — no I/O, no global state, no DB.

    Args:
        prices:               Close prices, oldest-first. ≥ 2 elements needed.
        volumes:              Volume readings aligned with prices (may be empty).
        rsi_period:           RSI lookback window (default 14).
        rsi_oversold:         RSI ≤ this → BUY vote.
        rsi_overbought:       RSI ≥ this → SELL vote.
        sma_short / sma_long: SMA crossover windows.
        change_pct:           Today's % change (from scraper or history row).
                              None → skip change_pct rule entirely.
        change_pct_threshold: |change_pct| must exceed this to cast a vote.
        clamp_change_pct:     Clamp change_pct to ±7.5 % before comparison.
                              Pass True in backtester (PSX enforces this limit),
                              False in live engine (scraper already reflects it).
        volume_spike_x:       If > 0, cast BUY when last volume exceeds
                              volume_spike_x × mean of prior volumes.
                              Requires len(volumes) ≥ 2.
        avg_cost:             Current position cost basis (for stop-loss).
                              Pass 0 (or omit) when not in a position.
        stop_loss_pct:        % drop from avg_cost that triggers FORCE_SELL.
                              Pass 0 (or omit) to disable.

    Returns:
        {
          "signal":     "BUY" | "SELL" | "HOLD" | "FORCE_SELL",
          "sources":    list of strategy names that voted,
          "rsi":        float | None,           (top-level, kept for back-compat)
          "change_pct": original change_pct passed in (unmodified),
          "meta": {
            "rsi":      float | None,           (same value — useful for ML/debug)
            "sma_fast": float | None,           (current short SMA)
            "sma_slow": float | None,           (current long SMA)
          }
        }

    Signal priority order is defined by SIGNAL_PRIORITY (module constant).
    """
    _empty_meta = {"rsi": None, "sma_fast": None, "sma_slow": None}

    # ── Guard: no prices ──────────────────────────────────────────────────────
    if not prices:
        return {"signal": "HOLD", "sources": [], "rsi": None,
                "change_pct": change_pct, "meta": _empty_meta}

    # ── Guard: minimum history ────────────────────────────────────────────────
    # Indicators are unreliable below MIN_PRICES ticks. Returning HOLD here
    # prevents noisy early signals from polluting the forward tracker.
    if len(prices) < MIN_PRICES:
        return {"signal": "HOLD", "sources": [], "rsi": None,
                "change_pct": change_pct, "meta": _empty_meta}

    cur      = prices[-1]
    signals: list[str] = []
    sources:  list[str] = []

    # ── Compute indicators up-front so meta is always populated ───────────────
    # (Cheap O(n) over a small tail — worth it for consistent debug output.)
    rsi       = compute_rsi(prices, rsi_period)
    sma_fast  = compute_sma(prices, sma_short)
    sma_slow  = compute_sma(prices, sma_long)
    meta      = {
        "rsi":      rsi,
        "sma_fast": round(sma_fast, 4) if sma_fast is not None else None,
        "sma_slow": round(sma_slow, 4) if sma_slow is not None else None,
    }

    # ── 1. Stop-loss ──────────────────────────────────────────────────────────
    # FORCE_SELL short-circuits all voting rules when price has fallen too far.
    if avg_cost > 0 and stop_loss_pct > 0 and cur <= avg_cost * (1 - stop_loss_pct / 100):
        return {
            "signal":     "FORCE_SELL",
            "sources":    ["stop_loss"],
            "rsi":        rsi,
            "change_pct": change_pct,
            "meta":       meta,
        }

    # ── 2. RSI ────────────────────────────────────────────────────────────────
    if rsi is not None:
        if rsi <= rsi_oversold:
            signals.append("BUY");  sources.append("rsi")
        elif rsi >= rsi_overbought:
            signals.append("SELL"); sources.append("rsi")

    # ── 3. SMA crossover ──────────────────────────────────────────────────────
    sma_s_prev = compute_sma_prev(prices, sma_short)
    sma_l_prev = compute_sma_prev(prices, sma_long)

    if all(v is not None for v in (sma_fast, sma_slow, sma_s_prev, sma_l_prev)):
        if sma_s_prev <= sma_l_prev and sma_fast > sma_slow:    # golden cross
            signals.append("BUY");  sources.append("sma_crossover")
        elif sma_s_prev >= sma_l_prev and sma_fast < sma_slow:  # death cross
            signals.append("SELL"); sources.append("sma_crossover")

    # ── 4. Change % momentum ──────────────────────────────────────────────────
    if change_pct is not None:
        chg = max(-7.5, min(7.5, change_pct)) if clamp_change_pct else change_pct
        if chg <= -change_pct_threshold:
            signals.append("SELL"); sources.append("change_pct")
        elif chg >= change_pct_threshold:
            signals.append("BUY");  sources.append("change_pct")

    # ── 5. Inline volume spike ────────────────────────────────────────────────
    # Uses the volumes list directly (no external rolling buffer).
    # In the live engine, VolumeSpikeStrategy handles this with a per-symbol
    # deque and is called from the strategy registry — this path is for the
    # backtester (and any caller that passes volumes explicitly).
    if volume_spike_x > 0 and len(volumes) > 1:
        vol_now = volumes[-1] or 0
        avg_vol = sum(volumes[:-1]) / (len(volumes) - 1)
        if avg_vol > 0 and vol_now > avg_vol * volume_spike_x:
            signals.append("BUY"); sources.append("volume_spike")

    return {
        "signal":     resolve_signals(signals),
        "sources":    sources,
        "rsi":        rsi,
        "change_pct": change_pct,
        "meta":       meta,
    }
