"""
Prediction API routes.

Endpoints
─────────
  GET  /api/predictions            → current predictions for all symbols
  GET  /api/predictions/{symbol}   → prediction for a specific symbol

Predictions are embedded in the live signal state (app_state.signals).
These endpoints extract and shape that data for consumption.

Query parameters for GET /api/predictions
  direction : filter by "up" | "down" | "neutral"  (default: non-neutral only)
  signal    : filter by "BUY" | "SELL" | "HOLD" | "FORCE_SELL"
  min_conf  : minimum confidence threshold (float, default 0.0)
  limit     : max results (default 50, max 500)
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..state import app_state

prediction_router = APIRouter(prefix="/api/predictions")


@prediction_router.get("")
async def get_predictions(
    direction: Optional[str] = Query(None, description="up | down | neutral"),
    signal: Optional[str]    = Query(None, description="BUY | SELL | HOLD | FORCE_SELL"),
    min_conf: float          = Query(0.0,  ge=0.0, le=1.0, description="Minimum confidence"),
    limit: int               = Query(50,   ge=1,   le=500),
):
    """
    Return enriched predictions for all symbols.

    By default only non-neutral predictions are returned.
    Use ?direction=neutral to include unpredictable symbols.
    """
    results = []

    for s in app_state.signals.values():
        pred = s.get("prediction")
        if not pred:
            continue

        pred_dir = pred.get("direction", "neutral")
        pred_conf = pred.get("confidence", 0.0)
        sig_type  = s.get("signal", "HOLD")

        # Direction filter
        if direction:
            if pred_dir != direction.lower():
                continue
        else:
            # Default: skip neutral
            if pred_dir == "neutral":
                continue

        # Signal filter
        if signal and sig_type.upper() != signal.upper():
            continue

        # Confidence threshold
        if pred_conf < min_conf:
            continue

        results.append({
            "symbol":          s.get("symbol"),
            "signal":          sig_type,
            "current":         s.get("current"),
            "change_pct":      s.get("change_pct"),
            "rsi":             s.get("rsi"),
            "action_score":    s.get("action_score"),
            "signal_sources":  s.get("signal_sources", []),
            "prediction":      pred,
        })

    # Sort: highest confidence first
    results.sort(key=lambda x: x["prediction"].get("confidence", 0.0), reverse=True)

    return {
        "count":      len(results[:limit]),
        "updated_at": app_state.last_update,
        "data":       results[:limit],
    }


@prediction_router.get("/{symbol}")
async def get_prediction(symbol: str):
    """
    Return the current prediction for a specific symbol.
    Includes full signal context alongside the prediction block.
    """
    sym = symbol.upper()
    sig = app_state.signals.get(sym)

    if not sig:
        raise HTTPException(status_code=404, detail=f"Symbol '{sym}' not found")

    pred = sig.get("prediction")
    if not pred:
        raise HTTPException(
            status_code=404,
            detail=f"No prediction available for '{sym}' yet — "
                   f"price history is still accumulating.",
        )

    return {
        "symbol":         sym,
        "signal":         sig.get("signal"),
        "current":        sig.get("current"),
        "change_pct":     sig.get("change_pct"),
        "volume":         sig.get("volume"),
        "rsi":            sig.get("rsi"),
        "sma5":           sig.get("sma5"),
        "sma20":          sig.get("sma20"),
        "action_score":   sig.get("action_score"),
        "signal_sources": sig.get("signal_sources", []),
        "horizon":        sig.get("horizon"),
        "prediction":     pred,
    }
