# PSX Trader — System Documentation

---

## 1. Overview

### What the System Does

PSX Trader is a live trading signal system for the Pakistan Stock Exchange (PSX/KSE). It scrapes intraday price data from `www.psx.com.pk/market-summary`, applies a rule-based + indicator strategy engine to generate BUY / SELL / HOLD / FORCE_SELL signals per symbol, and delivers those signals to a browser frontend over WebSocket. It also simulates a paper portfolio (PortfolioManager), runs historical strategy backtests (Backtester), evaluates historical signal accuracy (SignalValidationEngine / Phase 6), and tracks live signal performance via forward testing (ForwardTracker / Phase 7).

**There is no live brokerage integration. All trading is simulated.**

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  BACKEND (FastAPI, async Python)                                            │
│                                                                             │
│  ┌──────────────┐    ┌────────────────┐    ┌───────────────────────────┐   │
│  │  PSXScraper  │───▶│  SignalEngine  │───▶│  PredictionEngine         │   │
│  │  (httpx/BS4) │    │  (5 strategies)│    │  (momentum/RSI/BB/S-R)   │   │
│  └──────────────┘    └────────────────┘    └───────────────────────────┘   │
│         │                    │                          │                   │
│         ▼                    ▼                          ▼                   │
│  ┌──────────────┐    ┌────────────────┐    ┌───────────────────────────┐   │
│  │  HistoryStore│    │   app_state    │    │  WebSocket /ws            │   │
│  │  (SQLite)    │    │ (in-memory)    │    │  (broadcast to clients)   │   │
│  └──────────────┘    └────────────────┘    └───────────────────────────┘   │
│                              │                                              │
│       ┌──────────────────────┼──────────────────────┐                      │
│       ▼                      ▼                      ▼                      │
│  ┌──────────┐    ┌─────────────────────┐  ┌─────────────────────────────┐  │
│  │Backtester│    │ PortfolioManager    │  │ ForwardTracker + Evaluator  │  │
│  │(read-only│    │ (simulated trades)  │  │ (signal outcome analysis)   │  │
│  │ DB)      │    └─────────────────────┘  └─────────────────────────────┘  │
│  └──────────┘                                                               │
└─────────────────────────────────────────────────────────────────────────────┘
                          │  REST + WebSocket
┌─────────────────────────────────────────────────────────────────────────────┐
│  FRONTEND (React + Vite + Zustand)                                          │
│  Dashboard · Portfolio · Backtest · Performance · Charts · Database         │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Scrape** — `PSXScraper` fetches market summary HTML from PSX every 15 seconds (configurable). If the market is closed or the request fails, it returns data from `last_snapshot.json` marked as stale.
2. **Signal** — `SignalEngine.process()` receives the stock list, pushes closing prices into `PriceBuffer`, evaluates 5 strategy classes, resolves to a single signal per symbol, computes `action_score`.
3. **Enrich** — `PredictionEngine.enrich_batch()` adds a `prediction` block (direction, confidence, hold_days, risk, etc.) to each signal.
4. **Persist** — `HistoryStore` buffers ticks and signals, bulk-inserts into SQLite in batches of 20 or every 60 seconds.
5. **Forward-test** — `create_trades_batch()` opens `ForwardTrade` rows for actionable signals; `update_open_trades()` updates extremes and closes positions on TP/SL/time-exit.
6. **Broadcast** — `WebSocketManager` pushes the enriched signal payload to all connected clients.
7. **Evaluate** — Every 10 minutes the signal evaluator compares historical signals to actual forward prices and upserts accuracy rows into `signal_outcomes`.

---

## 2. Backend Architecture

### 2.1 Core Components

#### PSXScraper (`scraper/psx_scraper.py`)

Fetches and parses the PSX market summary page (`www.psx.com.pk/market-summary`) using `httpx` (async) and `BeautifulSoup`. Returns a list of stock dicts with keys: `symbol`, `sector`, `ldcp`, `open`, `high`, `low`, `current`, `volume`, `change_pct`, `source`, `timestamp`.

**Snapshot persistence guarantee:** Every successful live scrape is saved to `last_snapshot.json`. If the market is closed, the snapshot is returned immediately without making an HTTP request (marked `stale=True`). If a live scrape fails while the market is open, the snapshot is returned as stale. Mock data is the fallback of absolute last resort when no snapshot exists (activated by `PSX_MOCK=true` env var).

**Result is never an empty list.**

A **mock mode** (`scraper.enable_mock()`) generates synthetic tick-by-tick price movement for 15 hardcoded symbols (ENGRO, LUCK, HBL, PSO, OGDC, PPL, MCB, UBL, MARI, HUBC, UNITY, FFBL, PTCL, SYS, TRG).

**Historical data backfill** is available via `POST /api/system/fetch-historical`. This triggers a background job that downloads full EOD history from `dps.psx.com.pk/timeseries/eod/{SYMBOL}` for up to 41 symbols using `ON CONFLICT DO NOTHING` inserts. `high` and `low` are not provided by that endpoint and are stored as NULL for historical rows.

#### SignalEngine (`strategy/signal_engine.py`)

Applies 5 registered strategy classes to each stock tick. All strategies are instantiated as singletons in `STRATEGY_REGISTRY`.

| Strategy Class | Name | Logic |
|---|---|---|
| `PriceThresholdStrategy` | `price_threshold` | Reads symbol-level `buy_below`, `sell_above`, `stop_loss` from `strategy.json`. Abstains if symbol not configured. |
| `VolumeSpikeStrategy` | `volume_spike` | Returns BUY if volume exceeds `volume_spike_threshold × 1,000,000`. Uses a hard-coded 1M baseline (no rolling average). |
| `ChangePctStrategy` | `change_pct` | BUY if `change_pct >= threshold`; SELL if `<= -threshold`. Threshold from active `SignalConfig`. |
| `SMACrossoverStrategy` | `sma_crossover` | Golden cross (SMA_short crosses above SMA_long) → BUY; death cross → SELL. Requires `price_buffer.len(sym) >= sma_long`. |
| `RSIStrategy` | `rsi` | Standard Wilder RSI (14-period default). RSI ≤ oversold → BUY; RSI ≥ overbought → SELL. Attaches `_rsi` to the stock dict for downstream consumption. |

**Signal resolution priority:** `FORCE_SELL > SELL > BUY > HOLD`. The highest-priority signal across all strategies wins.

**Signal change detection:** The engine maintains `_prev_signals: dict[str, str]` in memory. `signal_changed=True` when the current signal differs from the prior tick. This resets to `False` on server restart, meaning the first tick after restart always reports `changed=False`.

**Action score (`compute_action_score`):** A composite urgency ranking derived from signal type, strategy agreement (source count), signal freshness, momentum (change_pct), volume, and RSI position. Two modes: `short` (weights momentum and liquidity; applies penny-stock and RSI momentum-zone adjustments) and `long` (weights RSI extremes and strategy agreement; rewards `price_threshold` and `sma_crossover` confirmations). The mode is selected by `preset_to_horizon(name)`: presets `aggressive` and `momentum` use `short`; `conservative` and `default` use `long`.

**PriceBuffer:** A per-symbol `collections.deque(maxlen=200)` of closing prices. Singleton `price_buffer` is shared across the engine and the prediction module. Warmed from DB on startup (see `HistoryStore.warm_price_buffer`).

**Configuration:** `strategy.json` is loaded at startup and watched for file changes every 5 seconds. Changes trigger `engine.reload_config()` and a `config_reloaded` WebSocket broadcast.

**SignalConfig:** A dataclass mirroring the active strategy preset's thresholds (`rsi_period`, `rsi_oversold`, `rsi_overbought`, `sma_short`, `sma_long`, `change_pct_threshold`). Cached on `app_state.signal_cfg`.

#### HistoryStore (`db/history_store.py`)

Async data access layer for `price_history` and `signals_log`. All public methods are coroutines. Uses `get_session()` context manager — one transaction per call.

**Write path:** Both ticks and signals are buffered in memory. Flush occurs when the buffer reaches 20 items OR when the oldest item has been buffered for ≥ 60 seconds. Writes are batched into a single INSERT using SQLAlchemy `session.add_all()`. A shared `asyncio.Lock` prevents overlapping flushes between tick and signal buffers.

**Selective signal persistence:** HOLD signals are not buffered unless `signal_changed=True`. This keeps `signals_log` lean.

**Startup warm-up:** `warm_price_buffer()` issues 2 queries to load the last 200 closing prices per symbol within a 4-hour lookback window, then pushes them into `price_buffer` oldest-first. This ensures SMA/RSI calculations are accurate immediately after restart.

**On shutdown:** `flush_ticks()` and `flush_signals()` are called to drain any remaining buffer before the process exits.

#### PortfolioManager (`portfolio/portfolio_manager.py`)

Simulated paper trading portfolio backed by SQLite. Manages a single portfolio (id=1, auto-created on startup). Exposes async methods:

- `ensure_default_portfolio()` — creates portfolio row with 0 cash if it doesn't exist.
- `get_portfolio(prices)` — returns `PortfolioSummary` with live unrealized P&L calculated against the current price map.
- `execute_buy(symbol, shares, price)` — deducts `shares × price + fees` from cash; creates or updates `Position` (weighted average cost basis); writes an immutable `Trade` record.
- `execute_sell(symbol, shares, price)` — adds net proceeds to cash; reduces/closes `Position`; writes `Trade` with `realized_pl`.
- `take_snapshot(prices)` — writes a `PortfolioSnapshot` row for equity curve charting.
- `set_cash(amount)` — replaces cash balance (for funding simulation).
- `add_position_manual(symbol, shares, avg_buy_price)` — records a pre-existing holding without deducting cash.
- `remove_position_manual(symbol)` — deletes a position without creating a trade record.
- `reset_positions()` — bulk-deletes all positions.
- `buying_power(symbol, price)` — returns max shares purchasable with available cash after fees.
- `get_trades(limit, offset, symbol)` — paginated trade history.

**Domain exceptions:** `InsufficientCashError`, `InsufficientSharesError`, `PositionNotFoundError`, `PortfolioNotFoundError`. These are mapped to HTTP 422/404 in route handlers.

**Trade execution guards:** Buys and sells require market-open status (`Depends(require_market_open)`) and a rate limit check (`Depends(require_trade_rate_limit)`). Both are defined in `api/deps.py`.

#### Backtester (`strategy/backtester.py`)

Pure simulation engine — no DB writes, no side effects. Deterministic: same price history + config → same result.

**Key design decisions:**
- Signal generated at tick `t` is executed at tick `t+1` (no lookahead bias).
- Last pending signal is never executed (no peeking beyond history).
- Slippage of 0.1% applied to every execution price.
- Position capped at 25% of current equity per trade.
- One position at a time — no pyramiding.
- Brokerage fees applied on both sides via `calculate_fee()`.
- Open positions at end of history are not force-closed; unrealized P&L is tracked separately.
- Symbols with average volume < 100,000 (last 30 ticks) are skipped with `skipped="low_liquidity"`.
- Minimum 50 ticks required; otherwise `skipped="insufficient_data"`.

**Strategy configs (presets):**

| Preset | RSI Oversold | RSI Overbought | SMA Short | SMA Long | Stop Loss | Change % Threshold |
|---|---|---|---|---|---|---|
| conservative | 25 | 75 | 10 | 30 | 4.0% | 4.0% |
| default | 30 | 70 | 5 | 20 | 5.0% | 3.0% |
| aggressive | 35 | 65 | 3 | 10 | 8.0% | 2.0% |
| momentum | 40 | 60 | 5 | 15 | 6.0% | 1.5% |

**Run modes:** `single` (one config), `variants` (list of configs, sorted by return_pct desc), `presets` (all 4 presets), `walk_forward` (N rolling windows, evaluates out-of-sample stability).

**Sharpe ratio note:** Computed as a trade-based proxy — each closed trade is treated as one period, scaled by √252. Not time-normalized; useful for relative comparison only.

#### Signal Evaluator / Phase 6 (`analytics/signal_evaluator.py`)

Evaluates historical signals against actual forward prices across three time horizons. Runs every 10 minutes (first run staggered by 60 seconds after startup).

See Section 6 for full detail.

#### Forward Tracker / Phase 7 (`analytics/forward_tracker.py`)

Opens and manages `ForwardTrade` rows corresponding to live actionable signals. Called from the poll loop via `asyncio.create_task()`.

See Section 4.3 for full detail.

#### PredictionEngine (`prediction/prediction_engine.py`)

Enriches signal dicts with a `prediction` block derived from 4 sub-models. Runs synchronously on every poll tick, target runtime < 50ms per symbol.

See Section 4.1 for full detail.

#### AppState (`state.py`)

Global singleton `app_state: AppState` shared across all modules to avoid circular imports. Contains:

| Field | Type | Purpose |
|---|---|---|
| `stocks` | `dict[str, dict]` | Latest raw stock data keyed by symbol |
| `signals` | `dict[str, dict]` | Latest enriched signal per symbol (includes prediction block) |
| `last_update` | `float` | Unix timestamp of last successful poll |
| `data_source` | `str` | `"live"` or `"snapshot"` |
| `data_stale` | `bool` | True when prices come from snapshot, not live scrape |
| `stale_reason` | `str \| None` | Human-readable stale explanation |
| `ws_clients` | `int` | Current WebSocket connection count |
| `engine` | `SignalEngine` | Singleton signal engine instance |
| `strategy` | `str` | Active preset name |
| `signal_cfg` | `SignalConfig` | Cached thresholds for the active preset |
| `history_store` | `HistoryStore` | DB access layer |
| `portfolio` | `PortfolioManager` | Simulated portfolio |
| `prediction_engine` | `PredictionEngine` | Prediction enrichment |
| `started_at` | `float` | Server startup Unix timestamp |

---

### 2.2 Database Schema

**Database:** SQLite (default), async via `aiosqlite`. PostgreSQL supported via `DATABASE_URL` env var. Uses SQLAlchemy async ORM (`AsyncSession`, `async_sessionmaker`).

**SQLite tuning** (applied on every connection): WAL journal mode, `synchronous=NORMAL`, `busy_timeout=15000ms`, 8MB page cache. `NullPool` used for SQLite to avoid connection-pool exhaustion under concurrent async writes.

---

#### `price_history`

One row per scraped price tick per symbol.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | Auto-increment |
| `symbol` | String(16) | Indexed |
| `sector` | String(64) | Nullable |
| `ldcp` | Float | Last Day Closing Price; nullable |
| `open_price` | Float | Nullable; NULL for historical EOD rows |
| `high` | Float | Nullable; NULL for historical EOD rows |
| `low` | Float | Nullable; NULL for historical EOD rows |
| `close` | Float | Not null |
| `volume` | Integer | Nullable |
| `change_pct` | Float | Nullable |
| `source` | String(8) | `"live"` \| `"snapshot"` \| `"historical"` |
| `scraped_at` | Integer | Unix timestamp; not null; indexed |

**Indexes:** `ix_ph_symbol_time (symbol, scraped_at)`, `ix_price_history_symbol`, `ix_price_history_scraped_at`.

**Stale ticks are not persisted.** The poll loop skips `save_tick()` for rows where `s.get("stale")` is True — snapshot data is already in the DB.

---

#### `signals_log`

Every generated non-HOLD or changed signal.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | Auto-increment |
| `symbol` | String(16) | Indexed |
| `signal` | String(16) | `BUY` \| `SELL` \| `HOLD` \| `FORCE_SELL` |
| `prev_signal` | String(16) | Nullable |
| `signal_changed` | Boolean | |
| `signal_sources` | Text | JSON array, e.g. `["rsi","sma_crossover"]`; nullable |
| `action_score` | Float | Nullable |
| `horizon` | String(8) | Stored as `"default"` — original per-signal derivation removed |
| `rsi` | Float | Nullable |
| `sma5` | Float | Nullable (actually the active preset's SMA_short, not always period-5) |
| `sma20` | Float | Nullable (active preset's SMA_long) |
| `price` | Float | Nullable |
| `volume` | Integer | Nullable |
| `confidence` | Float | Nullable; capped at 0.85 at storage boundary |
| `time_horizon` | String(32) | Nullable; e.g. `"~3 days"` |
| `generated_at` | Integer | Unix timestamp; not null; indexed |

**Indexes:** `ix_sl_symbol_time (symbol, generated_at)`, `ix_sl_signal_time (signal, generated_at)`.

---

#### `portfolio`

One row per portfolio (currently always a single row, id=1).

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `name` | String(64) | Default `"Main Portfolio"` |
| `cash_available` | Float | Current cash balance |
| `created_at` | Integer | Unix timestamp |
| `updated_at` | Integer | Unix timestamp |

**Relationships:** Has many `Position`, `Trade`, `PortfolioSnapshot` (cascade delete-orphan).

---

#### `positions`

Open stock holdings. One row per symbol per portfolio. Updated in-place on additional buys (weighted average cost recalculated).

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `portfolio_id` | Integer FK → portfolio.id | |
| `symbol` | String(16) | Indexed |
| `shares` | Float | |
| `avg_buy_price` | Float | Weighted average cost basis |
| `total_invested` | Float | `shares × avg_buy_price` |
| `opened_at` | Integer | Unix timestamp |
| `notes` | Text | Nullable |

**Constraints:** `UNIQUE(portfolio_id, symbol)`.

---

#### `trades`

Immutable trade ledger. Never updated or deleted.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `portfolio_id` | Integer FK → portfolio.id | |
| `symbol` | String(16) | Indexed |
| `trade_type` | String(16) | `BUY` \| `SELL` \| `FORCE_SELL` |
| `shares` | Float | |
| `price_per_share` | Float | |
| `total_value` | Float | `shares × price` |
| `brokerage_fee` | Float | |
| `net_value` | Float | `total_value ± fee` |
| `realized_pl` | Float | Nullable; None for BUY trades |
| `signal_id` | Integer FK → signals_log.id | Nullable |
| `executed_at` | Integer | Unix timestamp; indexed |
| `notes` | Text | Nullable |

**Indexes:** `ix_trade_portfolio_time (portfolio_id, executed_at)`, `ix_trade_symbol_time (symbol, executed_at)`.

---

#### `portfolio_snapshots`

Periodic portfolio value snapshots for the equity curve chart.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `portfolio_id` | Integer FK → portfolio.id | |
| `total_value` | Float | Cash + market value of all positions |
| `cash` | Float | |
| `invested_value` | Float | Market value of all positions |
| `total_pl` | Float | Unrealized + realized |
| `unrealized_pl` | Float | |
| `realized_pl` | Float | |
| `snapshotted_at` | Integer | Unix timestamp; indexed |

**Index:** `ix_snap_portfolio_time (portfolio_id, snapshotted_at)`.

---

#### `prediction_log`

Non-neutral predictions logged for BUY/SELL/FORCE_SELL signals. Outcome resolution is not currently automated — `outcome` field remains `"pending"` indefinitely after insert.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `symbol` | String(16) | Indexed |
| `prediction_type` | String(32) | e.g. `"up_signal"` \| `"down_signal"` |
| `predicted_at` | Integer | Unix timestamp |
| `price_at_prediction` | Float | |
| `predicted_direction` | String(8) | `"up"` \| `"down"` |
| `confidence` | Float | Capped at 0.85 |
| `time_horizon_days` | Integer | Nullable |
| `target_price` | Float | Nullable; not populated (always NULL) |
| `outcome` | String(16) | Always `"pending"` — not resolved by any current process |
| `outcome_price` | Float | Nullable; never populated |
| `outcome_at` | Integer | Nullable; never populated |

---

#### `signal_outcomes`

Results of the Signal Validation Engine. One row per `(symbol, signal_ts)` pair. Outcomes are filled progressively across multiple evaluator runs via COALESCE upsert.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `symbol` | String(16) | Indexed |
| `signal` | String(16) | `BUY` \| `SELL` \| `HOLD` \| `FORCE_SELL` |
| `signal_sources` | Text | JSON string from signals_log |
| `timestamp` | Integer | Signal `generated_at` (Unix ts) |
| `price_at_signal` | Float | Nullable; last price ≤ signal time |
| `price_short` | Float | Nullable; first price ≥ signal+30min |
| `price_medium` | Float | Nullable; first price ≥ signal+2h |
| `price_long` | Float | Nullable; session-based (see §6) |
| `outcome_short` | String(16) | `"correct"` \| `"incorrect"` \| `"neutral"` \| NULL |
| `outcome_medium` | String(16) | Same; NULL until horizon elapses |
| `outcome_long` | String(16) | Same; NULL until horizon elapses |
| `short_latency_sec` | Integer | Nullable; seconds between target ts and actual tick found |
| `medium_latency_sec` | Integer | Nullable |
| `long_latency_sec` | Integer | Nullable |
| `evaluated_at` | Integer | Updated on every run |

**Constraints:** `UNIQUE(symbol, timestamp)` — idempotency enforced via COALESCE upsert.
**Indexes:** `ix_so_symbol_time (symbol, timestamp)`, `ix_so_signal (signal)`, `ix_so_evaluated_at`.

---

#### `forward_trades`

Forward-testing trades. One open trade per symbol at any time.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `symbol` | String(16) | Indexed |
| `signal` | String(16) | `BUY` \| `SELL` \| `FORCE_SELL` |
| `entry_price` | Float | |
| `entry_time` | Integer | Unix timestamp |
| `max_price_seen` | Float | Updated every tick while OPEN |
| `min_price_seen` | Float | Updated every tick while OPEN |
| `exit_price` | Float | Nullable; NULL while OPEN |
| `exit_time` | Integer | Nullable; NULL while OPEN |
| `status` | String(8) | `"OPEN"` \| `"CLOSED"` |
| `outcome` | String(8) | `"STRONG_WIN"` \| `"WEAK_WIN"` \| `"BREAKEVEN"` \| `"LOSS"` |
| `mfe_pct` | Float | Max Favourable Excursion %; finalised on close |
| `mae_pct` | Float | Max Adverse Excursion %; finalised on close |
| `duration_minutes` | Float | `(exit_time - entry_time) / 60`; finalised on close |

**Constraints:** `UNIQUE(symbol, entry_time)`.
**Indexes:** `ix_ft_symbol_status (symbol, status)`, `ix_ft_entry_time`.

---

### 2.3 API Layer

All routes are prefixed and registered on the FastAPI `app` instance in `main.py`. CORS allows all origins.

---

#### Core signals/stocks (`/api` — `routes.py`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/stocks` | Returns all cached raw stock dicts. Query params: `sector` (filter), `limit` (1–500, default 100). |
| GET | `/api/signals` | Returns all cached enriched signal dicts. Query params: `signal` (BUY/SELL/HOLD/FORCE_SELL), `sector`, `limit`. |
| GET | `/api/signals/{symbol}` | Returns the current signal for a single symbol. 404 if not tracked. |
| GET | `/api/status` | Returns system health, market state, data staleness, strategy, uptime, WS client count. |
| POST | `/api/strategy/{name}` | Switches active strategy preset (conservative/default/aggressive/momentum). Immediately reprocesses all cached signals and broadcasts a `snapshot` WS message. |
| POST | `/api/config/reload` | Hot-reloads `strategy.json` from disk. |

---

#### History (`/api/history` — `history_routes.py`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/history/stats` | Returns tick count and time range per symbol (diagnostic). |
| GET | `/api/history/{symbol}` | Returns up to `n` OHLCV ticks oldest-first. Query params: `n` (1–2000, default 200), `since` (Unix ts). 404 if no data. |
| GET | `/api/history/{symbol}/signals` | Returns recent non-HOLD/changed signals for symbol, newest-first. Query param: `limit` (1–500, default 50). 404 if no data. |

---

#### Portfolio and Trades (`/api` — `portfolio_routes.py`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/portfolio` | Full portfolio summary: cash, positions with live P&L, aggregate realized P&L. |
| GET | `/api/portfolio/buying-power/{symbol}` | Max shares buyable with current cash after fees. 404 if no live price. |
| POST | `/api/portfolio/cash` | Set cash balance. Body: `{ amount: float }`. |
| GET | `/api/portfolio/positions` | Alias for GET /api/portfolio. |
| POST | `/api/portfolio/positions` | Manually record a pre-existing holding. Cash NOT deducted. |
| DELETE | `/api/portfolio/positions/{symbol}` | Remove a position without creating a trade record. |
| DELETE | `/api/portfolio/reset` | Delete all positions. For testing/reconciliation. |
| GET | `/api/portfolio/snapshots` | Historical equity curve snapshots, oldest-first. Query: `limit` (1–5000, default 500). |
| POST | `/api/trades/buy` | Execute a BUY. Requires market OPEN + rate limit. Body: `{ symbol, shares, price, notes? }`. |
| POST | `/api/trades/sell` | Execute a SELL. Same guards. Body: `{ symbol, shares, price, notes? }`. |
| GET | `/api/trades` | Paginated trade history, newest-first. Query: `limit`, `offset`. |
| GET | `/api/trades/{symbol}` | Paginated trade history for one symbol. |

---

#### Backtest (`/api/backtest` — `backtest_routes.py`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/backtest/presets` | List all 4 built-in preset strategy configs. |
| POST | `/api/backtest/run` | Run a backtest. Body: `{ symbol, mode, start_ts?, end_ts?, config?, variants? }`. Modes: `single`, `variants`, `presets`. Returns full result with `trade_log` and `equity_curve`. |
| POST | `/api/backtest/walk-forward` | Walk-forward validation. Body: `{ symbol, n_windows, train_ratio, config? }`. |
| GET | `/api/backtest/results` | In-session backtest result history (newest-first, max 50 runs, not persisted to DB). Query: `limit`, `symbol`. |

---

#### Analytics (`/api/analytics` — `analytics_routes.py`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/analytics/summary` | Global accuracy metrics across all symbols and signal types. Returns per-signal-type breakdown (BUY/SELL/HOLD/FORCE_SELL) with accuracy at each horizon. |
| GET | `/api/analytics/symbol/{symbol}` | Per-symbol accuracy, best/worst signal type, average return. 404 if no evaluated signals. |
| GET | `/api/analytics/sources` | Per-indicator accuracy (RSI, SMA, momentum, volume) by deserialising `signal_sources` JSON. |

---

#### Performance / Forward-test (`/api/performance` — `performance_routes.py`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/performance/live` | All OPEN forward trades, newest-first. |
| GET | `/api/performance/history` | Paginated CLOSED forward trades. Query: `symbol`, `limit`, `offset`. |
| GET | `/api/performance/summary` | Aggregate stats: win_rate, avg_win_pct, avg_loss_pct, expectancy, avg_mfe, avg_mae, avg_return_per_trade, avg_return_per_hour, total_closed, total_open. |

---

#### Predictions (`/api/predictions` — `prediction_routes.py`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/predictions` | All non-neutral predictions (or filtered). Query: `direction`, `signal`, `min_conf`, `limit`. Sorted by confidence desc. |
| GET | `/api/predictions/{symbol}` | Prediction + full signal context for a single symbol. |

---

#### System (`/api/system` — `system_routes.py`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/system/status` | Structured snapshot: `market`, `data`, `signals`, `trading`, `system` blocks. Primary endpoint for frontend to determine trade-execution eligibility and market state badge. |
| POST | `/api/system/fetch-historical` | Triggers background EOD history download for up to 41 PSX symbols. Query: `symbols` (comma-separated; defaults to all known). |

---

#### WebSocket (`/ws`)

Single endpoint. On connect: sends a `snapshot` message with all current signals. On each poll tick: broadcasts an `update` message. Clients send `"ping"` text frames; server replies `"pong"`. Latency is computed by the client from ping round-trip time.

**Broadcast payload shape:**
```json
{
  "type":         "snapshot" | "update" | "config_reloaded",
  "timestamp":    1234567890.123,
  "source":       "live" | "snapshot",
  "stale":        false,
  "stale_reason": null,
  "strategy":     "default",
  "config_at":    1234567890.0,
  "all":          [...signals],
  "changed":      [...signals_that_changed],
  "client_count": 3
}
```

#### Health Check

`GET /health` — returns `{ status: "ok", uptime: float }`. No auth.

---

### 2.4 Background Tasks

All tasks are `asyncio.Task` objects created in the `lifespan` context manager. They are cancelled on shutdown.

#### Poll Loop (`_poll_loop`)

- **Interval:** `PSX_POLL_INTERVAL` env var, default 15 seconds.
- **Behavior:** Scrapes PSX → runs signal engine → enriches with predictions → updates `app_state.stocks` and `app_state.signals` → persists ticks/signals (fire-and-forget) → creates forward trades (batch) → updates open forward trades → broadcasts WS update.
- **Stale guard:** Tick persistence and forward-trade creation are skipped when `is_stale=True`. Signal persistence runs regardless of staleness.

#### Portfolio Snapshot Loop (`_portfolio_snapshot_loop`)

- **Interval:** `PSX_SNAPSHOT_INTERVAL` env var, default 300 seconds (5 minutes).
- **Behavior:** Calls `portfolio.take_snapshot(prices)` with current live prices from `app_state.stocks`. Non-fatal: errors are logged and skipped.

#### Signal Evaluation Loop (`_signal_evaluation_loop`)

- **Interval:** `PSX_EVALUATOR_INTERVAL` env var, default 600 seconds (10 minutes).
- **First run:** Staggered by 60 seconds after startup.
- **Behavior:** Calls `evaluate_pending_signals(batch_size=500)`. Processes signals whose short horizon (30 min) has elapsed. Upserts outcomes progressively — NULL fields are filled as their time horizon elapses; resolved fields are never overwritten (COALESCE). Non-fatal: errors are logged and skipped.

#### Strategy File Watcher (`_watch_strategy`)

- **Interval:** Every 5 seconds.
- **Behavior:** Compares `strategy.json` mtime to last-known mtime. On change: calls `engine.reload_config()`, updates `app_state.config_loaded_at`, broadcasts `config_reloaded` WS message.

#### Startup sequence (lifespan)

1. `init_db()` — creates all tables if missing.
2. `portfolio.ensure_default_portfolio()` — creates portfolio row if not exists.
3. `history_store.warm_price_buffer(price_buffer)` — loads last 200 prices per symbol from DB.
4. `recover_open_trades()` — force-closes any OPEN forward trades older than 1 trading day (21,600 seconds).
5. Live warm-up fetch — one full poll cycle to populate `app_state.stocks` and `app_state.signals` before serving requests.
6. All 4 background tasks are started.

---

## 3. Frontend Architecture

**Stack:** React 18, Vite, Zustand (state management), Tailwind CSS. No server-side rendering.

### 3.1 State Management

Five Zustand stores manage all application state. Data flows: WebSocket/API → store setter → React component re-render via selector subscription.

#### `useMarketStore`

Source of truth for live signal data. Updated by the WebSocket hook on every `snapshot` and `update` message.

| State field | Type | Notes |
|---|---|---|
| `signals` | `Signal[]` | Full signal array from last WS message |
| `changedSignals` | `Signal[]` | Signals that changed in the last tick |
| `isConnected` | `boolean` | |
| `wsStatus` | `string` | Raw: `connecting \| open \| closed \| error` |
| `connectionStatus` | `string` | Semantic: `connected \| reconnecting \| disconnected` |
| `latency` | `number \| null` | Ping round-trip in ms |
| `strategy` | `string` | Active preset name |
| `dataStale` | `boolean` | |
| `staleReason` | `string \| null` | |
| `source` | `string` | `"live"` \| `"snapshot"` |
| `systemStatus` | `object \| null` | Populated by polling `/api/system/status` |

#### `usePortfolioStore`

Manages portfolio data and equity value history. Seeded from DB snapshots (`/api/portfolio/snapshots`) on the first fetch per session. Subsequent fetches append in-memory points. Max 500 history points kept.

Provides `optimisticTrade(side, shares, price)` which speculatively adjusts `cash_available` before the API call confirms, and returns a rollback function.

#### `useUIStore`

Cross-cutting UI state: active tab (`dashboard \| portfolio \| charts \| database \| backtest \| performance`), toast notifications (auto-dismiss after 4.5s), trade intent pre-fill (symbol + side + price passed from SignalTable to TradePanel), expanded symbol in SignalTable, watchlist (persisted to `localStorage`).

#### `usePerformanceStore`

Forward-test performance data: `liveTrades`, `history` (paginated closed trades), `historyTotal`, `summary`. Fetched via `fetchAll()` which parallelizes `fetchSummary`, `fetchLive`, and `fetchHistory`.

#### `useBacktestStore`

In-session backtest run history (max 20 runs, not persisted). Each entry records symbol, mode, best strategy name, return_pct, win_rate, total_trades.

---

### 3.2 Pages

#### Dashboard (`pages/Dashboard.jsx`)

Primary view. Contains `SignalTable` (main signal grid with filtering, sorting, sparklines, prediction panels) and `SummaryCards`. Reads from `useMarketStore`.

#### Portfolio (`pages/Portfolio.jsx`)

Paper trading interface. Contains `PortfolioBar` (summary ribbon), `PositionsTable` (open holdings with live P&L), `TradePanel` (buy/sell order form), `PortfolioChart` (equity curve). Reads from `usePortfolioStore`. Trade form can be pre-filled via `useUIStore.setTradeIntent()` from the Dashboard.

#### Backtest (`pages/Backtest.jsx`)

Backtesting interface. Contains `BacktestPanel` (config form), `ResultsTable` (metrics), `EquityChart` (equity curve), `BacktestHistory` (session run log from `useBacktestStore`).

#### Performance (`pages/Performance.jsx`)

Forward-test results. Contains `PerformancePanel` (summary stats: win rate, expectancy, MFE/MAE), `LiveTradesTable` (open forward trades), `TradeHistoryTable` (closed trades, paginated), `PerformanceChart`. Reads from `usePerformanceStore`.

#### Charts (`pages/Charts.jsx`)

Price charts for individual symbols using data from `/api/history/{symbol}`. Implementation detail not fully inspected.

#### Database (`pages/Database.jsx`)

DB diagnostics view using data from `/api/history/stats`. Shows tick counts per symbol.

---

### 3.3 Key Components

**`SignalTable` (`components/dashboard/SignalTable.jsx`)** — Main signal grid. Columns include symbol, sector, price, change_pct, volume, RSI, SMA values, signal badge, action_score, signal sources. Expandable rows show `PredictionPanel`. Supports filtering by signal type, sector, text search, and sorting. Integrates sparkline charts (`Sparkline.jsx`) for price trend visualization.

**`TradePanel` (`components/portfolio/TradePanel.jsx`)** — Buy/sell order form. Inputs: symbol (auto-filled from trade intent or manual entry), quantity, price (auto-filled from live signal). Shows fee breakdown and estimated total before confirmation. Calls `POST /api/trades/buy` or `/api/trades/sell`.

**`PortfolioBar` (`components/portfolio/PortfolioBar.jsx`)** — Sticky summary ribbon showing cash, invested value, total portfolio value, unrealized P&L, realized P&L.

**`BacktestPanel` (`components/backtest/BacktestPanel.jsx`)** — Strategy config form. Supports mode selection (single/variants/presets), custom RSI/SMA/stop-loss parameter input, symbol and date-range selection.

**`PerformancePanel` (`components/performance/PerformancePanel.jsx`)** — Aggregate forward-test stats card: win rate, avg win/loss %, expectancy, MFE/MAE, trade counts.

**`PredictionPanel` (`components/dashboard/PredictionPanel.jsx`)** — Expanded signal detail showing prediction direction, confidence, trade action, hold days, risk level, expected move, reward/risk ratio, and basis indicators.

**`StatusBar` (`components/StatusBar.jsx`)** — Shows WS connection status, data staleness banner, market state, last update timestamp.

---

### 3.4 Real-time Data

#### WebSocket (`hooks/useWebSocket.js`)

Mounts once at the App root (`WSBoot` component) and remains alive across tab switches. URL: `VITE_WS_URL` env var (default `ws://localhost:8000/ws`).

- **Reconnect:** Exponential backoff, `delay = min(1000 × 2^retries, 10000ms)`, max 12 retries.
- **Ping/pong:** Client sends `"ping"` every 5 seconds; server replies `"pong"`. Round-trip time is measured and stored as `latency` in `useMarketStore`.
- **Notifications:** On `update` messages with changed signals that are BUY/SELL/FORCE_SELL or have confidence ≥ 0.7, the hook fires a browser `Notification` API notification (fallback: toast via `useUIStore.showToast`).

#### Polling Fallbacks

Portfolio data is fetched via REST polling (not WebSocket) by the Portfolio page. Performance data is fetched via REST polling by the Performance page. There is no automatic polling interval in the store — components trigger fetches on mount and on manual refresh.

---

## 4. Trading Logic

### 4.1 Signal Generation

**Indicators used:**

- **RSI (Relative Strength Index):** Wilder's RSI, period configurable per preset (default 14). Computed from `price_buffer` (live engine) or from the historical price series slice (backtester). BUY when RSI ≤ oversold threshold; SELL when RSI ≥ overbought threshold. PSX circuit-breaker clamp of ±7.5% daily change applied in the backtester before change_pct signals.

- **SMA Crossover:** Short and long SMA periods configurable per preset (default 5/20). Golden cross (short crosses above long) → BUY; death cross (short crosses below long) → SELL. Requires `price_buffer.len(sym) >= sma_long`.

- **Change % (Momentum):** BUY if `change_pct >= threshold`; SELL if `change_pct <= -threshold`. Threshold configurable per preset (default 3.0%). In the backtester, change_pct is clamped to ±7.5% before evaluation.

- **Price Threshold:** Symbol-level price levels configured in `strategy.json`. `buy_below`, `sell_above`, `stop_loss` per symbol. Abstains if symbol not configured.

- **Volume Spike:** BUY if current volume > `volume_spike_threshold × 1,000,000`. Uses hard-coded 1M baseline — no rolling volume average.

**Score calculation:** `compute_action_score()` produces a numeric urgency score. Non-zero only for non-HOLD signals. The formula is horizon-dependent:

- **Short horizon** (aggressive/momentum presets): Base (FORCE_SELL=10000, BUY/SELL=1000) + `source_count × 25` + `changed × 200` + `|change_pct| × 25` + volume tier bonus (0/75/150/300) + `log10(volume) × 15` + `intraday_range × 20` − penny stock penalty (−300 if price < 10 PKR) + RSI momentum zone bonus.
- **Long horizon** (conservative/default presets): Base + `source_count × 40` + `changed × 80` + `|change_pct| × 8` + `log10(volume) × 6` + RSI extreme bonus + price_threshold bonus (+200) + sma_crossover bonus (+150).

**PredictionEngine sub-models and confidence formula:**

Four sub-models cast directional votes:

| Sub-model | Method | Vote condition |
|---|---|---|
| Momentum | OLS linear regression on last 20 prices | Abstains if R² < 0.10 or |slope| trivially flat |
| RSI mean reversion | Distance from 30/70 thresholds | BUY if RSI ≤ 30; SELL if RSI ≥ 70 |
| Bollinger Bands | 2σ band position | BUY if percent_b < 0.10; SELL if percent_b > 0.90 |
| Support/Resistance | Rolling min/max proximity (50–200 tick dynamic window) | BUY if proximity < 0.15; SELL if proximity > 0.85 |

Volume acts as a multiplier (not an independent vote): `vol_ratio > 1.5` → boost all strengths 30%; `vol_ratio < 0.7` → dampen 30%.

Weighted confidence formula:
```
up_strength   = Σ MODEL_WEIGHTS[model] × strength  (for "up" votes)
down_strength = Σ MODEL_WEIGHTS[model] × strength  (for "down" votes)
total_strength = up_strength + down_strength
agreement_factor = |up - down| / total_strength   ∈ [0, 1]
confidence = total_strength × agreement_factor     clamped 0.0–0.99
```

Weights: `momentum=0.35, rsi=0.20, bollinger=0.15, support=0.10, resistance=0.10`.

Trade action thresholds: `confidence ≥ 0.12` AND `total_strength ≥ 0.10` AND `agreement_factor ≥ 0.15`. Below any threshold → `"avoid"`.

Confidence is hard-capped at 0.85 at the DB storage boundary (`_cap_confidence()`).

---

### 4.2 Trade Execution (Simulated)

**PortfolioManager rules:**

- One position per symbol (no pyramiding in the paper portfolio).
- Additional buys on an existing position are accumulated with weighted-average cost recalculation.
- Sells reduce or fully close the position.
- Realized P&L = `net_proceeds − cost_basis` (pre-CGT).
- Cash balance is the single source of liquidity — buys fail with `InsufficientCashError` if insufficient funds.

**PSX fee structure (`portfolio/fees.py`):**

| Fee | Rate | Notes |
|---|---|---|
| Commission (TREC) | 0.15% of trade value | Configurable via `strategy.json` |
| CDC charge | PKR 10.00 flat | Per side |
| SECP levy | 0.0115% of trade value | Configurable |
| Total (approx) | ~0.1615% + PKR 10 per side | Round-trip ≈ 0.323% + PKR 20 |

Capital Gains Tax (CGT) is not computed. All P&L shown is pre-CGT.

**Trade execution guards (`api/deps.py`):**

- `require_market_open`: Returns HTTP 423 if `not mkt.is_open` or `app_state.data_stale`. Prevents trading on stale prices.
- `require_trade_rate_limit`: Rate limit details not fully inspected; raises HTTP 429 on breach.

**Backtester trade mechanics:**

- Slippage: 0.1% on execution price (buy at `price × 1.001`; sell at `price × 0.999`).
- Position size: `min(position_size_pct × cash, equity × 25%)` — capped at 25% of current equity.
- Shares rounded down to whole shares. One safety trim if total cost overshoots cash after rounding.
- Stop-loss in backtester: if `current_price ≤ avg_cost × (1 − stop_loss_pct/100)` → FORCE_SELL signal generated.

---

### 4.3 Forward Testing

Forward testing observes what happens after a live signal fires, using actual subsequent price data. It is not backtesting.

**Trade creation (`create_trades_batch`):**

Called once per poll tick (batched). For each actionable signal (BUY/SELL/FORCE_SELL) that is not stale: checks if an OPEN trade already exists for the symbol (P1 guard — one OPEN trade per symbol). If not, inserts a `ForwardTrade` row with `status=OPEN`, `outcome=BREAKEVEN`, `mfe=mae=0.0`.

Idempotency: `UNIQUE(symbol, entry_time)` with `ON CONFLICT DO NOTHING`.

**Trade update (`update_open_trades`):**

Called every poll tick. Fetches all OPEN forward trades for symbols in the current tick in one SELECT. Per trade:

1. Compute per-symbol TP/SL thresholds dynamically from recent price volatility (P3): `TP = vol × 0.8` clamped [1.5%, 5.0%]; `SL = vol × 0.6` clamped [1.0%, 3.0%]. Falls back to defaults (TP=2.0%, SL=1.5%) when buffer is too shallow.
2. Update `max_price_seen` and `min_price_seen`.
3. **Opposite-signal close (P2):** If the current signal is the opposite direction (e.g., open trade is BUY but current signal is SELL), close immediately.
4. Check exit conditions: TP hit, SL hit, or time exit (trade open ≥ 21,600 seconds = 1 PSX trading day).
5. On exit: set `exit_price`, `exit_time`, `status=CLOSED`, compute `duration_minutes`, compute `pnl_pct`, apply four-bucket outcome classification (P8), compute MFE/MAE.

**Outcome classification (P8):**

| Outcome | Condition |
|---|---|
| `STRONG_WIN` | `pnl_pct ≥ tp_pct` (full take-profit achieved) |
| `WEAK_WIN` | `0.2% < pnl_pct < tp_pct` |
| `BREAKEVEN` | `|pnl_pct| ≤ 0.2%` |
| `LOSS` | `pnl_pct < -0.2%` |

For performance summary: wins = `STRONG_WIN + WEAK_WIN`; `BREAKEVEN` is excluded from win/loss buckets.

**MFE/MAE:**
- BUY: `mfe = (max_price_seen - entry) / entry × 100`; `mae = (min_price_seen - entry) / entry × 100` (typically negative).
- SELL: `mfe = (entry - min_price_seen) / entry × 100`; `mae = (entry - max_price_seen) / entry × 100`.

**Startup recovery (`recover_open_trades`):** On startup, any OPEN trades with `entry_time < now - 21600` are force-closed at their `entry_price` (best available fallback). This prevents stale OPEN trades from accumulating across restarts.

**Performance metrics (`get_performance_summary`):**

- `win_rate` = (STRONG_WIN + WEAK_WIN) / total_closed × 100
- `avg_win_pct` = mean P&L% of winning trades
- `avg_loss_pct` = mean P&L% of LOSS trades
- `expectancy` = `win_rate × avg_win − (1 − win_rate) × |avg_loss|`
- `avg_mfe`, `avg_mae` = averages across all closed trades
- `avg_return_per_trade` = mean P&L% across all closed trades
- `avg_return_per_hour` = mean P&L% per hour, excluding zero-duration trades

---

## 5. Backtesting Engine

### Strategy Rules

The backtester applies the same 3 indicator-based signals as the live engine (RSI, SMA crossover, change_pct momentum) plus a stop-loss rule. It does NOT apply `PriceThresholdStrategy` (requires `strategy.json` per-symbol config) or `VolumeSpikeStrategy` (uses an unreliable hard-coded baseline).

The signal generation function `_generate_signal()` in `backtester.py` is a self-contained reimplementation of the live engine's logic, applied over a pre-loaded price series rather than a rolling buffer.

### Simulation Mechanics

- **Data source:** `price_history` table, ordered `scraped_at ASC`.
- **Signal generation lag:** Signal at tick `t` is executed at tick `t+1`.
- **Warmup:** The first `max(sma_long, rsi_period + 2)` ticks produce only HOLD signals — no trades.
- **Slippage:** 0.1% on execution price.
- **Position sizing:** `min(position_size_pct × cash, equity × 25%)`. Defaults to all-in (position_size_pct=1.0).
- **Fees:** Uses `calculate_fee()` — identical to the live paper trading fee model.
- **Single position at a time:** No pyramiding.
- **End-of-history:** No last pending signal execution. Open positions at end of history are mark-to-market for `unrealized_pl`.
- **Return calculation:** `(final_equity − starting_cash) / starting_cash × 100`. `final_equity = cash + open_position_market_value`.

### Metrics

| Metric | Formula |
|---|---|
| `total_return_pct` | `(final_equity − starting_cash) / starting_cash × 100` |
| `win_rate` | `winning_trades / total_closed_trades × 100` |
| `avg_gain_pct` | Mean return % of winning trades |
| `avg_loss_pct` | Mean return % of losing trades (negative) |
| `max_drawdown_pct` | Largest peak-to-trough equity drop |
| `profit_factor` | `gross_profit / gross_loss`; `None` if no losses |
| `sharpe_ratio` | Trade-based proxy: `mean(returns) / std(returns) × √252`; `None` if < 3 closed trades |
| `realized_pl` | Sum of all closed-trade P&L after fees |
| `unrealized_pl` | Mark-to-market of any still-open position at end of history |

### Walk-Forward Validation

History is split into `n_windows` equal segments (default 3). Each window is split `train_ratio/test_ratio` (default 70%/30%). The full window is simulated; only trades occurring in the test (out-of-sample) portion are counted for metrics. Stability = standard deviation of per-window returns — lower is better.

### Limitations

- RSI in the backtester is a simple average-of-gains/average-of-losses (non-smoothed) Wilder calculation — not the exponentially smoothed version used in some charting libraries.
- Walk-forward passes the full window to `_simulate()` — the train portion affects indicator warmup and potential trades, but only test-portion trades are reported. This is not a pure out-of-sample test (train-period trades could affect cash/position state entering the test period).
- Backtest results are NOT persisted to the database — only held in an in-process list capped at 50 entries. Results are lost on server restart.
- The `sma5`/`sma20` column names in `signals_log` are misleading — they actually store the values for whatever SMA windows the active preset uses, not necessarily periods 5 and 20.

---

## 6. Analytics (Phase 6)

### Signal Evaluation Logic

`evaluate_pending_signals()` in `analytics/signal_evaluator.py` runs every 10 minutes. It processes signals from `signals_log` whose short horizon has elapsed (30 minutes past `generated_at`) in batches of up to 500.

### Time Horizons

| Horizon | Target timestamp | Outcome timing |
|---|---|---|
| Short | `signal_ts + 1800s` (30 min) | Evaluated on the next run after 30 min have elapsed |
| Medium | `signal_ts + 7200s` (2 hours) | Evaluated on the next run after 2h have elapsed |
| Long | Session-based (see below) | Evaluated on the next run after the target has elapsed |

**Long horizon target:** If the signal was generated within 2 hours of 15:30 PKT (session close), the target is the next trading day's open (09:30 PKT on the next business day). Otherwise, the target is 15:30 PKT on the same day.

### Price Lookup Pattern

All price lookups use batch queries — not N+1 per signal. For each unique symbol, a single time-range SELECT fetches all ticks in the relevant window. Python `bisect` finds the closest tick per target timestamp. This reduces database round-trips from O(N×horizons) to O(unique_symbols).

Search window: ±24 hours around each target timestamp (covers weekends, holidays, and market closures).

### Outcome Classification

| Signal | Correct | Incorrect | Neutral |
|---|---|---|---|
| BUY | `future_price > signal_price` by > 0.2% | `future_price < signal_price` by > 0.2% | `|Δ| ≤ 0.2%` |
| SELL / FORCE_SELL | `future_price < signal_price` | `future_price > signal_price` | `Δ = 0` |
| HOLD | `|Δ| < 0.5%` | `|Δ| ≥ 0.5%` | — |
| Any | `NULL` when price data not yet available | | |

`NULL` outcomes (not the string `"pending"`) indicate that the horizon has not yet elapsed or price data was not found. COALESCE upsert ensures that once an outcome is resolved, it is never overwritten by a subsequent run.

### Accuracy Tracking

Accuracy is computed as `correct / (correct + incorrect + neutral) × 100`. NULL outcomes are excluded from the denominator.

Latency is tracked per horizon: `actual_scraped_at − target_ts` (seconds). This measures data freshness — how far after the target timestamp the actual scraped tick was found.

Per-indicator accuracy is computed by deserialising `signal_sources` JSON and grouping by normalised source name (rsi, sma, momentum, volume).

---

## 7. Current Limitations

### Missing prediction engine

`PredictionLog.outcome`, `outcome_price`, and `outcome_at` are never populated by any current process. The prediction logging pipeline (`prediction_engine.log_predictions`) inserts rows with `outcome="pending"` but no background job evaluates them. The `prediction_log` table accumulates rows indefinitely without resolution. This is separate from `signal_outcomes` (which IS evaluated by the signal evaluator).

### No real brokerage integration

All trade execution is simulated. There is no connection to a PSX broker API, NCCPL clearing, or any live order management system. The portfolio manager operates entirely within the application's SQLite database.

### Simplified execution model

- No slippage model for the paper portfolio (live trades execute at the exact price specified in the request body).
- No partial fills — trades succeed fully or fail entirely.
- Volume availability is not checked against the order size (you can "buy" more shares than the symbol's daily volume).
- CGT (Capital Gains Tax) is not computed. All P&L is pre-tax.
- No order types beyond market orders (no limit orders, stop orders, or trailing stops in the paper trading system — though the backtester implements a stop-loss rule).

### Data limitations

- The scraper fetches only symbols listed on `www.psx.com.pk/market-summary`. The number of tracked symbols depends on what PSX includes on that page.
- `high` and `low` fields are NULL for all rows inserted via the historical EOD download endpoint — that API does not provide intraday range data.
- `signal_changed` resets to `False` on every server restart. The first tick after restart always reports all signals as unchanged, even if the signal differs from the pre-restart state.
- Volume spike detection uses a hard-coded 1,000,000 baseline rather than a per-symbol rolling average, making it unreliable for thinly or heavily traded symbols.

### Architectural constraints

- **Single database:** SQLite is used for all persistence. Concurrent write throughput is limited by SQLite's WAL mode. The `NullPool` configuration for the async engine prevents pool exhaustion but means every async DB access opens a new connection.
- **In-memory signal state:** `app_state.stocks` and `app_state.signals` hold only the latest tick. Historical tick data requires DB queries. On a cold start (empty DB), the system serves HOLDs for all symbols until the price buffer accumulates sufficient history for RSI and SMA calculations.
- **Backtest results are ephemeral:** No DB persistence for backtest outputs. Lost on restart.
- **Walk-forward implementation caveat:** The train portion of each walk-forward window is passed through the full simulator, meaning train-period trades can affect cash and position state in the test portion. This creates train-contamination in the test metrics.
- **PSX scraping is fragile:** The scraper depends on the HTML structure of `www.psx.com.pk/market-summary`. Any PSX site redesign will break data ingestion until the parser is updated.

---

## 8. System Characteristics

### Deterministic vs Stochastic

**Deterministic:**
- Signal generation (given the same price buffer contents and strategy config).
- Backtester (given same DB price history and config).
- Fee calculation.
- Signal outcome evaluation.
- Forward trade TP/SL thresholds (given the same price buffer contents).

**Non-deterministic / Time-dependent:**
- Scraper output (depends on PSX data at scrape time).
- `signal_changed` flag after server restart (in-memory `_prev_signals` is lost).
- Flush timing (buffer age-based — depends on wall clock).
- Background task execution order (asyncio scheduling).

### Real-time vs Batch

**Real-time (every poll cycle, ~15s):**
- Price scraping and signal generation.
- WebSocket broadcast.
- Forward trade entry creation and exit checks.
- Price buffer update.

**Periodic batch:**
- Price and signal persistence (buffered: every 20 rows or 60 seconds).
- Portfolio snapshots (every 5 minutes).
- Signal evaluation / accuracy tracking (every 10 minutes).
- Prediction logging (fire-and-forget per tick, but DB write is batched per-tick).

**On-demand:**
- Backtesting (triggered by API call, runs synchronously in the request handler).
- Historical data download (background task, triggered by API call).
- Portfolio operations (trade execution, position queries).

### Scalability Considerations

- **Single instance only.** `app_state` is a module-level singleton. Running multiple instances would result in split state and conflicting DB writes.
- **SQLite ceiling.** Under concurrent async write load (many forward trades firing simultaneously), SQLite WAL with `busy_timeout=15000ms` prevents hard failures, but throughput is bounded. Migration to PostgreSQL (`DATABASE_URL=postgresql+asyncpg://...`) is supported at the configuration layer — the ORM is database-agnostic.
- **WebSocket fan-out.** `manager.broadcast()` sends JSON to all connected clients sequentially. At high client counts, this could delay the broadcast. No batching or backpressure mechanism is implemented.
- **Price buffer is unbounded per symbol at 200 entries.** At 15-second poll intervals, 200 ticks ≈ 50 minutes. Long gaps in data are handled by the 4-hour warm-up window on startup.
- **Backtest runs are synchronous.** A slow backtest (large symbol, wide date range) blocks the event loop. This is a known limitation — no background task or task queue is used for backtest execution.
