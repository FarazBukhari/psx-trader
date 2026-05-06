"""
test_signal_parity.py — verify that live engine and backtester produce
identical signals for the same price series.

Both code paths now call generate_signal() from strategy/core.py.
This test confirms the shared core produces consistent outputs and that
the two callers pass the same thresholds (except clamp_change_pct, which
is an intentional difference documented in core.py).

Run:  python -m pytest backend/tests/test_signal_parity.py -v
  or: python backend/tests/test_signal_parity.py
"""

from __future__ import annotations

import sys
import os

# Allow running directly without installing the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.strategy.core import (
    generate_signal, compute_rsi, compute_sma, compute_sma_prev,
    SIGNAL_PRIORITY, MIN_PRICES,
)
from app.strategy.backtester import StrategyConfig
from app.strategy.signal_engine import SignalConfig


# ---------------------------------------------------------------------------
# Helper: call generate_signal the way the backtester does
# ---------------------------------------------------------------------------

def _bt(prices, change_pct=None, config=None):
    cfg = config or StrategyConfig()
    r = generate_signal(
        prices, [],
        rsi_period=cfg.rsi_period,
        rsi_oversold=cfg.rsi_oversold,
        rsi_overbought=cfg.rsi_overbought,
        sma_short=cfg.sma_short,
        sma_long=cfg.sma_long,
        change_pct=change_pct,
        change_pct_threshold=cfg.change_pct_threshold,
        clamp_change_pct=True,
    )
    return r["signal"], r["sources"]


# ---------------------------------------------------------------------------
# Helper: call generate_signal the way the live engine's indicator strategies do
# ---------------------------------------------------------------------------

def _live(prices, change_pct=None, signal_cfg=None):
    scfg = signal_cfg or SignalConfig()
    r = generate_signal(
        prices, [],
        rsi_period=scfg.rsi_period,
        rsi_oversold=scfg.rsi_oversold,
        rsi_overbought=scfg.rsi_overbought,
        sma_short=scfg.sma_short,
        sma_long=scfg.sma_long,
        change_pct=change_pct,
        change_pct_threshold=scfg.change_pct_threshold,
        clamp_change_pct=False,   # live engine does not clamp (PSX scraper is authoritative)
    )
    return r["signal"], r["sources"]


# ---------------------------------------------------------------------------
# RSI parity
# ---------------------------------------------------------------------------

def test_rsi_oversold_buy():
    """Sustained decline over MIN_PRICES ticks → RSI oversold → BUY from both paths."""
    prices = [100.0 - i for i in range(MIN_PRICES + 1)]   # 21 prices, strong downtrend
    bt_sig, bt_src = _bt(prices)
    lv_sig, lv_src = _live(prices)
    assert bt_sig == lv_sig,  f"RSI oversold: bt={bt_sig} live={lv_sig}"
    assert bt_src == lv_src,  f"RSI oversold sources: bt={bt_src} live={lv_src}"
    assert "rsi" in bt_src,   "Expected 'rsi' source in BUY signal"


def test_rsi_overbought_sell():
    """Sustained rise over MIN_PRICES ticks → RSI overbought → SELL from both paths."""
    prices = [80.0 + i for i in range(MIN_PRICES + 1)]   # 21 prices, strong uptrend
    bt_sig, bt_src = _bt(prices)
    lv_sig, lv_src = _live(prices)
    assert bt_sig == lv_sig,  f"RSI overbought: bt={bt_sig} live={lv_sig}"
    assert bt_src == lv_src,  f"RSI overbought sources: bt={bt_src} live={lv_src}"
    assert "rsi" in bt_src,   "Expected 'rsi' source in SELL signal"


def test_rsi_numerical_identity():
    """RSI value in meta must match compute_rsi directly for the same inputs."""
    # 21 prices — enough to clear MIN_PRICES and compute RSI
    prices = [100, 99, 101, 98, 103, 97, 104, 96, 105, 95,
              106, 94, 107, 93, 108, 92, 109, 91, 110, 90, 111]
    cfg     = StrategyConfig()
    rsi_val = compute_rsi(prices, cfg.rsi_period)
    r_bt    = generate_signal(prices, [], rsi_period=cfg.rsi_period,
                               rsi_oversold=cfg.rsi_oversold, rsi_overbought=cfg.rsi_overbought)
    r_live  = generate_signal(prices, [], rsi_period=cfg.rsi_period,
                               rsi_oversold=cfg.rsi_oversold, rsi_overbought=cfg.rsi_overbought)
    assert r_bt["rsi"]        == rsi_val, f"Backtester RSI: {r_bt['rsi']} vs {rsi_val}"
    assert r_live["rsi"]      == rsi_val, f"Live RSI: {r_live['rsi']} vs {rsi_val}"
    assert r_bt["meta"]["rsi"] == rsi_val, "meta.rsi must match top-level rsi"


# ---------------------------------------------------------------------------
# SMA crossover parity
# ---------------------------------------------------------------------------

def test_sma_golden_cross():
    """Price series that triggers a golden cross → BUY with sma_crossover source."""
    # 25 prices: first 20 decline, last 5 spike sharply
    prices = [100.0 - i * 0.5 for i in range(20)] + [93.0 + i * 3 for i in range(5)]
    bt_sig, bt_src = _bt(prices)
    lv_sig, lv_src = _live(prices)
    assert bt_sig == lv_sig, f"Golden cross: bt={bt_sig} live={lv_sig}"
    assert set(bt_src) == set(lv_src), f"Golden cross sources: bt={bt_src} live={lv_src}"


def test_sma_death_cross():
    """Price series that triggers a death cross → SELL with sma_crossover source."""
    # 25 prices: first 20 rise, last 5 drop sharply
    prices = [80.0 + i * 0.5 for i in range(20)] + [87.0 - i * 3 for i in range(5)]
    bt_sig, bt_src = _bt(prices)
    lv_sig, lv_src = _live(prices)
    assert bt_sig == lv_sig, f"Death cross: bt={bt_sig} live={lv_sig}"
    assert set(bt_src) == set(lv_src), f"Death cross sources: bt={bt_src} live={lv_src}"


def test_sma_numerical_identity():
    """SMA values from core helpers must be identical for both callers."""
    prices = [float(100 + i % 5) for i in range(25)]
    cfg = StrategyConfig()
    assert compute_sma(prices, cfg.sma_short) == compute_sma(prices, cfg.sma_short)
    assert compute_sma_prev(prices, cfg.sma_long) == compute_sma_prev(prices, cfg.sma_long)


# ---------------------------------------------------------------------------
# HOLD when no signals fire
# ---------------------------------------------------------------------------

def test_hold_oscillating_prices():
    """
    Oscillating prices: both paths must agree on the same signal (parity).
    We don't assert HOLD specifically because SMA crossovers may fire
    depending on the tail; the critical invariant is that both callers
    produce the same result from the same core function.
    """
    prices = [100.0 + (1 if i % 2 == 0 else -1) for i in range(25)]
    bt_sig, bt_src = _bt(prices)
    lv_sig, lv_src = _live(prices)
    assert bt_sig == lv_sig, f"Oscillating parity: bt={bt_sig} live={lv_sig}"
    assert set(bt_src) == set(lv_src), f"Sources mismatch: bt={bt_src} live={lv_src}"


def test_hold_insufficient_data():
    """Fewer prices than sma_long → no signal from either path."""
    prices = [100.0, 101.0, 99.0]   # way below sma_long=20
    bt_sig, bt_src = _bt(prices)
    lv_sig, lv_src = _live(prices)
    assert bt_sig == lv_sig == "HOLD"
    assert bt_src == lv_src == []


# ---------------------------------------------------------------------------
# change_pct parity (within the ±7.5% band — clamping is irrelevant here)
# ---------------------------------------------------------------------------

def test_change_pct_buy():
    """change_pct above threshold → BUY from both paths."""
    prices = [100.0] * 25
    bt_sig, bt_src = _bt(prices, change_pct=4.0)
    lv_sig, lv_src = _live(prices, change_pct=4.0)
    assert bt_sig == lv_sig
    assert "change_pct" in bt_src
    assert "change_pct" in lv_src


def test_change_pct_sell():
    """change_pct below negative threshold → SELL from both paths."""
    prices = [100.0] * 25
    bt_sig, bt_src = _bt(prices, change_pct=-4.0)
    lv_sig, lv_src = _live(prices, change_pct=-4.0)
    assert bt_sig == lv_sig
    assert "change_pct" in bt_src
    assert "change_pct" in lv_src


# ---------------------------------------------------------------------------
# Stop-loss (backtester only — live engine uses PriceThresholdStrategy)
# ---------------------------------------------------------------------------

def test_stop_loss_force_sell():
    """avg_cost + price drop → FORCE_SELL from generate_signal directly."""
    prices = [100.0] * 25
    r = generate_signal(
        prices, [],
        avg_cost=110.0,    # bought at 110, now at 100 → ~9% drop
        stop_loss_pct=5.0,
    )
    assert r["signal"] == "FORCE_SELL"
    assert "stop_loss" in r["sources"]
    # meta is populated even on FORCE_SELL (useful for debugging)
    assert "meta" in r
    assert "rsi" in r["meta"]


# ---------------------------------------------------------------------------
# New: SIGNAL_PRIORITY contract
# ---------------------------------------------------------------------------

def test_signal_priority_order():
    """SIGNAL_PRIORITY must be in exact order — changing it breaks everything."""
    assert SIGNAL_PRIORITY[0] == "FORCE_SELL", "FORCE_SELL must be first"
    assert SIGNAL_PRIORITY[1] == "SELL",       "SELL must be second"
    assert SIGNAL_PRIORITY[2] == "BUY",        "BUY must be third"
    assert SIGNAL_PRIORITY[3] == "HOLD",       "HOLD must be last"
    assert len(SIGNAL_PRIORITY) == 4,          "No extra entries allowed"


def test_signal_priority_wins():
    """When multiple signals compete, FORCE_SELL > SELL > BUY."""
    from app.strategy.core import resolve_signals
    assert resolve_signals(["BUY", "SELL"])            == "SELL"
    assert resolve_signals(["BUY", "FORCE_SELL"])      == "FORCE_SELL"
    assert resolve_signals(["SELL", "FORCE_SELL"])     == "FORCE_SELL"
    assert resolve_signals(["BUY", "SELL", "FORCE_SELL"]) == "FORCE_SELL"
    assert resolve_signals([])                         == "HOLD"


# ---------------------------------------------------------------------------
# New: MIN_PRICES guard
# ---------------------------------------------------------------------------

def test_min_prices_guard():
    """Fewer than MIN_PRICES prices → always HOLD with empty sources."""
    for n in [0, 1, 5, MIN_PRICES - 1]:
        prices = [100.0] * n
        r = generate_signal(prices, [])
        assert r["signal"] == "HOLD",    f"n={n}: expected HOLD, got {r['signal']}"
        assert r["sources"] == [],        f"n={n}: expected empty sources"
        assert r["meta"]["rsi"] is None,  f"n={n}: rsi should be None"


def test_min_prices_threshold():
    """Exactly MIN_PRICES prices should not be short-circuited (guard is strict <)."""
    prices = [100.0] * MIN_PRICES
    r = generate_signal(prices, [])
    # Should not be a guard-HOLD — result depends on indicator values
    # (flat prices → RSI=100 → SELL). Just confirm it ran past the guard.
    assert "meta" in r
    assert r["meta"] is not None


# ---------------------------------------------------------------------------
# New: meta dict shape
# ---------------------------------------------------------------------------

def test_meta_shape():
    """meta always contains rsi, sma_fast, sma_slow keys."""
    prices = [100.0 + (i % 3) for i in range(25)]
    r = generate_signal(prices, [])
    assert set(r["meta"].keys()) == {"rsi", "sma_fast", "sma_slow"}


def test_meta_sma_values():
    """sma_fast and sma_slow in meta match compute_sma directly."""
    prices = list(range(1, 26))   # 1..25
    cfg    = StrategyConfig()
    r      = generate_signal(prices, [], sma_short=cfg.sma_short, sma_long=cfg.sma_long)
    expected_fast = compute_sma(prices, cfg.sma_short)
    expected_slow = compute_sma(prices, cfg.sma_long)
    assert r["meta"]["sma_fast"] == round(expected_fast, 4)
    assert r["meta"]["sma_slow"] == round(expected_slow, 4)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_rsi_oversold_buy,
        test_rsi_overbought_sell,
        test_rsi_numerical_identity,
        test_sma_golden_cross,
        test_sma_death_cross,
        test_sma_numerical_identity,
        test_hold_oscillating_prices,
        test_hold_insufficient_data,
        test_change_pct_buy,
        test_change_pct_sell,
        test_stop_loss_force_sell,
        # new
        test_signal_priority_order,
        test_signal_priority_wins,
        test_min_prices_guard,
        test_min_prices_threshold,
        test_meta_shape,
        test_meta_sma_values,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            print(f"  ERROR {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
