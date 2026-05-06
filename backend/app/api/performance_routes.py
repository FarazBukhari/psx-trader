"""
Forward-Testing Performance API.

Endpoints:
  GET /api/performance/live     → all currently OPEN forward trades
  GET /api/performance/history  → CLOSED trades, paginated (newest-first)
  GET /api/performance/summary  → aggregate stats: win_rate, expectancy, MFE/MAE
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from typing import Optional

from ..analytics.forward_tracker import (
    get_closed_trades,
    get_open_trades,
    get_performance_summary,
)

performance_router = APIRouter(prefix="/api/performance", tags=["performance"])


@performance_router.get("/live")
async def live_trades():
    """
    All OPEN forward trades, ordered newest-first.

    Each row represents a signal that has been entered but not yet exited.
    max_price_seen / min_price_seen are updated every poll tick.
    """
    trades = await get_open_trades()
    return {"count": len(trades), "trades": trades}


@performance_router.get("/history")
async def trade_history(
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    limit:  int           = Query(50,   ge=1, le=500),
    offset: int           = Query(0,    ge=0),
):
    """
    Paginated CLOSED forward trades, newest-first.

    Optional `symbol` param filters to a single ticker.
    Response includes total count for frontend pagination.
    """
    trades, total = await get_closed_trades(
        limit=limit,
        offset=offset,
        symbol=symbol.strip().upper() if symbol else None,
    )
    return {
        "total":  total,
        "limit":  limit,
        "offset": offset,
        "trades": trades,
    }


@performance_router.get("/summary")
async def performance_summary():
    """
    Aggregate forward-test statistics across all CLOSED trades.

    Fields:
      win_rate      — % of closed trades that are WINs
      avg_win_pct   — average P&L % on winning trades
      avg_loss_pct  — average P&L % on losing trades (negative)
      expectancy    — expected P&L per trade
                      = win_rate × avg_win − (1 − win_rate) × |avg_loss|
      avg_mfe       — average max favourable excursion %
      avg_mae       — average max adverse excursion %
      total_closed  — total evaluated closed trades
      total_open    — currently open (tracking) trades

    Example:
      {
        "win_rate": 58.2, "avg_win_pct": 2.3, "avg_loss_pct": -1.4,
        "expectancy": 0.72, "avg_mfe": 3.1, "avg_mae": -1.2,
        "total_closed": 138, "total_open": 4
      }
    """
    return await get_performance_summary()
