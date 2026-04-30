"""
PSX Smart Trading Signal System — FastAPI Backend
Run:  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .api.history_routes import history_router
from .api.system_routes import system_router
from .api.portfolio_routes import portfolio_router
from .api.prediction_routes import prediction_router
from .api.backtest_routes import backtest_router
from .api.analytics_routes import analytics_router
from .analytics.signal_evaluator import evaluate_pending_signals
from .db import init_db
from .logger import setup_logging
from .scraper.psx_scraper import PSXScraper
from .state import app_state
from .strategy.signal_engine import price_buffer
from .websocket.manager import manager

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
setup_logging(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("psx.main")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
POLL_INTERVAL      = int(os.getenv("PSX_POLL_INTERVAL", "15"))
USE_MOCK           = os.getenv("PSX_MOCK", "false").lower() == "true"
STRATEGY_PATH      = Path(__file__).parent.parent.parent.parent / "config" / "strategy.json"
SNAPSHOT_INTERVAL  = int(os.getenv("PSX_SNAPSHOT_INTERVAL", "300"))   # portfolio snapshot every 5 min
EVALUATOR_INTERVAL = int(os.getenv("PSX_EVALUATOR_INTERVAL", "600"))  # signal evaluation every 10 min

# ---------------------------------------------------------------------------
# Background: strategy.json file watcher
# Polls mtime every 5s — reloads config automatically when file changes.
# No external dependencies needed.
# ---------------------------------------------------------------------------

async def _watch_strategy(check_interval: int = 5):
    last_mtime = STRATEGY_PATH.stat().st_mtime if STRATEGY_PATH.exists() else 0
    logger.info("Watching strategy.json for changes (every %ds)", check_interval)
    while True:
        await asyncio.sleep(check_interval)
        try:
            if not STRATEGY_PATH.exists():
                continue
            mtime = STRATEGY_PATH.stat().st_mtime
            if mtime != last_mtime:
                last_mtime = mtime
                app_state.engine.reload_config()
                app_state.config_loaded_at = time.time()
                logger.info("strategy.json changed — config reloaded automatically ✅")
                # Notify all WS clients
                await manager.broadcast({
                    "type":    "config_reloaded",
                    "message": "strategy.json reloaded",
                    "ts":      time.time(),
                })
        except Exception as exc:
            logger.warning("File watcher error: %s", exc)


# ---------------------------------------------------------------------------
# Background poll loop
# ---------------------------------------------------------------------------

async def _portfolio_snapshot_loop():
    """Periodically persist a portfolio value snapshot for P&L charting."""
    logger.info("Portfolio snapshot loop started — interval: %ds", SNAPSHOT_INTERVAL)
    while True:
        await asyncio.sleep(SNAPSHOT_INTERVAL)
        try:
            prices = {
                sym: data["close"]
                for sym, data in app_state.stocks.items()
                if data.get("close") is not None
            }
            await app_state.portfolio.take_snapshot(prices)
        except Exception as exc:
            logger.warning("Portfolio snapshot failed (non-fatal): %s", exc)


async def _signal_evaluation_loop():
    """Periodically evaluate historical signals against forward price data."""
    logger.info("Signal evaluation loop started — interval: %ds", EVALUATOR_INTERVAL)
    # Stagger first run by 60s so the app finishes warm-up before hitting the DB
    await asyncio.sleep(60)
    while True:
        try:
            inserted = await evaluate_pending_signals()
            if inserted:
                logger.info("Signal evaluator: %d new outcomes persisted", inserted)
        except Exception as exc:
            logger.warning("Signal evaluation failed (non-fatal): %s", exc)
        await asyncio.sleep(EVALUATOR_INTERVAL)


async def _poll_loop(scraper: PSXScraper):
    logger.info("Poll loop started — interval: %ds", POLL_INTERVAL)
    while True:
        try:
            stocks  = await scraper.fetch()
            signals = app_state.engine.process(stocks, horizon=app_state.horizon)

            # Phase 3: enrich signals with forward-looking prediction metadata
            signals = app_state.prediction_engine.enrich_batch(signals)

            # Derive stale status from the first returned row (all rows share the same source)
            first = stocks[0] if stocks else {}
            is_stale = bool(first.get("stale", False))
            stale_reason = (
                f"Data from snapshot ({first.get('snapshot_age', 0)}s old)"
                if is_stale else None
            )

            for s in stocks:
                app_state.stocks[s["symbol"]] = s
                # Only persist ticks from live scrapes — snapshot ticks are already in DB
                if not s.get("stale"):
                    asyncio.create_task(app_state.history_store.save_tick(s))

            for s in signals:
                # Stamp stale onto each signal so /api/signals and WS carry it per-row
                s["stale"] = is_stale
                app_state.signals[s["symbol"]] = s
                # Persist non-HOLD / changed signals regardless of stale status
                asyncio.create_task(app_state.history_store.save_signal(s))

            # Phase 3: log non-neutral predictions to DB (fire-and-forget)
            asyncio.create_task(app_state.prediction_engine.log_predictions(signals))

            app_state.last_update  = time.time()
            app_state.data_source  = first.get("source", "unknown")
            app_state.data_stale   = is_stale
            app_state.stale_reason = stale_reason
            app_state.ws_clients   = manager.client_count

            changed = [s for s in signals if s.get("signal_changed")]
            payload = {
                "type":         "update",
                "timestamp":    app_state.last_update,
                "source":       app_state.data_source,
                "stale":        app_state.data_stale,
                "stale_reason": app_state.stale_reason,
                "horizon":      app_state.horizon,
                "config_at":    app_state.config_loaded_at,
                "all":          signals,
                "changed":      changed,
                "client_count": manager.client_count,
            }
            await manager.broadcast(payload)

            if changed and not is_stale:
                syms = [f"{s['symbol']}→{s['signal']}" for s in changed]
                logger.info("Signal changes: %s", ", ".join(syms))

        except Exception as exc:
            logger.exception("Poll loop error: %s", exc)

        await asyncio.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Step 1: initialise database (create tables if they don't exist) ──
    await init_db()

    # ── Step 2: ensure default portfolio exists ───────────────────────
    try:
        await app_state.portfolio.ensure_default_portfolio()
        logger.info("Portfolio ready")
    except Exception as exc:
        logger.warning("Portfolio init failed (non-fatal): %s", exc)

    # ── Step 3: warm price buffer from DB so indicators start accurate ──
    try:
        await app_state.history_store.warm_price_buffer(price_buffer)
    except Exception as exc:
        logger.warning("Price buffer warm-up failed (non-fatal): %s", exc)

    scraper = PSXScraper()
    if USE_MOCK:
        scraper.enable_mock()
        logger.info("Running in MOCK mode (PSX_MOCK=true)")

    # ── Step 4: live warm-up fetch ────────────────────────────────────
    try:
        stocks  = await scraper.fetch()
        signals = app_state.engine.process(stocks, horizon=app_state.horizon)
        # Phase 3: enrich warm-up signals with predictions
        signals = app_state.prediction_engine.enrich_batch(signals)
        for s in stocks:
            app_state.stocks[s["symbol"]] = s
        for s in signals:
            app_state.signals[s["symbol"]] = s
        app_state.last_update      = time.time()
        app_state.config_loaded_at = time.time()
        app_state.data_source      = stocks[0].get("source", "unknown") if stocks else "unknown"
        logger.info("Warm-up complete — %d stocks loaded", len(stocks))
    except Exception as exc:
        logger.warning("Live warm-up failed: %s", exc)

    poll_task      = asyncio.create_task(_poll_loop(scraper))
    watch_task     = asyncio.create_task(_watch_strategy())
    snapshot_task  = asyncio.create_task(_portfolio_snapshot_loop())
    evaluator_task = asyncio.create_task(_signal_evaluation_loop())

    yield

    # ── Shutdown: flush any remaining buffered ticks ──────────────────
    try:
        await app_state.history_store.flush_ticks()
        await app_state.history_store.flush_signals()
        logger.info("DB buffers flushed on shutdown")
    except Exception as exc:
        logger.warning("DB flush on shutdown failed: %s", exc)

    poll_task.cancel()
    watch_task.cancel()
    snapshot_task.cancel()
    evaluator_task.cancel()
    await scraper.close()
    logger.info("Server shutdown — goodbye")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="PSX Signal System",
    description="Live Pakistan Stock Exchange trading signals API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(history_router)
app.include_router(system_router)
app.include_router(portfolio_router)
app.include_router(prediction_router)
app.include_router(backtest_router)
app.include_router(analytics_router)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    app_state.ws_clients = manager.client_count
    try:
        await ws.send_json({
            "type":         "snapshot",
            "timestamp":    app_state.last_update,
            "source":       app_state.data_source,
            "stale":        app_state.data_stale,
            "stale_reason": app_state.stale_reason,
            "horizon":      app_state.horizon,
            "config_at":    app_state.config_loaded_at,
            "all":          list(app_state.signals.values()),
        })
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(ws)
        app_state.ws_clients = manager.client_count


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "uptime": round(time.time() - app_state.started_at, 1)}
