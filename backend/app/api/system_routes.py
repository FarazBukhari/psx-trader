"""
System status routes.

GET /api/system/status
    Full structured snapshot of market state, data freshness, signal health,
    trade-execution eligibility, and system vitals.

    This is the primary endpoint the frontend polls to decide:
      - whether to show the STALE DATA banner
      - whether to enable/disable the Buy/Sell buttons
      - what market phase badge to display
      - how long until the market opens or closes

Response shape:
  {
    "market":  { state, is_open, phase_label, pkt_time, pkt_date,
                 reason, next_open, seconds_to_open, seconds_to_close,
                 trading_hours },
    "data":    { stale, stale_reason, last_live_scrape, snapshot_age_seconds,
                 stocks_cached, data_source },
    "signals": { stale, stale_note, total, buy, sell, hold, force_sell },
    "trading": { execution_enabled, disabled_reason },
    "system":  { uptime_seconds, ws_clients, horizon, db_ok }
  }
"""

from __future__ import annotations

import time as _time

from fastapi import APIRouter

from ..market_hours import market_status, MarketState
from ..state import app_state

system_router = APIRouter(prefix="/api/system")


@system_router.get("/status")
async def get_system_status():
    mkt   = market_status()
    now   = _time.time()
    sigs  = list(app_state.signals.values())

    # ── Market block ──────────────────────────────────────────────────────────
    market_block = {
        "state":            mkt.state.value,
        "is_open":          mkt.is_open,
        "phase_label":      mkt.phase_label,
        "pkt_time":         mkt.pkt_now.strftime("%H:%M:%S"),
        "pkt_date":         mkt.pkt_now.strftime("%A %d %b %Y"),
        "reason":           mkt.reason,
        "next_open":        mkt.next_open.strftime("%a %d %b %H:%M PKT"),
        "next_open_iso":    mkt.next_open.isoformat(),
        "seconds_to_open":  mkt.seconds_to_open,
        "seconds_to_close": mkt.seconds_to_close,
        "trading_hours":    "09:30–15:30 PKT, Monday–Friday",
    }

    # ── Data block ────────────────────────────────────────────────────────────
    last_update       = app_state.last_update
    snapshot_age_secs = int(now - last_update) if last_update else None

    data_block = {
        "stale":                app_state.data_stale,
        "stale_reason":         app_state.stale_reason,
        "last_update":          last_update,
        "last_update_human":    (
            _time.strftime("%Y-%m-%d %H:%M:%S PKT", _time.localtime(last_update))
            if last_update else None
        ),
        "snapshot_age_seconds": snapshot_age_secs,
        "stocks_cached":        len(app_state.stocks),
        "data_source":          app_state.data_source,
    }

    # ── Signals block ─────────────────────────────────────────────────────────
    sig_counts: dict[str, int] = {"BUY": 0, "SELL": 0, "HOLD": 0, "FORCE_SELL": 0}
    for s in sigs:
        sig_counts[s.get("signal", "HOLD")] = sig_counts.get(s.get("signal", "HOLD"), 0) + 1

    stale_note = (
        "Signals are based on snapshot prices and are informational only — "
        "not suitable for trade execution."
        if app_state.data_stale else None
    )

    signals_block = {
        "stale":       app_state.data_stale,
        "stale_note":  stale_note,
        "total":       len(sigs),
        "buy":         sig_counts.get("BUY", 0),
        "sell":        sig_counts.get("SELL", 0),
        "hold":        sig_counts.get("HOLD", 0),
        "force_sell":  sig_counts.get("FORCE_SELL", 0),
    }

    # ── Trading block ─────────────────────────────────────────────────────────
    execution_enabled = mkt.is_open and not app_state.data_stale

    if not execution_enabled:
        if app_state.data_stale:
            disabled_reason = (
                "Prices are from a snapshot — trading on stale data is disabled. "
                f"Reason: {app_state.stale_reason or 'unknown'}."
            )
        else:
            disabled_reason = (
                f"Market is {mkt.phase_label.lower()} — "
                f"trade execution only available 09:30–15:30 PKT, Mon–Fri. "
                f"Next open in {_fmt_countdown(mkt.seconds_to_open)}."
            )
    else:
        disabled_reason = None

    trading_block = {
        "execution_enabled": execution_enabled,
        "disabled_reason":   disabled_reason,
        # Convenience fields for the frontend trade button
        "market_state":      mkt.state.value,
        "seconds_to_open":   mkt.seconds_to_open,
        "seconds_to_close":  mkt.seconds_to_close,
    }

    # ── System block ──────────────────────────────────────────────────────────
    # Lightweight DB connectivity check — just confirms the engine is importable
    db_ok = True
    try:
        from ..db.database import async_engine  # noqa: F401
    except Exception:
        db_ok = False

    system_block = {
        "uptime_seconds": round(now - app_state.started_at, 1),
        "ws_clients":     app_state.ws_clients,
        "horizon":        app_state.horizon,
        "config_loaded_at": app_state.config_loaded_at,
        "db_ok":          db_ok,
    }

    return {
        "market":  market_block,
        "data":    data_block,
        "signals": signals_block,
        "trading": trading_block,
        "system":  system_block,
    }


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _fmt_countdown(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    h, rem = divmod(seconds, 3600)
    m, _   = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"
