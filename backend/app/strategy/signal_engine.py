"""
Signal Engine — applies rule-based + indicator strategies to stock data.

Signal values: "BUY" | "SELL" | "HOLD" | "FORCE_SELL"

Extensibility:
  - Add new strategies by subclassing BaseStrategy and registering in STRATEGY_REGISTRY
  - The engine applies all registered strategies and merges results
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from typing import Optional

from .core import compute_rsi, compute_sma, compute_sma_prev, resolve_signals

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal configuration (mirrors StrategyConfig in backtester, but lighter)
# ---------------------------------------------------------------------------

@dataclass
class SignalConfig:
    """Thresholds used by the live signal engine. Populated from a preset."""
    rsi_period:           int   = 14
    rsi_oversold:         float = 30.0
    rsi_overbought:       float = 70.0
    sma_short:            int   = 5
    sma_long:             int   = 20
    change_pct_threshold: float = 3.0

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "../../../config/strategy.json"
)


def load_config(path: Optional[str] = None) -> dict:
    p = path or _CONFIG_PATH
    try:
        with open(os.path.abspath(p)) as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("strategy.json not found at %s — using defaults", p)
        return {"symbols": {}, "global": {}}
    except json.JSONDecodeError as e:
        logger.error("Invalid strategy.json: %s", e)
        return {"symbols": {}, "global": {}}


# ---------------------------------------------------------------------------
# Price history buffer (for indicator calculations)
# ---------------------------------------------------------------------------

class PriceBuffer:
    """Circular buffer storing recent closing prices per symbol."""

    def __init__(self, maxlen: int = 200):
        self._data: dict[str, deque[float]] = {}
        self._maxlen = maxlen

    def push(self, symbol: str, price: float):
        if symbol not in self._data:
            self._data[symbol] = deque(maxlen=self._maxlen)
        self._data[symbol].append(price)

    def get(self, symbol: str, n: Optional[int] = None) -> list[float]:
        buf = self._data.get(symbol, deque())
        prices = list(buf)
        return prices[-n:] if n else prices

    def len(self, symbol: str) -> int:
        return len(self._data.get(symbol, []))


# Shared singleton
price_buffer = PriceBuffer()


# ---------------------------------------------------------------------------
# Base strategy interface
# ---------------------------------------------------------------------------

class BaseStrategy(ABC):
    name: str = "base"

    @abstractmethod
    def evaluate(self, stock: dict, config: dict, signal_cfg: SignalConfig = None) -> Optional[str]:
        """
        Return "BUY", "SELL", "FORCE_SELL", "HOLD", or None (abstain).
        None means this strategy has no opinion for this stock.
        signal_cfg carries the active preset thresholds (RSI, SMA, change %).
        """


# ---------------------------------------------------------------------------
# Strategy 1: Price threshold (core rule-based)
# ---------------------------------------------------------------------------

class PriceThresholdStrategy(BaseStrategy):
    name = "price_threshold"

    def evaluate(self, stock: dict, config: dict, signal_cfg: SignalConfig = None) -> Optional[str]:
        sym = stock["symbol"]
        cfg = config.get("symbols", {}).get(sym)
        if not cfg:
            return None  # No config for this symbol → abstain

        price = stock["current"]
        if price <= 0:
            return None

        stop_loss   = cfg.get("stop_loss")
        buy_below   = cfg.get("buy_below")
        sell_above  = cfg.get("sell_above")

        if stop_loss and price <= stop_loss:
            return "FORCE_SELL"
        if sell_above and price >= sell_above:
            return "SELL"
        if buy_below and price <= buy_below:
            return "BUY"
        return "HOLD"


# ---------------------------------------------------------------------------
# Strategy 2: Volume spike detector
# ---------------------------------------------------------------------------

class VolumeSpikeStrategy(BaseStrategy):
    """
    Fires BUY when current volume exceeds the per-symbol 20-day rolling
    average by more than `volume_spike_threshold` (default 2.5×).

    The rolling average is maintained in-process:
      - Warmed at startup from price_history via HistoryStore.warm_volume_baselines()
      - Updated on every evaluate() call with the live volume reading

    This replaces the previous 1 M hardcoded baseline, which was meaningless
    for thin-volume PSX small-caps and wrong for high-volume large-caps.
    """

    name = "volume_spike"
    _WINDOW = 20   # rolling window size (20 trading days ≈ one calendar month)

    def __init__(self) -> None:
        self._buffers: dict[str, deque] = {}

    # ── External warm-up interface (called once by HistoryStore) ──────────

    def push_volume(self, symbol: str, volume: int) -> None:
        """Append one volume reading to the rolling buffer for `symbol`."""
        if not volume or volume <= 0:
            return
        if symbol not in self._buffers:
            self._buffers[symbol] = deque(maxlen=self._WINDOW)
        self._buffers[symbol].append(volume)

    def avg_volume(self, symbol: str) -> float:
        """Return the rolling average volume for `symbol`, or 0.0 if unknown."""
        buf = self._buffers.get(symbol)
        if not buf:
            return 0.0
        return sum(buf) / len(buf)

    # ── Strategy interface ────────────────────────────────────────────────

    def evaluate(self, stock: dict, config: dict, signal_cfg: SignalConfig = None) -> Optional[str]:
        gcfg = config.get("global", {})
        if not gcfg.get("enable_volume_filter", True):
            return None

        threshold = gcfg.get("volume_spike_threshold", 2.5)
        symbol    = stock["symbol"]
        volume    = stock.get("volume") or 0

        # Update rolling buffer with this tick's volume
        self.push_volume(symbol, volume)

        avg_vol = self.avg_volume(symbol)
        if avg_vol <= 0:
            # No baseline yet — abstain rather than use a meaningless number
            return None

        if volume > avg_vol * threshold:
            # Volume spike confirms accumulation → lean BUY
            return "BUY"

        return None


# ---------------------------------------------------------------------------
# Strategy 3: Change % alert
# ---------------------------------------------------------------------------

class ChangePctStrategy(BaseStrategy):
    name = "change_pct"

    def evaluate(self, stock: dict, config: dict, signal_cfg: SignalConfig = None) -> Optional[str]:
        gcfg = config.get("global", {})
        if not gcfg.get("enable_change_pct_filter", True):
            return None

        cfg = signal_cfg or SignalConfig()
        threshold = cfg.change_pct_threshold
        chg = stock.get("change_pct", 0)

        if chg <= -threshold:
            return "SELL"      # Sharp drop → sell signal
        if chg >= threshold:
            return "BUY"       # Sharp rise → momentum buy
        return None


# ---------------------------------------------------------------------------
# Strategy 4: Simple Moving Average crossover (requires price history)
# ---------------------------------------------------------------------------

class SMACrossoverStrategy(BaseStrategy):
    name = "sma_crossover"

    def evaluate(self, stock: dict, config: dict, signal_cfg: SignalConfig = None) -> Optional[str]:
        sym   = stock["symbol"]
        cfg   = signal_cfg or SignalConfig()
        SHORT = cfg.sma_short
        LONG  = cfg.sma_long

        if price_buffer.len(sym) < LONG:
            return None  # Not enough data yet

        prices = price_buffer.get(sym)

        sma_s_now  = compute_sma(prices, SHORT)
        sma_l_now  = compute_sma(prices, LONG)
        sma_s_prev = compute_sma_prev(prices, SHORT)
        sma_l_prev = compute_sma_prev(prices, LONG)

        if any(v is None for v in (sma_s_now, sma_l_now, sma_s_prev, sma_l_prev)):
            return None

        if sma_s_prev <= sma_l_prev and sma_s_now > sma_l_now:
            return "BUY"   # Golden cross
        if sma_s_prev >= sma_l_prev and sma_s_now < sma_l_now:
            return "SELL"  # Death cross
        return None


# ---------------------------------------------------------------------------
# Strategy 5: RSI (Relative Strength Index)
# ---------------------------------------------------------------------------

class RSIStrategy(BaseStrategy):
    name = "rsi"

    def evaluate(self, stock: dict, config: dict, signal_cfg: SignalConfig = None) -> Optional[str]:
        sym = stock["symbol"]
        cfg = signal_cfg or SignalConfig()

        if price_buffer.len(sym) < cfg.rsi_period + 1:
            return None

        prices = price_buffer.get(sym, cfg.rsi_period + 1)
        rsi    = compute_rsi(prices, cfg.rsi_period)
        if rsi is None:
            return None

        stock["_rsi"] = rsi  # attach for API response

        if rsi <= cfg.rsi_oversold:
            return "BUY"
        if rsi >= cfg.rsi_overbought:
            return "SELL"
        return None


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

_volume_spike_strategy = VolumeSpikeStrategy()

STRATEGY_REGISTRY: list[BaseStrategy] = [
    PriceThresholdStrategy(),
    _volume_spike_strategy,
    ChangePctStrategy(),
    SMACrossoverStrategy(),
    RSIStrategy(),
]

# resolve_signals imported from core — single source of truth for priority merge


# ---------------------------------------------------------------------------
# Action Score — composite urgency ranking (horizon-aware)
# ---------------------------------------------------------------------------
#
# horizon = "short" | "long"
#
# SHORT-TERM weights (intraday / swing 1-3 days):
#   Priorities: volume liquidity, price momentum, intraday volatility,
#               freshness of signal, avoiding penny stocks
#
#   Base                     FORCE_SELL=10000  SELL/BUY=1000  HOLD=0
#   + source_count × 25      strategy agreement
#   + changed × 200          just flipped → act NOW
#   + abs(chg_pct) × 25      momentum weight (2× long-term)
#   + volume_tier bonus      >5M=300  1M-5M=150  500K-1M=75  <500K=0
#   + log10(volume) × 15     liquidity (3× long-term)
#   + intraday_range × 20    (high-low)/price % — volatility = opportunity
#   - penny_penalty          price < 10 PKR → -300 (hard to exit fast)
#   RSI: neutral zone (40-65) preferred — signal must come from momentum
#        not from deep oversold (that's a long-term play)
#
# LONG-TERM weights (weeks / months):
#   Priorities: RSI oversold/overbought extremes, price at support/resistance
#               (strategy.json thresholds), SMA crossover confirmation
#
#   Base                     same
#   + source_count × 40      more agreement = safer long-term entry
#   + changed × 80           freshness matters less
#   + abs(chg_pct) × 8       momentum matters less
#   + log10(volume) × 6      volume matters less (long-term ignores daily noise)
#   + rsi_edge × 6           RSI extremes weighted much higher (2× short-term)
#   + threshold_bonus        +200 if price_threshold strategy fired
#                             (you set these levels yourself in strategy.json)
#   + sma_bonus              +150 if sma_crossover fired (trend confirmation)
#
# ---------------------------------------------------------------------------

import math as _math


def compute_action_score(stock: dict, horizon: str = "short") -> float:
    signal   = stock.get("signal", "HOLD")
    sources  = stock.get("signal_sources", [])
    changed  = stock.get("signal_changed", False)
    chg_pct  = abs(stock.get("change_pct") or 0)
    volume   = stock.get("volume") or 0
    price    = stock.get("current") or 0
    high     = stock.get("high") or price
    low      = stock.get("low") or price
    rsi      = stock.get("rsi")

    if signal == "HOLD":
        return 0.0

    base = {"FORCE_SELL": 10_000, "SELL": 1_000, "BUY": 1_000}.get(signal, 0)

    if horizon == "short":
        # ── SHORT-TERM ──────────────────────────────────────────────────────

        # Strategy agreement
        confidence = len(sources) * 25

        # Freshness: just-changed signals are most urgent for short-term
        freshness = 200 if changed else 0

        # Price momentum — the primary short-term driver
        momentum = chg_pct * 25

        # Volume tier: absolute liquidity thresholds matter for quick exits
        if volume >= 5_000_000:
            vol_tier = 300
        elif volume >= 1_000_000:
            vol_tier = 150
        elif volume >= 500_000:
            vol_tier = 75
        else:
            vol_tier = 0   # thin volume = skip for short-term

        # Log-scale volume bonus on top
        liquidity = _math.log10(max(volume, 1)) * 15

        # Intraday range: (high - low) / price — bigger spread = more opportunity
        intraday_range = ((high - low) / price * 100) * 20 if price > 0 else 0

        # Penny stock penalty: price < 10 PKR = wide spreads, hard to exit
        penny_penalty = -300 if price < 10 else 0

        # RSI for short-term: reward momentum zone (45-65 rising, 35-55 falling)
        # Don't reward deep oversold — that's a slow mean-reversion play
        rsi_edge = 0.0
        if rsi is not None:
            if signal == "BUY" and 35 <= rsi <= 60:
                rsi_edge = (rsi - 35) * 2   # rising through mid-zone = momentum
            elif signal in ("SELL", "FORCE_SELL") and 40 <= rsi <= 70:
                rsi_edge = (70 - rsi) * 2

        score = base + confidence + freshness + momentum + vol_tier + liquidity + intraday_range + penny_penalty + rsi_edge

    else:
        # ── LONG-TERM ───────────────────────────────────────────────────────

        # Strategy agreement matters more — want multiple confirmations
        confidence = len(sources) * 40

        # Freshness matters less — long-term entries aren't time-critical
        freshness = 80 if changed else 0

        # Momentum matters less — you're buying value, not chasing price
        momentum = chg_pct * 8

        # Volume matters less day-to-day
        liquidity = _math.log10(max(volume, 1)) * 6

        # RSI extremes are the primary long-term signal
        rsi_edge = 0.0
        if rsi is not None:
            if signal == "BUY":
                rsi_edge = max(0.0, (40 - rsi) * 6)   # deep oversold = strong buy
            elif signal in ("SELL", "FORCE_SELL"):
                rsi_edge = max(0.0, (rsi - 60) * 6)   # deep overbought = strong sell

        # Price threshold bonus: you manually set these levels — trust them
        threshold_bonus = 200 if "price_threshold" in sources else 0

        # SMA crossover: golden/death cross = confirmed trend change
        sma_bonus = 150 if "sma_crossover" in sources else 0

        score = base + confidence + freshness + momentum + liquidity + rsi_edge + threshold_bonus + sma_bonus

    return round(max(0.0, score), 2)


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------

class SignalEngine:
    def __init__(self, config_path: Optional[str] = None):
        self.config = load_config(config_path)
        self._prev_signals: dict[str, str] = {}

    def reload_config(self, config_path: Optional[str] = None):
        self.config = load_config(config_path)
        logger.info("Strategy config reloaded")

    def process(
        self,
        stocks: list[dict],
        signal_cfg: SignalConfig = None,
        horizon: str = "long",
    ) -> list[dict]:
        """
        Takes a list of stock dicts (from scraper) and returns enriched
        dicts with 'signal', 'signal_sources', action_score, and indicator fields.

        signal_cfg: active preset thresholds — built from app_state.signal_cfg.
        horizon:    internal scoring mode derived via preset_to_horizon(); callers
                    should not set this directly — use set_strategy() instead.
        """
        cfg = signal_cfg or SignalConfig()
        results = []
        for stock in stocks:
            sym = stock["symbol"]

            # Update price history buffer
            price_buffer.push(sym, stock["current"])

            # Collect signals from all strategies
            opinions: list[str] = []
            sources: list[str]  = []
            for strategy in STRATEGY_REGISTRY:
                try:
                    opinion = strategy.evaluate(stock, self.config, cfg)
                    if opinion:
                        opinions.append(opinion)
                        sources.append(strategy.name)
                except Exception as exc:
                    logger.warning("Strategy %s error on %s: %s", strategy.name, sym, exc)

            signal = resolve_signals(opinions)

            # Detect signal change
            prev = self._prev_signals.get(sym)
            changed = prev is not None and prev != signal
            self._prev_signals[sym] = signal

            enriched = {
                **stock,
                "signal":          signal,
                "signal_sources":  sources,
                "signal_changed":  changed,
                "prev_signal":     prev or "—",
                "rsi":             stock.pop("_rsi", None),
                # sma5/sma20 keys kept for DB/history compatibility;
                # values now reflect the active preset's SMA windows
                "sma5":            self._sma(sym, cfg.sma_short),
                "sma20":           self._sma(sym, cfg.sma_long),
            }
            enriched["action_score"] = compute_action_score(enriched, horizon)
            results.append(enriched)

        return results

    @staticmethod
    def _sma(symbol: str, n: int) -> Optional[float]:
        prices = price_buffer.get(symbol, n)
        val    = compute_sma(prices, n)
        return round(val, 2) if val is not None else None
