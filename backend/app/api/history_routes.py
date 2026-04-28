"""
History API routes — read-only access to persisted price and signal history.

Endpoints:
  GET /api/history/{symbol}              → price history (OHLCV ticks)
  GET /api/history/{symbol}/signals      → signal history for a symbol
  GET /api/history/stats                 → DB stats (tick counts per symbol)
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..state import app_state

history_router = APIRouter(prefix="/api/history")


@history_router.get("/stats")
async def get_history_stats():
    """
    Return DB diagnostic stats: tick count and time range per symbol.
    Useful for verifying history is being stored correctly.
    """
    stats = await app_state.history_store.get_history_stats()
    return {
        "symbols": len(stats),
        "data":    stats,
    }


@history_router.get("/{symbol}")
async def get_price_history(
    symbol: str,
    n: int = Query(200, ge=1, le=2000, description="Number of ticks to return (max 2000)"),
    since: Optional[int] = Query(None, description="Return only ticks after this Unix timestamp"),
):
    """
    Return up to `n` OHLCV price ticks for a symbol, ordered oldest → newest.
    Designed for sparkline / mini-chart rendering on the frontend.

    - `n=200`  → default (fills SMA20, RSI14 buffers with room to spare)
    - `n=2000` → max (approximately 8 hours of 15s ticks, or 33 days of daily closes)
    - `since`  → Unix timestamp to filter from (useful for incremental loads)
    """
    sym = symbol.upper()

    history = await app_state.history_store.get_history(sym, n=n, since=since)

    if not history:
        raise HTTPException(
            status_code=404,
            detail=f"No price history found for '{sym}'. "
                   f"Data accumulates as the server polls PSX.",
        )

    return {
        "symbol":  sym,
        "count":   len(history),
        "data":    history,
    }


@history_router.get("/{symbol}/signals")
async def get_signal_history(
    symbol: str,
    limit: int = Query(50, ge=1, le=500, description="Number of signals to return"),
):
    """
    Return recent signals for a symbol (newest first).
    Only non-HOLD or changed signals are stored, so this represents
    meaningful signal transitions only.
    """
    sym = symbol.upper()

    signals = await app_state.history_store.get_recent_signals(sym, limit=limit)

    if not signals:
        raise HTTPException(
            status_code=404,
            detail=f"No signal history found for '{sym}'.",
        )

    return {
        "symbol": sym,
        "count":  len(signals),
        "data":   signals,
    }
