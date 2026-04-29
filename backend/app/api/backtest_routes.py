"""
Backtest API routes.

Endpoints:
  POST /api/backtest/run         → run a single backtest (or multi-variant comparison)
  POST /api/backtest/walk-forward → walk-forward validation for one config
  GET  /api/backtest/results     → retrieve stored backtest results
  GET  /api/backtest/presets     → list available preset strategy configs

In-session result store:
  Results are kept in an in-process list (capped at 50).
  This is intentionally simple — no DB persistence for backtest output.
  The source of truth is always the price_history table; re-run anytime.

Request body for POST /api/backtest/run:
  {
    "symbol":    "ENGRO",
    "start_ts":  null,             // optional Unix timestamp
    "end_ts":    null,
    "mode":      "single",         // "single" | "variants" | "presets"
    "config": {                    // only for mode=single
      "name":                 "my_config",
      "rsi_oversold":         30,
      "rsi_overbought":       70,
      "sma_short":            5,
      "sma_long":             20,
      "stop_loss_pct":        5.0,
      "change_pct_threshold": 3.0,
      "position_size_pct":    1.0,
      "starting_cash":        100000.0
    },
    "variants": [...]              // only for mode=variants (list of configs)
  }
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..strategy.backtester import (
    Backtester,
    StrategyConfig,
    PRESET_VARIANTS,
    BacktestResult,
    WalkForwardResult,
)

backtest_router = APIRouter(prefix="/api/backtest")

# ── In-process result store (capped) ─────────────────────────────────────────
_MAX_STORED = 50
_results: list[dict] = []        # newest at front


def _store_result(result_dict: dict) -> None:
    """Prepend result to in-memory store. Trim to _MAX_STORED."""
    _results.insert(0, result_dict)
    if len(_results) > _MAX_STORED:
        _results.pop()


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class StrategyConfigSchema(BaseModel):
    name:                  str   = "default"
    rsi_period:            int   = Field(14, ge=2, le=50)
    rsi_oversold:          float = Field(30.0, ge=1, le=49)
    rsi_overbought:        float = Field(70.0, ge=51, le=99)
    sma_short:             int   = Field(5, ge=2, le=50)
    sma_long:              int   = Field(20, ge=5, le=200)
    stop_loss_pct:         float = Field(5.0, ge=0.5, le=30.0)
    change_pct_threshold:  float = Field(3.0, ge=0.5, le=20.0)
    position_size_pct:     float = Field(1.0, ge=0.1, le=1.0)
    starting_cash:         float = Field(100_000.0, ge=1_000.0)


class BacktestRunRequest(BaseModel):
    symbol:    str
    start_ts:  Optional[int] = None
    end_ts:    Optional[int] = None
    mode:      str            = Field("single", pattern="^(single|variants|presets)$")
    config:    Optional[StrategyConfigSchema] = None
    variants:  Optional[list[StrategyConfigSchema]] = None


class WalkForwardRequest(BaseModel):
    symbol:      str
    n_windows:   int   = Field(3, ge=2, le=10)
    train_ratio: float = Field(0.7, ge=0.5, le=0.9)
    config:      Optional[StrategyConfigSchema] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _schema_to_config(s: StrategyConfigSchema) -> StrategyConfig:
    return StrategyConfig(
        name=s.name,
        rsi_period=s.rsi_period,
        rsi_oversold=s.rsi_oversold,
        rsi_overbought=s.rsi_overbought,
        sma_short=s.sma_short,
        sma_long=s.sma_long,
        stop_loss_pct=s.stop_loss_pct,
        change_pct_threshold=s.change_pct_threshold,
        position_size_pct=s.position_size_pct,
        starting_cash=s.starting_cash,
    )


def _result_to_summary(r: BacktestResult) -> dict:
    """Lean summary dict (no trade_log / equity_curve) for GET /results."""
    return {
        "strategy":         r.strategy,
        "symbol":           r.symbol,
        "return_pct":       r.return_pct,
        "win_rate":         r.win_rate,
        "max_drawdown_pct": r.max_drawdown_pct,
        "profit_factor":    r.profit_factor,
        "sharpe_ratio":     r.sharpe_ratio,
        "trades":           r.trades,
        "winning_trades":   r.winning_trades,
        "losing_trades":    r.losing_trades,
        "ticks_used":       r.ticks_used,
        "starting_cash":    r.starting_cash,
        "final_equity":     r.final_equity,
        "start_ts":         r.start_ts,
        "end_ts":           r.end_ts,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@backtest_router.get("/presets")
async def get_presets():
    """List the built-in strategy presets available for testing."""
    return {
        "presets": [
            {
                "name": c.name,
                "rsi_oversold":         c.rsi_oversold,
                "rsi_overbought":       c.rsi_overbought,
                "sma_short":            c.sma_short,
                "sma_long":             c.sma_long,
                "stop_loss_pct":        c.stop_loss_pct,
                "change_pct_threshold": c.change_pct_threshold,
            }
            for c in PRESET_VARIANTS
        ]
    }


@backtest_router.post("/run")
async def run_backtest(req: BacktestRunRequest):
    """
    Run a backtest for a symbol.

    mode=single   → one config, full result with trade_log + equity_curve
    mode=variants → multiple configs, ranked by return_pct
    mode=presets  → run all 4 built-in presets, compare side by side
    """
    sym = req.symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")

    bt = Backtester()
    ran_at = int(time.time())

    if req.mode == "single":
        cfg = _schema_to_config(req.config) if req.config else StrategyConfig()
        result = await bt.run(sym, cfg, req.start_ts, req.end_ts)
        payload = result.to_dict()
        payload["ran_at"] = ran_at
        payload["mode"]   = "single"
        _store_result({**_result_to_summary(result), "ran_at": ran_at, "mode": "single"})
        return payload

    elif req.mode == "variants":
        if not req.variants:
            raise HTTPException(
                status_code=400,
                detail="variants list is required for mode=variants"
            )
        configs = [_schema_to_config(v) for v in req.variants]
        results = await bt.run_variants(sym, configs, req.start_ts, req.end_ts)
        for r in results:
            _store_result({**_result_to_summary(r), "ran_at": ran_at, "mode": "variants"})
        return {
            "mode":    "variants",
            "symbol":  sym,
            "ran_at":  ran_at,
            "count":   len(results),
            "results": [r.to_dict() for r in results],
        }

    elif req.mode == "presets":
        results = await bt.run_variants(sym, PRESET_VARIANTS, req.start_ts, req.end_ts)
        for r in results:
            _store_result({**_result_to_summary(r), "ran_at": ran_at, "mode": "presets"})
        return {
            "mode":    "presets",
            "symbol":  sym,
            "ran_at":  ran_at,
            "count":   len(results),
            "results": [
                {
                    "strategy":         r.strategy,
                    "return_pct":       r.return_pct,
                    "win_rate":         r.win_rate,
                    "max_drawdown_pct": r.max_drawdown_pct,
                    "profit_factor":    r.profit_factor,
                    "sharpe_ratio":     r.sharpe_ratio,
                    "trades":           r.trades,
                    "final_equity":     r.final_equity,
                    "ticks_used":       r.ticks_used,
                    "trade_log":        r.trade_log,
                    "equity_curve":     r.equity_curve,
                    "config":           r.config,
                }
                for r in results
            ],
        }


@backtest_router.post("/walk-forward")
async def run_walk_forward(req: WalkForwardRequest):
    """
    Walk-forward validation: split history into N windows, evaluate
    out-of-sample performance in each window to test strategy stability.
    """
    sym = req.symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")

    cfg    = _schema_to_config(req.config) if req.config else StrategyConfig()
    bt     = Backtester()
    result = await bt.run_walk_forward(sym, cfg, req.n_windows, req.train_ratio)
    ran_at = int(time.time())

    payload = {
        "mode":        "walk_forward",
        "strategy":    result.strategy,
        "symbol":      result.symbol,
        "ran_at":      ran_at,
        "n_windows":   req.n_windows,
        "train_ratio": req.train_ratio,
        "avg_return":  result.avg_return,
        "avg_win_rate": result.avg_win_rate,
        "stability_std": result.stability,
        "interpretation": _interpret_stability(result.stability, result.avg_return),
        "windows":     result.windows,
    }
    _store_result({
        "strategy":    result.strategy,
        "symbol":      sym,
        "avg_return":  result.avg_return,
        "avg_win_rate": result.avg_win_rate,
        "stability":   result.stability,
        "ran_at":      ran_at,
        "mode":        "walk_forward",
    })
    return payload


@backtest_router.get("/results")
async def get_results(
    limit: int = Query(20, ge=1, le=50),
    symbol: Optional[str] = Query(None),
):
    """
    Return stored backtest results from this session (newest-first).
    These are summary rows — no trade_log or equity_curve.
    """
    rows = _results
    if symbol:
        rows = [r for r in rows if r.get("symbol", "").upper() == symbol.upper()]
    return {
        "count":   len(rows[:limit]),
        "total":   len(rows),
        "results": rows[:limit],
    }


# ── Interpretation helper ─────────────────────────────────────────────────────

def _interpret_stability(stability: float, avg_return: float) -> str:
    """Human-readable verdict on walk-forward results."""
    if avg_return <= 0:
        return "Strategy is unprofitable on average — reconsider parameters."
    if stability < 2.0:
        return "Highly stable — consistent returns across windows. Good signal."
    if stability < 5.0:
        return "Moderate stability — some variance across windows. Use with caution."
    return "High variance across windows — strategy may be curve-fitted. Consider simpler config."
