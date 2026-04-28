# PSX Smart Trading Signal System

A real-time trading signal and portfolio management system for the Pakistan Stock Exchange (PSX). Scrapes live prices, runs a multi-indicator signal engine, and tracks a paper portfolio with full P&L accounting — all from a local server you run yourself.

---

## What it does

- Scrapes live PSX prices every 15 seconds during market hours
- Generates BUY / SELL / HOLD / FORCE_SELL signals using RSI, SMA crossover, volume spikes, and momentum scoring
- Broadcasts signal updates to a React dashboard over WebSocket
- Persists price history and signal logs to SQLite for indicator warm-up on restart
- Tracks a paper portfolio: execute buys and sells, computes weighted average cost basis, realized and unrealized P&L, and brokerage fees
- Enforces PSX market hours (09:30–15:30 PKT, Mon–Fri) — trade endpoints are locked outside these hours
- Falls back to a cached snapshot when the market is closed or a scrape fails, marking data as stale
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
| Frontend | React + Vite + Tailwind CSS |

---

## Project structure

```
psx-trader/
├── config/
│   └── strategy.json          # per-symbol buy/sell/stop levels + global config
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── routes.py              # signals, horizon
│   │   │   ├── history_routes.py      # price + signal history
│   │   │   ├── system_routes.py       # GET /api/system/status
│   │   │   ├── portfolio_routes.py    # portfolio + trade endpoints
│   │   │   └── deps.py                # market-open guard, rate limiter
│   │   ├── db/
│   │   │   ├── database.py            # async engine, session factory
│   │   │   ├── models.py              # 7 ORM tables
│   │   │   └── history_store.py       # buffered tick/signal persistence
│   │   ├── portfolio/
│   │   │   ├── portfolio_manager.py   # buy/sell execution, P&L, snapshots
│   │   │   ├── fees.py                # PSX brokerage fee calculator
│   │   │   └── schemas.py             # Pydantic request/response models
│   │   ├── scraper/
│   │   │   └── psx_scraper.py         # live scrape + snapshot fallback
│   │   ├── strategy/
│   │   │   └── signal_engine.py       # RSI, SMA, volume, momentum signals
│   │   ├── market_hours.py            # PKT market state + countdown
│   │   ├── state.py                   # shared in-memory app state
│   │   └── main.py                    # FastAPI app, lifespan, WebSocket, poll loop
│   ├── migrations/                    # Alembic migration versions
│   ├── tests/
│   │   ├── conftest.py                # per-test isolated DB fixture
│   │   └── test_portfolio_e2e.py      # 16 end-to-end portfolio tests
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── App.jsx
│       ├── components/                # SignalBadge, StockTable, StatusBar, SummaryCards
│       └── hooks/                     # useWebSocket, useNotifications
└── start.sh                           # one-command launcher
```

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
| `DATABASE_URL` | `sqlite+aiosqlite:///backend/psx_trader.db` | Override DB path |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

---

## API reference

### Signals & system

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/signals` | Current signals for all tracked symbols |
| GET | `/api/system/status` | Market state, data freshness, trading execution flag |
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
| DELETE | `/api/portfolio/positions/{symbol}` | Remove a position (no trade record) |
| GET | `/api/portfolio/buying-power/{symbol}` | Max shares purchasable with available cash |

### Trades

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/trades/buy` | Execute a BUY order |
| POST | `/api/trades/sell` | Execute a SELL order |
| GET | `/api/trades` | Trade history (paginated) |
| GET | `/api/trades/{symbol}` | Trade history for one symbol |

Trade endpoints require the market to be OPEN with live data. They return HTTP 423 when the market is closed or data is stale, and HTTP 429 when the rate limit (5 trades / 60 seconds) is exceeded.

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

Seven tables managed by Alembic:

- `price_history` — scraped OHLCV ticks per symbol
- `signals_log` — non-HOLD and changed signals with indicator values
- `portfolios` — cash balance and metadata
- `positions` — open holdings with weighted average cost basis
- `trades` — immutable ledger of every executed buy and sell
- `portfolio_snapshots` — periodic point-in-time portfolio value for charting
- `prediction_log` — reserved for Phase 3 ML predictions

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

---

## Roadmap

- **Phase 3** — ML prediction engine: linear regression confidence scoring, outcome tracking, accuracy metrics
- **Phase 4** — Strategy evolution: backtesting engine, parameter auto-tuning against historical data
- **Phase 5** — UI enhancements: portfolio P&L chart, trade history view, signal timeline
