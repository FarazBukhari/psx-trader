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

import asyncio
import logging
import time as _time
from typing import Optional

import httpx  # already a project dependency (used by PSXScraper)

from fastapi import APIRouter, BackgroundTasks, Query

from ..market_hours import market_status, MarketState
from ..state import app_state

logger = logging.getLogger("psx.system")
system_router = APIRouter(prefix="/api/system")

# PSX Data Portal EOD endpoint — returns full history for any listed symbol
_PSX_EOD_URL = "https://dps.psx.com.pk/timeseries/eod/{symbol}"
_PSX_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://dps.psx.com.pk/",
}

# All symbols available on dps.psx.com.pk (KSE-100 + common mid-caps)
_DEFAULT_SYMBOLS = [
    "ENGRO", "LUCK",  "HBL",  "PSO",   "OGDC",  "PPL",   "MCB",
    "UBL",   "MARI",  "HUBC", "UNITY", "FFBL",  "PTCL",  "SYS",
    "TRG",   "EFERT", "FFC",  "MEBL",  "BAHL",  "NBP",   "POL",
    "SNGPL", "SSGC",  "KAPCO","NCPL",  "DGKC",  "MLCF",  "FCCL",
    "ACPL",  "CHCC",  "KOHC", "PIOC",  "FABL",  "BAFL",  "SILK",
    "ATRL",  "APL",   "SHEL", "PSX",   "PAKT",  "GATM",  "COLG",
]

# Track in-progress fetch to avoid double-triggering
_fetch_running = False


# ---------------------------------------------------------------------------
# Historical data fetch (background task)
# ---------------------------------------------------------------------------

async def _fetch_eod_psx(client: httpx.AsyncClient, symbol: str) -> list[dict]:
    """
    Fetch complete EOD history for one symbol from dps.psx.com.pk.
    Returns rows sorted oldest-first, ready for DB insert.

    Response data format (newest-first): [[ts, close, volume, open], ...]
    high/low are not provided by this endpoint — left as NULL.
    """
    url = _PSX_EOD_URL.format(symbol=symbol)
    try:
        resp = await client.get(url, timeout=20.0)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        logger.warning("Historical: %s — fetch failed: %s", symbol, exc)
        return []

    if payload.get("status") != 1 or not payload.get("data"):
        logger.warning("Historical: %s — empty or error response", symbol)
        return []

    rows = []
    for entry in reversed(payload["data"]):   # oldest-first
        if len(entry) < 4:
            continue
        ts, close, volume, open_price = entry[0], entry[1], entry[2], entry[3]
        rows.append({
            "symbol":     symbol,
            "sector":     None,
            "ldcp":       None,    # back-filled below
            "open_price": float(open_price),
            "high":       None,    # not in this endpoint
            "low":        None,    # not in this endpoint
            "close":      float(close),
            "volume":     int(volume),
            "change_pct": None,    # back-filled below
            "source":     "historical",
            "scraped_at": int(ts),
        })

    # Back-fill ldcp and change_pct now that rows are oldest-first
    prev_close = None
    for row in rows:
        if prev_close is not None:
            row["ldcp"]       = round(prev_close, 4)
            row["change_pct"] = round((row["close"] - prev_close) / prev_close * 100, 4)
        prev_close = row["close"]

    return rows


async def _run_historical_fetch(symbols: list[str]) -> None:
    global _fetch_running
    _fetch_running = True
    try:
        from ..db.models import PriceHistory
        from ..db.database import get_session
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        total_inserted = 0
        async with httpx.AsyncClient(headers=_PSX_HEADERS, follow_redirects=True) as client:
            for symbol in symbols:
                rows = await _fetch_eod_psx(client, symbol)
                if not rows:
                    continue

                # Bulk insert in batches of 500
                inserted = 0
                for i in range(0, len(rows), 500):
                    batch = rows[i:i + 500]
                    async with get_session() as session:
                        stmt = (
                            sqlite_insert(PriceHistory)
                            .values(batch)
                            .on_conflict_do_nothing()
                        )
                        result = await session.execute(stmt)
                        inserted += result.rowcount or 0

                total_inserted += inserted
                logger.info(
                    "Historical: %s — %d trading days, %d newly inserted",
                    symbol, len(rows), inserted,
                )
                await asyncio.sleep(0.25)

        logger.info(
            "Historical fetch complete — %d symbols, %d rows newly inserted",
            len(symbols), total_inserted,
        )
    except Exception as exc:
        logger.error("Historical fetch failed: %s", exc)
    finally:
        _fetch_running = False


@system_router.post("/fetch-historical")
async def trigger_historical_fetch(
    background_tasks: BackgroundTasks,
    symbols: Optional[str] = Query(None, description="Comma-separated PSX symbols; omit for all known"),
) -> dict:
    """
    Trigger a background job that downloads full EOD history for PSX symbols
    from dps.psx.com.pk/timeseries/eod/{SYMBOL} and inserts it into price_history.

    Data format from PSX: [unix_timestamp, close, volume, open] per trading day.
    high/low are not provided by this endpoint and will be NULL in the DB.
    Rows are inserted with source='historical'; duplicates are silently skipped.

    No external dependencies required — uses httpx (already installed).
    """
    global _fetch_running
    if _fetch_running:
        return {"status": "already_running", "message": "Historical fetch already in progress."}

    sym_list = [s.strip().upper() for s in symbols.split(",")] if symbols else _DEFAULT_SYMBOLS

    background_tasks.add_task(_run_historical_fetch, sym_list)

    return {
        "status":  "started",
        "symbols": len(sym_list),
        "message": (
            f"Historical fetch started for {len(sym_list)} symbol(s) from dps.psx.com.pk. "
            "Full history per symbol is returned in one call — no date range needed. "
            "Check server logs for progress."
        ),
    }


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
