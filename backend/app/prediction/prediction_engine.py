"""
PredictionEngine — Phase 3 forward-looking signal enrichment.

Enriches SignalEngine outputs with prediction metadata.
No ML libraries — all math is standard arithmetic.
Target runtime: <50ms per symbol (operates on in-memory PriceBuffer).

Sub-models
──────────
1. Momentum projection   : OLS linear regression on last N closing prices
2. RSI mean reversion    : distance from neutral 50 → expected reversion
3. Bollinger Bands       : price position within bands → breakout / compression
4. Support / Resistance  : rolling min/max proximity
5. Volume divergence     : volume trend vs price trend (confirmation / warning)

Output contract (per signal)
────────────────────────────
{
  "prediction": {
    "direction":         "up" | "down" | "neutral",
    "confidence":        float   0.0–0.99,
    "trade_action":      "buy" | "sell" | "avoid",
    "time_horizon":      str     e.g. "~3 days",
    "hold_days":         int     1–5 (0 when neutral),
    "risk":              "low" | "medium" | "high",
    "basis":             list[str],
    "expected_move_pct": float,
    "reward_risk_ratio": float,
  }
}

Confidence scoring (weighted model)
─────────────────────────────────────
  MODEL_WEIGHTS: momentum=0.35, rsi=0.20, bollinger=0.15, support/resistance=0.10
  Volume is a *multiplier* on all vote strengths (not an independent vote).

  up_strength   = Σ weight_i × strength_i  (for "up" votes)
  down_strength = Σ weight_i × strength_i  (for "down" votes)
  total_strength = up_strength + down_strength
  agreement_factor = |up − down| / total_strength   ∈ [0, 1]
  confidence = total_strength × agreement_factor     clamped 0.0–0.99

Trade action
────────────
  "avoid" when: confidence < 0.40, total_strength < 0.30, or agreement_factor < 0.25
  "buy"   when: direction == "up"   and thresholds pass
  "sell"  when: direction == "down" and thresholds pass

Prediction logging
──────────────────
Non-neutral predictions on BUY/SELL/FORCE_SELL signals are persisted
to the prediction_log table as fire-and-forget async tasks.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Optional

from ..db.database import get_session
from ..db.models import PredictionLog
from ..strategy.signal_engine import price_buffer

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

REGRESSION_WINDOW = 20    # OLS lookback: number of ticks
RSI_PERIOD        = 14    # RSI calculation period
BB_PERIOD         = 20    # Bollinger Bands SMA period
SR_WINDOW         = 50    # Support/Resistance base window (dynamic: min(max(50,n),200))
SR_WINDOW_MAX     = 200   # SR window hard ceiling
VOLUME_WINDOW     = 20    # Rolling average volume window (long baseline)
VOLUME_RECENT     = 5     # Volume lookback for multiplier (last-5 sessions)
MIN_PRICES        = 10    # Minimum ticks needed before any prediction fires
TICKS_PER_SESSION = 50    # Heuristic: ~50 meaningful price ticks per PSX session
MAX_EXPECTED_MOVE = 30.0  # Cap on expected_move_pct absolute value (%)

# ── Weighted confidence model ─────────────────────────────────────────────────
# Sub-model weights must sum ≤ 1.0.
# Volume is a *modifier* (see _volume_multiplier), not a direct vote weight.
MODEL_WEIGHTS: dict[str, float] = {
    "momentum":   0.35,
    "rsi":        0.20,
    "bollinger":  0.15,
    "support":    0.10,
    "resistance": 0.10,
}

# Trade action thresholds
MIN_CONFIDENCE_ACT  = 0.40   # confidence must exceed this to trade
MIN_TOTAL_STR_ACT   = 0.30   # total weighted strength must exceed this
MIN_AGREEMENT_ACT   = 0.25   # agreement_factor must exceed this

# Volume multiplier caps
VOL_BOOST           = 1.30   # vol_ratio > 1.5 → boost all strengths 30 %
VOL_DAMPEN          = 0.70   # vol_ratio < 0.7 → dampen all strengths 30 %


# ──────────────────────────────────────────────────────────────────────────────
# Volume buffer — rolling average volume per symbol
# ──────────────────────────────────────────────────────────────────────────────

class VolumeBuffer:
    """Tracks a rolling window of volume readings per symbol."""

    def __init__(self, maxlen: int = VOLUME_WINDOW) -> None:
        self._data: dict[str, deque[int]] = {}
        self._maxlen = maxlen

    def push(self, symbol: str, volume: int) -> None:
        if volume and volume > 0:
            if symbol not in self._data:
                self._data[symbol] = deque(maxlen=self._maxlen)
            self._data[symbol].append(volume)

    def avg(self, symbol: str) -> float:
        buf = self._data.get(symbol, deque())
        return sum(buf) / len(buf) if buf else 0.0

    def avg_recent(self, symbol: str, n: int = VOLUME_RECENT) -> float:
        """Average of the last ``n`` volume readings (for multiplier calc)."""
        buf = self._data.get(symbol, deque())
        if not buf:
            return 0.0
        recent = list(buf)[-n:]
        return sum(recent) / len(recent) if recent else 0.0

    def count(self, symbol: str) -> int:
        return len(self._data.get(symbol, []))


# ──────────────────────────────────────────────────────────────────────────────
# Pure-math helpers  (no external dependencies)
# ──────────────────────────────────────────────────────────────────────────────

def _linear_regression(prices: list[float]) -> tuple[float, float]:
    """
    Ordinary Least Squares: y = slope·x + intercept, x = tick index.

    Returns
    -------
    slope     : price-units per tick (positive = uptrend)
    r_squared : goodness-of-fit [0, 1]
    """
    n = len(prices)
    if n < 2:
        return 0.0, 0.0

    x_mean = (n - 1) / 2.0
    y_mean = sum(prices) / n

    ss_xy = sum((i - x_mean) * (prices[i] - y_mean) for i in range(n))
    ss_xx = sum((i - x_mean) ** 2 for i in range(n))
    ss_yy = sum((p - y_mean) ** 2 for p in prices)

    if ss_xx == 0:
        return 0.0, 0.0

    slope = ss_xy / ss_xx
    r_sq  = (ss_xy ** 2) / (ss_xx * ss_yy) if ss_yy > 0 else 0.0
    return slope, min(r_sq, 1.0)


def _calc_rsi(prices: list[float], period: int = RSI_PERIOD) -> Optional[float]:
    """RSI over last `period` price changes. Returns None if not enough data."""
    if len(prices) < period + 1:
        return None
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    recent = deltas[-period:]
    avg_gain = sum(d for d in recent if d > 0) / period
    avg_loss = sum(-d for d in recent if d < 0) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - 100.0 / (1.0 + rs), 1)


def _calc_bollinger(
    prices: list[float],
    period: int = BB_PERIOD,
) -> Optional[tuple[float, float, float, float]]:
    """
    Bollinger Bands (2σ).

    Returns
    -------
    upper         : upper band price
    lower         : lower band price
    percent_b     : 0.0 = at lower band, 0.5 = at middle, 1.0 = at upper band
    bandwidth_pct : (upper–lower)/middle × 100  [volatility proxy]
    """
    if len(prices) < period:
        return None
    window   = prices[-period:]
    mean     = sum(window) / period
    variance = sum((p - mean) ** 2 for p in window) / period
    std      = variance ** 0.5
    if std == 0 or mean == 0:
        return None
    upper        = mean + 2 * std
    lower        = mean - 2 * std
    price        = prices[-1]
    band_range   = upper - lower
    percent_b    = (price - lower) / band_range if band_range > 0 else 0.5
    bandwidth_pct = band_range / mean * 100
    return upper, lower, percent_b, bandwidth_pct


def _calc_support_resistance(
    prices: list[float],
    window: Optional[int] = None,
) -> Optional[tuple[float, float, float]]:
    """
    Support / Resistance from rolling min/max.

    Window is dynamic: min(max(50, len(prices)), 200) so we use as much
    history as is available, bounded between 50 and 200 ticks.

    Returns
    -------
    support    : rolling minimum price
    resistance : rolling maximum price
    proximity  : 0.0 = at support, 1.0 = at resistance
    """
    if len(prices) < 5:
        return None
    # Dynamic window: use all available history up to SR_WINDOW_MAX, min SR_WINDOW
    effective = min(max(SR_WINDOW, len(prices)), SR_WINDOW_MAX) if window is None else window
    w          = prices[-effective:]
    support    = min(w)
    resistance = max(w)
    rng        = resistance - support
    if rng <= 0:
        return None
    proximity  = (prices[-1] - support) / rng
    return support, resistance, proximity


# ──────────────────────────────────────────────────────────────────────────────
# Sub-model vote functions
# Each returns (vote, strength, name) or None
#   vote     : "up" | "down"
#   strength : 0.0–1.0 (this model's conviction)
#   name     : label for the "basis" field
# ──────────────────────────────────────────────────────────────────────────────

def _momentum_vote(
    prices: list[float],
    price: float,
) -> Optional[tuple[str, float, str]]:
    """
    Linear regression on last REGRESSION_WINDOW prices.
    Strong upward slope → "up"; strong downward → "down".
    Abstains if R² < 0.10 (noisy, no clear trend).
    """
    window = prices[-REGRESSION_WINDOW:] if len(prices) >= REGRESSION_WINDOW else prices
    if len(window) < 5:
        return None

    slope, r_sq = _linear_regression(window)
    if r_sq < 0.10:
        return None  # Trend too weak to opine

    slope_pct = slope / price * 100 if price > 0 else 0.0
    if abs(slope_pct) < 0.001:
        return None  # Trivially flat

    vote = "up" if slope > 0 else "down"
    # Strength = fit quality × magnitude of slope
    # slope_pct of 0.02% per tick → ~1% per session → modest; scale so 2% per tick = full
    slope_strength = min(abs(slope_pct) * 50.0, 1.0)
    strength = round(r_sq * slope_strength, 3)
    if strength < 0.05:
        return None
    return vote, min(strength, 1.0), "momentum"


def _rsi_vote(
    prices: list[float],
    rsi: Optional[float],
) -> Optional[tuple[str, float, str]]:
    """
    RSI-based mean reversion.
    Oversold (RSI ≤ 30) → expects bounce up.
    Overbought (RSI ≥ 70) → expects pullback down.
    """
    if rsi is None:
        rsi = _calc_rsi(prices)
    if rsi is None:
        return None

    if rsi <= 30:
        # [0 at RSI=30 … 1 at RSI=0]
        distance = (30.0 - rsi) / 30.0
        strength = round(0.30 + distance * 0.70, 3)
        return "up", min(strength, 1.0), "rsi"

    if rsi >= 70:
        # [0 at RSI=70 … 1 at RSI=100]
        distance = (rsi - 70.0) / 30.0
        strength = round(0.30 + distance * 0.70, 3)
        return "down", min(strength, 1.0), "rsi"

    return None  # Neutral RSI zone — abstain


def _bollinger_vote(
    prices: list[float],
    price: float,
) -> Optional[tuple[str, float, str]]:
    """
    Bollinger Band position.
    Near lower band (percent_b < 0.10) → potential bounce up.
    Near upper band (percent_b > 0.90) → potential pullback down.
    """
    result = _calc_bollinger(prices)
    if result is None:
        return None
    _, _, percent_b, _ = result

    if percent_b < 0.10:
        # More extreme = stronger signal
        strength = round(0.30 + (0.10 - percent_b) * 7.0, 3)
        return "up", min(strength, 1.0), "bollinger"

    if percent_b > 0.90:
        strength = round(0.30 + (percent_b - 0.90) * 7.0, 3)
        return "down", min(strength, 1.0), "bollinger"

    return None  # Mid-band — no opinion


def _support_resistance_vote(
    prices: list[float],
    price: float,
) -> Optional[tuple[str, float, str]]:
    """
    Support / Resistance proximity.
    Near support (proximity < 0.15) → potential bounce up.
    Near resistance (proximity > 0.85) → potential pullback down.
    """
    result = _calc_support_resistance(prices)
    if result is None:
        return None
    _, _, proximity = result

    if proximity < 0.15:
        strength = round(0.20 + (0.15 - proximity) * 5.3, 3)
        return "up", min(strength, 1.0), "support"

    if proximity > 0.85:
        strength = round(0.20 + (proximity - 0.85) * 5.3, 3)
        return "down", min(strength, 1.0), "resistance"

    return None


def _volume_multiplier(volume: int, avg_recent5: float) -> float:
    """
    Returns a strength multiplier applied to ALL directional votes before
    they enter the confidence formula.  Volume no longer casts its own vote —
    instead it confirms or weakens every other signal proportionally.

    vol_ratio = current_volume / avg(last-5 sessions)
      > 1.5  →  VOL_BOOST  (1.30) — high volume confirms the move
      < 0.7  →  VOL_DAMPEN (0.70) — low volume warns of weak conviction
      else   →  1.0        (neutral, no adjustment)
    """
    if avg_recent5 <= 0 or volume <= 0:
        return 1.0
    vol_ratio = volume / avg_recent5
    if vol_ratio > 1.5:
        return VOL_BOOST
    if vol_ratio < 0.7:
        return VOL_DAMPEN
    return 1.0


# ──────────────────────────────────────────────────────────────────────────────
# Vote combiner
# ──────────────────────────────────────────────────────────────────────────────

def _combine_votes(
    votes: list[tuple[str, float, str]],
) -> tuple[str, float, list[str], float, float]:
    """
    Merge sub-model votes → (direction, confidence, basis, total_strength, agreement_factor).

    Weighted confidence formula
    ---------------------------
    Each sub-model contributes: weight_i × strength_i  (MODEL_WEIGHTS lookup).

      up_strength   = Σ weight_i × strength_i  for votes == "up"
      down_strength = Σ weight_i × strength_i  for votes == "down"
      total_strength = up_strength + down_strength

      agreement_factor = |up_strength − down_strength| / max(total_strength, ε)
        → 0.0 = perfectly split, 1.0 = unanimous

      confidence = total_strength × agreement_factor   [clamped 0.0–0.99]

    Direction threshold: agreement_factor ≥ 0.15
      → prevents calling direction on near-50/50 splits
    """
    up_strength   = 0.0
    down_strength = 0.0
    basis: list[str] = []

    for vote, strength, name in votes:
        w = MODEL_WEIGHTS.get(name, 0.10)
        weighted = w * strength
        if vote == "up":
            up_strength += weighted
            basis.append(name)
        elif vote == "down":
            down_strength += weighted
            basis.append(name)

    total_strength = up_strength + down_strength
    if total_strength < 1e-9:
        return "neutral", 0.0, [], 0.0, 0.0

    agreement_factor = abs(up_strength - down_strength) / max(total_strength, 1e-6)

    # Direction: winner must dominate by at least 15% of total weighted strength
    if agreement_factor >= 0.15:
        direction = "up" if up_strength >= down_strength else "down"
    else:
        direction = "neutral"

    confidence = round(min(total_strength * agreement_factor, 0.99), 3)

    return direction, confidence, list(set(basis)), total_strength, agreement_factor


# ──────────────────────────────────────────────────────────────────────────────
# Volatility helper
# ──────────────────────────────────────────────────────────────────────────────

def _calc_volatility(prices: list[float], window: int = 20) -> float:
    """
    Range-based normalised volatility over last ``window`` prices.

    Formula: (max − min) / mean × 100

    Returns a percentage value, e.g. 3.5 means the price has ranged 3.5%
    within the window.  Returns 0.0 when data is insufficient.

    Thresholds (used by _risk_level and _calc_hold_days):
      < 2 %  → low
      2–5 %  → medium
      > 5 %  → high
    """
    w = prices[-min(window, len(prices)):]
    if len(w) < 2:
        return 0.0
    mean_p = sum(w) / len(w)
    if mean_p <= 0:
        return 0.0
    return (max(w) - min(w)) / mean_p * 100.0


# ──────────────────────────────────────────────────────────────────────────────
# Risk assessment  (volatility-based, replaces heuristic BB scoring)
# ──────────────────────────────────────────────────────────────────────────────

def _risk_level(
    prices: list[float],
    confidence: float,
    rsi: Optional[float],
) -> str:
    """
    Assess prediction risk: "low" | "medium" | "high".

    Primary driver is realised price volatility over last 20 ticks.
    Confidence and extreme RSI act as secondary adjustments.
    """
    vol = _calc_volatility(prices, window=20)

    # Base risk from volatility bands
    if vol > 5.0:
        risk = "high"
    elif vol > 2.0:
        risk = "medium"
    else:
        risk = "low"

    # Lift risk one level if confidence is very low
    if confidence < 0.20 and risk != "high":
        risk = "high" if risk == "medium" else "medium"

    # Lift risk one level for extreme RSI (potential sharp reversal)
    if rsi is not None and (rsi < 20 or rsi > 80) and risk != "high":
        risk = "high" if risk == "medium" else "medium"

    return risk


# ──────────────────────────────────────────────────────────────────────────────
# Dynamic hold duration
# ──────────────────────────────────────────────────────────────────────────────

def _calc_hold_days(
    prices: list[float],
    votes: list[tuple[str, float, str]],
    direction: str,
) -> int:
    """
    Compute hold period from signal type and volatility.

    Base hold by dominant signal
    ─────────────────────────────
    Strong momentum (strength > 0.7) : 4 days  (trending — ride the wave)
    RSI reversal present              : 2 days  (mean-reversion plays are quick)
    Near support/resistance           : 1 day   (bounce/pullback — take profit fast)
    Default                           : 3 days

    Volatility adjustment (last 10 ticks)
    ──────────────────────────────────────
    vol > 5 % → −1  (choppy — reduce exposure)
    vol < 1.5 % → +1  (trending quietly — hold longer)

    Clamped to [1, 5].
    Returns 0 for neutral direction.
    """
    if direction == "neutral":
        return 0

    # Extract per-model strengths for signal-type detection
    str_by_name: dict[str, float] = {name: s for _, s, name in votes}

    momentum_str = str_by_name.get("momentum", 0.0)
    has_rsi      = "rsi" in str_by_name
    sr_str       = str_by_name.get("support", 0.0) + str_by_name.get("resistance", 0.0)

    if momentum_str > 0.7:
        base = 4          # Strong trend → hold 3-5, centre at 4
    elif has_rsi:
        base = 2          # Mean reversion → 1-2, centre at 2
    elif sr_str > 0.3:
        base = 1          # Bounce/pullback → very short hold
    else:
        base = 3          # Default: 2-3, centre at 3

    # Adjust by short-window volatility
    vol10 = _calc_volatility(prices, window=10)
    if vol10 > 5.0:
        base -= 1         # High volatility → shorter hold
    elif 0 < vol10 < 1.5:
        base += 1         # Steady / low vol → longer hold

    return max(1, min(5, base))


# ──────────────────────────────────────────────────────────────────────────────
# Reward / Risk ratio
# ──────────────────────────────────────────────────────────────────────────────

def _reward_risk_ratio(
    prices: list[float],
    price: float,
    direction: str,
) -> float:
    """
    Compute reward-to-risk ratio using support/resistance as targets.

    BUY  : reward = distance to resistance (upside), risk = distance to support (stop)
    SELL : reward = distance to support (downside), risk = distance to resistance (stop)

    Capped at 10.0 to avoid division-by-near-zero artefacts.
    Returns 0.0 when S/R cannot be calculated or price is out of range.
    """
    sr = _calc_support_resistance(prices)
    if sr is None or price <= 0:
        return 0.0

    support, resistance, _ = sr

    if direction == "up":
        reward = max(resistance - price, 0.0)
        risk   = max(price - support,    0.0)
    elif direction == "down":
        reward = max(price - support,    0.0)
        risk   = max(resistance - price, 0.0)
    else:
        return 0.0

    if risk < 1e-6:
        return 0.0  # No meaningful stop distance → skip ratio

    return round(min(reward / risk, 10.0), 2)


# ──────────────────────────────────────────────────────────────────────────────
# Expected move estimate
# ──────────────────────────────────────────────────────────────────────────────

def _expected_move(
    prices: list[float],
    price: float,
    direction: str,
    hold_days: int,
) -> float:
    """
    Estimate % price move over hold_days trading sessions.

    Method: OLS slope extrapolated by hold_days × TICKS_PER_SESSION,
    weighted by R² (fit quality).  Conflicting direction dampened 70%.
    Clamped to ±MAX_EXPECTED_MOVE.
    """
    if hold_days == 0 or direction == "neutral" or price <= 0:
        return 0.0

    window  = prices[-REGRESSION_WINDOW:] if len(prices) >= REGRESSION_WINDOW else prices
    slope, r_sq = _linear_regression(window)

    projected       = price + slope * TICKS_PER_SESSION * hold_days
    raw_move_pct    = (projected - price) / price * 100.0
    weighted_move   = raw_move_pct * r_sq

    # Align to declared direction (dampen if regression contradicts)
    if direction == "up" and weighted_move < 0:
        weighted_move = abs(weighted_move) * 0.30
    elif direction == "down" and weighted_move > 0:
        weighted_move = -abs(weighted_move) * 0.30

    return round(
        max(-MAX_EXPECTED_MOVE, min(MAX_EXPECTED_MOVE, weighted_move)), 2
    )


# ──────────────────────────────────────────────────────────────────────────────
# Empty prediction (returned when data is insufficient)
# ──────────────────────────────────────────────────────────────────────────────

def _empty_prediction() -> dict:
    return {
        "direction":         "neutral",
        "confidence":        0.0,
        "trade_action":      "avoid",
        "time_horizon":      "n/a",
        "hold_days":         0,
        "risk":              "high",
        "basis":             [],
        "expected_move_pct": 0.0,
        "reward_risk_ratio": 0.0,
    }


# ──────────────────────────────────────────────────────────────────────────────
# PredictionEngine — public interface
# ──────────────────────────────────────────────────────────────────────────────

class PredictionEngine:
    """
    Enriches SignalEngine outputs with forward-looking prediction metadata.

    Contract
    --------
    - All computation is synchronous and fast (<50ms per symbol).
    - Reads price data from the shared in-memory ``price_buffer``.
    - Does NOT modify SignalEngine or mutate input signal dicts.
    - DB logging is async (fire-and-forget) — call via asyncio.create_task.
    """

    def __init__(self) -> None:
        self._vol_buffer = VolumeBuffer()

    # ── Public ──────────────────────────────────────────────────────────────

    def enrich(self, signal: dict) -> dict:
        """
        Enrich one signal dict with a ``prediction`` block.
        Returns a *new* dict (input is never mutated).

        Pipeline
        ────────
        1. Collect directional votes from momentum / RSI / Bollinger / S-R sub-models.
        2. Scale all vote strengths by a volume multiplier (last-5 avg comparison).
        3. Combine via weighted confidence formula → direction, confidence,
           total_strength, agreement_factor.
        4. Derive trade_action from confidence + strength + agreement thresholds.
        5. Compute dynamic hold_days from dominant signal type + volatility.
        6. Compute reward_risk_ratio from S/R levels.
        7. Compute expected_move_pct via OLS slope × hold period.
        """
        symbol  = signal.get("symbol", "")
        price   = float(signal.get("current") or 0)
        volume  = int(signal.get("volume") or 0)
        rsi     = signal.get("rsi")

        prices: list[float] = price_buffer.get(symbol)

        if len(prices) < MIN_PRICES or price <= 0:
            enriched = dict(signal)
            enriched["prediction"] = _empty_prediction()
            return enriched

        # ── 1. Update volume tracker ───────────────────────────────────────
        self._vol_buffer.push(symbol, volume)
        avg_recent5 = self._vol_buffer.avg_recent(symbol, VOLUME_RECENT)

        # ── 2. Directional sub-model votes (volume excluded — see step 3) ──
        votes: list[tuple[str, float, str]] = []

        mv = _momentum_vote(prices, price)
        if mv:
            votes.append(mv)

        rv = _rsi_vote(prices, rsi)
        if rv:
            votes.append(rv)

        bv = _bollinger_vote(prices, price)
        if bv:
            votes.append(bv)

        srv = _support_resistance_vote(prices, price)
        if srv:
            votes.append(srv)

        # ── 3. Apply volume multiplier to all vote strengths ───────────────
        vol_mult = _volume_multiplier(volume, avg_recent5)
        if vol_mult != 1.0:
            votes = [(v, min(s * vol_mult, 1.0), n) for v, s, n in votes]

        # ── 4. Weighted confidence combine ─────────────────────────────────
        direction, confidence, basis, total_str, agree = _combine_votes(votes)

        # ── 5. Trade action ────────────────────────────────────────────────
        if (
            direction == "neutral"
            or confidence  < MIN_CONFIDENCE_ACT
            or total_str   < MIN_TOTAL_STR_ACT
            or agree       < MIN_AGREEMENT_ACT
        ):
            trade_action = "avoid"
        else:
            trade_action = "buy" if direction == "up" else "sell"

        # ── 6. Dynamic hold duration ───────────────────────────────────────
        hold_days = _calc_hold_days(prices, votes, direction)

        # ── 7. Assemble prediction ─────────────────────────────────────────
        prediction = {
            "direction":         direction,
            "confidence":        confidence,
            "trade_action":      trade_action,
            "time_horizon":      f"~{hold_days} days" if hold_days > 0 else "n/a",
            "hold_days":         hold_days,
            "risk":              _risk_level(prices, confidence, rsi),
            "basis":             basis,
            "expected_move_pct": _expected_move(prices, price, direction, hold_days),
            "reward_risk_ratio": _reward_risk_ratio(prices, price, direction),
        }

        enriched = dict(signal)
        enriched["prediction"] = prediction
        return enriched

    def enrich_batch(self, signals: list[dict]) -> list[dict]:
        """Enrich a list of signals. Returns a new list; original is unchanged."""
        return [self.enrich(s) for s in signals]

    async def log_predictions(self, signals: list[dict]) -> None:
        """
        Persist non-neutral predictions for BUY/SELL/FORCE_SELL signals
        to the ``prediction_log`` table.

        Only logs actionable predictions to keep the table lean.
        Designed to be called via ``asyncio.create_task`` (non-blocking).
        """
        now  = int(time.time())
        rows: list[PredictionLog] = []

        for s in signals:
            pred      = s.get("prediction", {})
            direction = pred.get("direction", "neutral")
            sig_type  = s.get("signal", "HOLD")

            # Only log directional predictions on actionable signals
            if direction == "neutral":
                continue
            if sig_type not in ("BUY", "SELL", "FORCE_SELL"):
                continue

            rows.append(PredictionLog(
                symbol              = s.get("symbol", "").upper(),
                prediction_type     = f"{direction}_signal",
                predicted_at        = now,
                price_at_prediction = float(s.get("current") or 0),
                predicted_direction = direction,
                confidence          = round(
                    min(float(pred.get("confidence") or 0), 0.85), 4
                ),
                time_horizon_days   = pred.get("hold_days") or None,
                target_price        = None,
                outcome             = "pending",
            ))

        if not rows:
            return

        try:
            async with get_session() as session:
                session.add_all(rows)
            logger.debug("Logged %d predictions to DB", len(rows))
        except Exception as exc:
            logger.error("Failed to log predictions: %s", exc)
