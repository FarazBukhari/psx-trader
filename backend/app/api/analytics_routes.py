"""
Analytics API — Signal Validation Engine results.

Endpoints:
  GET /api/analytics/summary           → global accuracy metrics across all symbols
  GET /api/analytics/symbol/{symbol}   → per-symbol breakdown
  GET /api/analytics/sources           → per-indicator (RSI/SMA/momentum/volume) accuracy
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..analytics.signal_evaluator import (
    get_global_summary,
    get_source_accuracy,
    get_symbol_summary,
)

analytics_router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@analytics_router.get("/summary")
async def analytics_summary():
    """
    Global accuracy metrics across all evaluated signals.

    Returns overall short/medium/long accuracy percentages, average return,
    and a per-signal-type breakdown (BUY/SELL/HOLD/FORCE_SELL).
    """
    return await get_global_summary()


@analytics_router.get("/symbol/{symbol}")
async def analytics_symbol(symbol: str):
    """
    Per-symbol accuracy breakdown.

    Returns accuracy % at each horizon, average return, and best/worst signal type.
    404 if the symbol has no evaluated signals yet.
    """
    result = await get_symbol_summary(symbol)
    if result.get("total_signals", 0) == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No evaluated signals found for symbol '{symbol.upper()}'",
        )
    return result


@analytics_router.get("/sources")
async def analytics_sources():
    """
    Per-indicator accuracy.

    Deserialises signal_sources JSON from each outcome row and aggregates
    accuracy at short/medium/long horizons per source (RSI, SMA, momentum, volume).
    """
    return await get_source_accuracy()
