"""
Forward-Testing Performance API.

Endpoints:
  GET /api/performance/live       → all currently OPEN forward trades
  GET /api/performance/history    → CLOSED trades, paginated (newest-first)
  GET /api/performance/summary    → aggregate stats: win_rate, expectancy, MFE/MAE
  GET /api/performance/benchmark  → KSE-100 proxy price ticks, normalised to base 100
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from typing import Optional

from sqlalchemy import select as sa_select

from ..analytics.forward_tracker import (
    get_closed_trades,
    get_open_trades,
    get_performance_summary,
)
from ..db.database import get_session
from ..db.models import PriceHistory

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


@performance_router.get("/benchmark")
async def benchmark_prices(
    since: int           = Query(..., description="Start Unix timestamp (first trade exit_time)"),
    until: Optional[int] = Query(None, description="End Unix timestamp (last trade exit_time)"),
    proxy: str           = Query("OGDC", description="PSX ticker to use as KSE-100 proxy"),
    n:     int           = Query(200, ge=2, le=2000, description="Max ticks to return"),
):
    """
    Return price history for a proxy symbol normalised to base 100 at `since`.

    Used by the Performance Chart to draw a KSE-100 comparison line alongside
    the portfolio equity curve.  Ticks are evenly sampled up to `n` rows so
    the frontend always receives a fixed-size dataset regardless of tick density.

    Response shape:
      {
        "proxy":  "OGDC",
        "points": [{"time": 1714500000, "value": 100.0}, ...]
      }

    Returns an empty `points` list if no price data exists for the proxy in range.
    """
    sym = proxy.strip().upper()

    try:
        async with get_session() as session:
            q = (
                sa_select(PriceHistory.scraped_at, PriceHistory.close)
                .where(
                    PriceHistory.symbol == sym,
                    PriceHistory.scraped_at >= since,
                    PriceHistory.close.isnot(None),
                )
            )
            if until:
                q = q.where(PriceHistory.scraped_at <= until)

            q = q.order_by(PriceHistory.scraped_at.asc()).limit(n)
            rows = (await session.execute(q)).all()
    except Exception as exc:
        return {"proxy": sym, "points": [], "error": str(exc)}

    if not rows:
        return {"proxy": sym, "points": []}

    base_close = rows[0].close
    if not base_close:
        return {"proxy": sym, "points": []}

    points = [
        {"time": r.scraped_at, "value": round(r.close / base_close * 100, 4)}
        for r in rows
    ]
    return {"proxy": sym, "points": points}
