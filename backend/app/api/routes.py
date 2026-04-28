"""
REST API routes — served by FastAPI.

Endpoints:
  GET  /api/stocks         → latest raw stock data
  GET  /api/signals        → latest enriched signals
  GET  /api/signals/{sym}  → signal for a specific symbol
  GET  /api/status         → system health + market status + stale flag
  POST /api/config/reload  → hot-reload strategy.json
  POST /api/horizon/{mode} → switch scoring horizon (short | long)
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..market_hours import market_status
from ..state import app_state

router = APIRouter(prefix="/api")


@router.get("/stocks")
async def get_stocks(
    sector: Optional[str] = Query(None, description="Filter by sector"),
    limit: int = Query(100, ge=1, le=500),
):
    stocks = list(app_state.stocks.values())
    if sector:
        stocks = [s for s in stocks if s.get("sector", "").upper() == sector.upper()]
    return {
        "count":      len(stocks),
        "updated_at": app_state.last_update,
        "stale":      app_state.data_stale,
        "stale_reason": app_state.stale_reason,
        "data":       stocks[:limit],
    }


@router.get("/signals")
async def get_signals(
    signal: Optional[str] = Query(None, description="Filter: BUY | SELL | HOLD | FORCE_SELL"),
    sector: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    signals = list(app_state.signals.values())

    if signal:
        signals = [s for s in signals if s.get("signal", "").upper() == signal.upper()]
    if sector:
        signals = [s for s in signals if s.get("sector", "").upper() == sector.upper()]

    return {
        "count": len(signals),
        "updated_at": app_state.last_update,
        "data": signals[:limit],
    }


@router.get("/signals/{symbol}")
async def get_signal(symbol: str):
    sym = symbol.upper()
    sig = app_state.signals.get(sym)
    if not sig:
        raise HTTPException(status_code=404, detail=f"Symbol '{sym}' not found")
    return sig


@router.get("/status")
async def get_status():
    mkt = market_status()
    return {
        "status":         "ok",
        "stocks_cached":  len(app_state.stocks),
        "ws_clients":     app_state.ws_clients,
        "last_update":    app_state.last_update,
        "data_source":    app_state.data_source,
        "data_stale":     app_state.data_stale,
        "stale_reason":   app_state.stale_reason,
        "horizon":        app_state.horizon,
        "uptime_seconds": round(time.time() - app_state.started_at, 1),
        "market": {
            "state":      mkt.state.value,
            "is_open":    mkt.is_open,
            "reason":     mkt.reason,
            "pkt_time":   mkt.pkt_now.strftime("%H:%M:%S PKT"),
            "next_open":  mkt.next_open.strftime("%a %d %b %H:%M PKT"),
        },
    }


@router.post("/horizon/{mode}")
async def set_horizon(mode: str):
    if mode not in ("short", "long"):
        raise HTTPException(status_code=400, detail="horizon must be 'short' or 'long'")
    app_state.horizon = mode
    # Immediately recompute scores with new horizon
    from ..strategy.signal_engine import compute_action_score
    for sym, sig in app_state.signals.items():
        sig["action_score"] = compute_action_score(sig, mode)
        sig["horizon"] = mode
    return {"horizon": mode, "message": f"Switched to {mode}-term scoring"}


@router.post("/config/reload")
async def reload_config():
    app_state.engine.reload_config()
    return {"message": "Strategy config reloaded successfully"}
