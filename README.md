# PSX Smart Trading Signal System

A real-time trading signal, portfolio management, and forward-testing system for the Pakistan Stock Exchange (PSX). Scrapes live prices, runs a multi-indicator signal engine, tracks a paper portfolio with full P&L accounting, and evaluates every signal with a live forward-testing engine — all from a local server you run yourself.

---

## What it does

- Scrapes live PSX prices every 15 seconds during market hours
- Generates BUY / SELL / HOLD / FORCE_SELL signals using RSI, SMA crossover, volume spikes, and momentum scoring
- Broadcasts signal updates to a React dashboard over WebSocket
- Persists price history and signal logs to SQLite for indicator warm-up on restart
- Tracks a paper portfolio: execute buys and sells, computes weighted average cost basis, realized and unrealized P&L, and brokerage fees
- Enforces PSX market hours (09:30–15:30 PKT, Mon–Fri) — trade endpoints are locked outside these hours
- Falls back to a cached snapshot when the market is closed or a scrape fails, marking data as stale
- **Forward-tests every signal in real time** — opens a virtual trade on each BUY/SELL/FORCE_SELL, tracks price extremes, and closes on TP/SL or end-of-day
- **Backtests any strategy** against historical price data with full metrics (win rate, Sharpe, max drawdown, profit factor)
- **Equity curve persists across page refreshes** — portfolio snapshots are loaded from the DB on startup
- **ML-style prediction engine** — regression-based confidence scoring, expected move %, risk classification, and trade action recommendations
- Exposes a full REST API and live WebSocket feed

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, FastAPI, uvicorn |
| Database | SQLite via SQLAlchemy 2.0 async + aiosqlite |
| Migrations | Alembic |
| Scraping | httpx + BeautifulSoup4 |
| Signal engine | pandas, numpy, scipy |
| Frontend | React + Vite + Tailwind CSS + Recharts |

---

## Project structure

```
psx-trader/
├── config/
│   └── strategy.json              # per-symbol buy/sell/stop levels + global config
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── routes.py                  # signals, horizon
│   │   │   ├── history_routes.py          # price + signal history
│   │   │   ├── system_routes.py           # system status + historical data fetch trigger
│   │   │   ├── portfolio_routes.py        # portfolio + trade endpoints + reset + snapshots
│   │   │   ├── backtest_routes.py         # strategy backtesting
│   │   │   ├── analytics_routes.py        # signal analytics
│   │   │   ├── performance_routes.py      # forward-test live trades + history + summary
│   │   │   └── deps.py                    # market-open guard, rate limiter
│   │   ├── analytics/
│   │   │   ├── forward_tracker.py         # forward-test engine (open/update/close trades, batched)
│   │   │   └── signal_evaluator.py        # historical signal outcome evaluation (O(N) queries)
│   │   ├── prediction/
│   │   │   └── prediction_engine.py       # regression-based confidence + trade action engine
│   │   ├── db/
│   │   │   ├── database.py                # async engine, NullPool for SQLite, session factory
│   │   │   ├── models.py                  # 8 ORM tables
│   │   │   └── history_store.py           # batched tick/signal persistence, 2-query warm-up
│   │   ├── portfolio/
│   │   │   ├── portfolio_manager.py       # buy/sell execution, P&L, snapshots, reset
│   │   │   ├── fees.py                    # PSX brokerage fee calculator
│   │   │   └── schemas.py                 # Pydantic request/response models
│   │   ├── scraper/
│   │   │   └── psx_scraper.py             # live scrape + snapshot fallback
│   │   ├── strategy/
│   │   │   ├── signal_engine.py           # RSI, SMA, volume, momentum signals
│   │   │   └── backtester.py              # vectorised strategy backtester
│   │   ├── market_hours.py                # PKT market state + countdown
│   │   ├── state.py                       # shared in-memory app state
│   │   └── main.py                        # FastAPI app, lifespan, WebSocket, poll loop
│   ├── migrations/                        # Alembic migration versions
│   ├── scripts/
│   │   └── fetch_historical.py            # CLI: bulk-fetch EOD history from PSX DPS
│   ├── tests/
│   │   ├── conftest.py                    # per-test isolated DB fixture
│   │   └── test_portfolio_e2e.py          # 16 end-to-end portfolio tests
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── App.jsx
│       ├── pages/
│       │   ├── Dashboard.jsx
│       │   ├── Portfolio.jsx
│       │   ├── Backtest.jsx
│       │   └── Performance.jsx            # forward-test dashboard
│       ├── components/
│       │   ├── common/                    # Tooltip (portal-based, always above sticky header)
│       │   ├── dashboard/                 # SignalTable — 3 collapsible sections (BUY/SELL/FORCE_SELL),
│       │   │                              # action filter, smart sort composite score
│       │   ├── layout/                    # Header, Tabs, StatusBar
│       │   ├── portfolio/                 # PortfolioBar, PositionsTable, TradePanel, Chart
│       │   └── performance/               # PerformancePanel, LiveTradesTable,
│       │                                  # TradeHistoryTable, PerformanceChart
│       ├── store/
│       │   ├── usePortfolioStore.js       # portfolio state + DB-backed equity curve
│       │   └── usePerformanceStore.js     # forward-test state
│       ├── api/
│       │   ├── portfolio.js               # portfolio + snapshots API calls
│       │   └── performance.js             # forward-test API calls
│       └── hooks/                         # useWebSocket, useNotifications
└── start.sh                               # one-command launcher
```

---

## Dashboard

The React dashboard is organised into three collapsible signal tables — one per signal type — so actionable opportunities are never buried in a long list.

### Signal tables

| Table | Contents |
|---|---|
| 🟢 BUY signals | Stocks with an active BUY signal from the signal engine |
| 🔴 SELL signals | Stocks with an active SELL signal |
| ⚡ FORCE_SELL signals | Stocks with an active FORCE_SELL (stop-loss breach) signal |

Each table shows the 10 highest-scoring rows by default. A **Show all N stocks** button expands to the full list. The table collapses back to its header row with a single click.

### Sorting

**SMART SORT** (default) ranks every row by a composite score that combines prediction quality and signal strength:

```
smartScore = (confidence × (1 + expectedMove/10) × actionBoost × riskPenalty) × 0.70
           + (action_score / 11_500) × 0.30

actionBoost  : 1.5 for BUY or SELL action, 1.0 for AVOID
riskPenalty  : 1.00 low / 0.85 medium / 0.70 high
```

You can also sort by any column header (symbol, RSI, SMA gap, volume, confidence, etc.).

### Action filter

A pill-style filter above the tables lets you narrow all three tables at once to stocks with a specific predicted trade action: **ALL · BUY · SELL · AVOID**.

### Prediction column (ACTION)

Each row shows a prediction badge derived from the ML prediction engine:

| Badge | Meaning |
|---|---|
| `BUY` (green) | Model recommends entering a long position |
| `SELL` (red) | Model recommends exiting or shorting |
| `AVOID` (yellow) | Signal not strong enough to act on |
| `—` (grey) | Prediction engine warming up (insufficient price history) |

The badge also shows confidence %, expected move %, and risk level on hover.

### Tooltips

All `ⓘ` help icons in the table header use a portal-based renderer — they escape the `overflow-x-auto` / sticky-header stacking context and always appear on top of the table.

---

## Quick start

**Prerequisites:** Python 3.10+, Node.js 18+

```bash
# Clone and launch (installs all deps automatically)
git clone <repo-url>
cd psx-trader
./start.sh

# Or use mock prices (no internet required)
./start.sh --mock
```

Once running:

| URL | Description |
|---|---|
| http://localhost:5173 | React dashboard |
| http://localhost:8000/docs | Interactive API docs (Swagger) |
| http://localhost:8000/health | Health check |
| ws://localhost:8000/ws | WebSocket feed |

---

## Configuration

Edit `config/strategy.json` to set per-symbol levels and global parameters. The backend watches this file and reloads automatically — no restart needed.

```json
{
  "symbols": {
    "ENGRO": { "buy_below": 280.0, "sell_above": 310.0, "stop_loss": 270.0 }
  },
  "global": {
    "poll_interval_seconds": 15,
    "volume_spike_threshold": 2.0,
    "commission_rate": 0.0015,
    "cdc_charge": 10.0,
    "secp_rate": 0.000115
  }
}
```

Environment variables (optional, set before `start.sh`):

| Variable | Default | Description |
|---|---|---|
| `PSX_POLL_INTERVAL` | `15` | Scrape interval in seconds |
| `PSX_MOCK` | `false` | Use simulated prices |
| `PSX_SNAPSHOT_INTERVAL` | `300` | Portfolio snapshot frequency in seconds |
| `PSX_EVALUATOR_INTERVAL` | `600` | Signal evaluation frequency in seconds |
| `DATABASE_URL` | `sqlite+aiosqlite:///backend/psx_trader.db` | Override DB path |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

---

## API reference

### Signals & system

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/signals` | Current signals for all tracked symbols |
| GET | `/api/system/status` | Market state, data freshness, trading execution flag |
| POST | `/api/system/fetch-historical` | Trigger background EOD history download from PSX DPS |
| GET | `/api/history/{symbol}` | Price tick history (up to 200 rows) |
| GET | `/api/history/{symbol}/signals` | Signal log for a symbol |
| GET | `/api/history/stats` | DB row counts |

### Portfolio

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/portfolio` | Full summary with live unrealized P&L |
| POST | `/api/portfolio/cash` | Set cash balance |
| GET | `/api/portfolio/positions` | Open positions |
| POST | `/api/portfolio/positions` | Manually record a pre-existing holding |
| DELETE | `/api/portfolio/positions/{symbol}` | Remove a single position (no trade record) |
| DELETE | `/api/portfolio/reset` | Clear **all** open positions (no trade records) |
| GET | `/api/portfolio/snapshots` | Historical portfolio value snapshots (equity curve) |
| GET | `/api/portfolio/buying-power/{symbol}` | Max shares purchasable with available cash |

### Trades

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/trades/buy` | Execute a BUY order |
| POST | `/api/trades/sell` | Execute a SELL order |
| GET | `/api/trades` | Trade history (paginated) |
| GET | `/api/trades/{symbol}` | Trade history for one symbol |

Trade endpoints require the market to be OPEN with live data. They return HTTP 423 when the market is closed or data is stale, and HTTP 429 when the rate limit (5 trades / 60 seconds) is exceeded.

### Forward-test performance

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/performance/live` | All currently OPEN forward-test trades |
| GET | `/api/performance/history` | Closed forward-test trades (paginated, filterable by symbol) |
| GET | `/api/performance/summary` | Aggregate metrics: win rate, avg return, Sharpe, profit factor |

### Backtesting

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/backtest/run` | Run a backtest over stored price history |
| GET | `/api/backtest/results` | Retrieve past backtest results |

---

## Prediction engine

Every signal is independently evaluated by a regression-based prediction engine that runs alongside the RSI/SMA signal engine. For each stock it fits a linear trend to recent closing prices and combines that with momentum and RSI indicators to produce:

| Output | Description |
|---|---|
| `confidence` | 0–85% — how strongly the model believes in the direction (capped at 85%) |
| `expected_move_pct` | Estimated % price move over the horizon |
| `trade_action` | `buy` / `sell` / `avoid` based on confidence + total strength + agreement thresholds |
| `risk` | `low` / `medium` / `high` — volatility-based risk classification |
| `basis` | List of factors that contributed to the prediction |

Thresholds for a non-AVOID recommendation are deliberately loose so that genuinely interesting setups (r² ≈ 0.4, mild RSI divergence) are surfaced rather than buried:

| Threshold | Value |
|---|---|
| Minimum confidence to act | 12% |
| Minimum total strength | 10% |
| Minimum indicator agreement | 15% |

---

## Forward testing engine

Every live BUY / SELL / FORCE_SELL signal automatically opens a virtual forward trade tracked in the `forward_trades` table. No manual setup required.

**How it works:**
- One open trade per symbol at a time (deduplication enforced at DB level)
- Take-profit and stop-loss thresholds are volatility-based (range ÷ mean of recent prices), clamped to TP 1.5–5%, SL 1–3%
- An opposite signal (e.g. SELL arriving while a BUY trade is open) closes the trade immediately
- Any trade open at market close is force-closed against the last known price
- MFE (max favourable excursion) and MAE (max adverse excursion) are tracked per trade

**Outcome classification (4 buckets):**

| Label | Condition |
|---|---|
| `STRONG_WIN` | P&L ≥ take-profit threshold |
| `WEAK_WIN` | P&L > 0.2% |
| `BREAKEVEN` | \|P&L\| ≤ 0.2% |
| `LOSS` | P&L < −0.2% |

**Performance tab** (🎯 in the UI) shows: live open trades, closed trade history with colour-coded rows, and a summary panel with win rate, avg return/trade, avg return/hour, profit factor, and a P&L distribution chart. Auto-refreshes every 10 seconds.

---

## Performance & resource efficiency

Several targeted optimisations reduce CPU, memory, and disk overhead without changing observable behaviour:

**Database (SQLite)**
- `NullPool` is used instead of the default connection pool — SQLite is file-based and gains nothing from pooling; removing the pool eliminates the `QueuePool limit reached` error that appeared under burst load.
- History store writes are batched: ticks and signals accumulate in memory and are flushed in a single `INSERT` when the buffer reaches 20 rows *or* 60 seconds have elapsed (whichever comes first), instead of one write per tick.
- `warm_price_buffer` (run on every startup) issues 2 queries instead of N+1: one `SELECT DISTINCT symbol` followed by one range `SELECT` across all symbols; grouping and capping to 200 rows per symbol is done in Python.
- Signal evaluator's `_fetch_prices_batch` issues O(N) queries (one range `SELECT` per symbol) instead of O(N×T) individual `LIMIT 1` lookups. Timestamp matching within the result set uses `bisect` for O(log n) lookups.

**Forward trade creation**
- On each poll tick, all actionable signals are processed in a single DB session (`create_trades_batch`) instead of spawning one `asyncio.create_task` per signal. This eliminates the burst of concurrent write tasks that exhausted the connection pool.

**Snapshot I/O**
- The live price snapshot is written to disk at most once every 60 seconds (debounced), not on every 15-second poll tick — a 4× reduction in snapshot write frequency.

**WebSocket broadcast**
- The payload is serialised to JSON once and the same string is sent to every connected client (was N serialisations for N clients). Disconnected clients are collected during the broadcast pass and removed after, avoiding set mutation during iteration.

---

## Historical data

To populate `price_history` with EOD data for backtesting, run the fetch script. It pulls directly from the PSX Data Portal (`dps.psx.com.pk`) — no external dependencies needed beyond the packages already installed.

```bash
cd backend

# Fetch full history for all known symbols (~40 symbols, full history in one call each)
python -m scripts.fetch_historical

# Fetch specific symbols only
python -m scripts.fetch_historical --symbols ENGRO LUCK HBL
```

You can also trigger a fetch from the API without restarting the server:

```bash
# All symbols
curl -X POST "http://localhost:8000/api/system/fetch-historical"

# Specific symbols
curl -X POST "http://localhost:8000/api/system/fetch-historical?symbols=ENGRO,LUCK,HBL"
```

The fetch runs as a background task — check server logs for per-symbol progress. Duplicate rows (same symbol + timestamp) are silently skipped, so re-running is safe.

**Data format from PSX DPS:** `[unix_timestamp, close, volume, open]` per trading day. `high` and `low` are not provided by this endpoint and are stored as NULL. Rows are inserted with `source='historical'`.

---

## Equity curve

The portfolio equity chart (on the Portfolio page) is backed by the `portfolio_snapshots` table. Snapshots are taken every 5 minutes during market hours. On page load the chart seeds from the full snapshot history so it survives browser refreshes — in-session data points are appended on top.

---

## Brokerage fees

Fees are calculated per PSX discount broker structure and applied to every trade:

| Component | Rate |
|---|---|
| Commission (TREC holder) | 0.15% of trade value |
| CDC charge | PKR 10.00 flat |
| SECP levy | 0.0115% of trade value |
| **Total per side** | **~0.162% + PKR 10** |

Realized P&L is pre-CGT. Capital Gains Tax depends on holding period and annual income bracket and is calculated at year-end by the broker.

---

## Running tests

```bash
cd backend
pip install pytest pytest-asyncio --break-system-packages
pytest tests/test_portfolio_e2e.py -v
```

Tests use an isolated in-memory SQLite DB per test case — the production database is never touched.

---

## Database schema

Eight tables managed by Alembic:

| Table | Description |
|---|---|
| `price_history` | Scraped OHLCV ticks per symbol (live + historical) |
| `signals_log` | Non-HOLD and changed signals with full indicator values |
| `portfolio` | Cash balance and metadata |
| `positions` | Open holdings with weighted average cost basis |
| `trades` | Immutable ledger of every executed buy and sell |
| `portfolio_snapshots` | Periodic point-in-time portfolio value for the equity curve chart |
| `forward_trades` | Virtual trades opened by the forward-testing engine |
| `signal_outcomes` | Validated signal outcomes across short/medium/long horizons |

Run migrations manually if needed:

```bash
cd backend
alembic upgrade head
```

---

## Market hours

The system uses Pakistan Standard Time (PKT = UTC+5, no DST).

| State | Hours (PKT) | Days |
|---|---|---|
| Pre-Market | 00:00 – 09:29 | Mon–Fri |
| Open | 09:30 – 15:30 | Mon–Fri |
| Post-Market | 15:31 – 23:59 | Mon–Fri |
| Closed | All day | Sat–Sun |

When the market is closed, the last successful scrape is served as a stale snapshot. All signals and API responses include a `stale: true` flag and a banner reason when this is the case.
