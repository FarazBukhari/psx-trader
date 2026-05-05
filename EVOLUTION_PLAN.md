# PSX Trader — Next Evolution Plan

---

## 1. Current System Assessment

### What is Strong

**Real-time pipeline architecture.** The poll → signal → enrich → persist → broadcast loop is well-structured. The async fire-and-forget pattern for DB writes keeps the critical path fast. The WebSocket fan-out is clean and low-latency for a single-instance system.

**Signal persistence discipline.** Storing every changed signal with its full indicator context (RSI, SMA, sources, action_score) in `signals_log` is the right foundation for any future ML training pipeline. The data is already labeled by signal type.

**Forward testing is the right instinct.** Tracking live trades from signals through to outcome (MFE/MAE, TP/SL, duration) and computing expectancy is exactly the feedback loop a serious system needs. Most amateur systems never close this loop.

**Fee realism.** The PSX-accurate three-component fee model (commission + CDC + SECP) is a detail most demo systems skip. Backtests and paper trades use the same function — no calibration gap.

**Stale data awareness.** The stale flag propagates end-to-end (scraper → signal → WS → UI). Most systems silently trade on stale data. This one refuses to execute on it.

**Signal evaluator time-horizon design.** The three-horizon outcome tracking (30min / 2h / session-based long) with COALESCE upsert for progressive filling is architecturally correct. The batch price-fetch using bisect reduces N+1 queries to O(symbols) — that is non-trivial engineering.

### What is Limiting

**The prediction engine is five arithmetic formulas dressed as ML.** OLS linear regression on 20 ticks, Bollinger position, RSI distance from 30/70, rolling S/R proximity — these are textbook indicator combinations that every retail trader already uses. The confidence formula is a weighted sum of heuristics. There is no learning, no generalization, no out-of-sample validation of the prediction model itself.

**Signal generation is purely rule-based with no memory.** Each tick is evaluated independently. The system cannot detect patterns that span multiple ticks (divergences, trend strength decay, volume climax), has no concept of regime (trending vs. ranging market), and cannot adapt its rules to recent market behavior.

**The evaluation loop is broken at its most critical joint.** `prediction_log.outcome` is never resolved. The prediction engine produces a `direction`, `confidence`, and `time_horizon` — but there is no process that ever checks whether those predictions were correct. The system accumulates prediction logs indefinitely without learning anything from them.

**Single data source, web-scraped.** The entire system's data originates from one HTML page (`psx.com.pk/market-summary`). A PSX redesign kills the system immediately. There is no intraday OHLCV (high/low are NULL for historical data). Volume data is per-day, not per-candle. There is no bid/ask spread, no order book depth, no tick-by-tick trade data.

**Backtester and live engine are divergent implementations.** `signal_engine.py` and `backtester.py` implement RSI, SMA crossover, and change_pct separately. A bug fix or threshold change in one is not automatically reflected in the other. The backtester also omits two of the five live strategies (`PriceThresholdStrategy`, `VolumeSpikeStrategy`), so backtest results do not accurately represent live signal behavior.

**SQLite as the only persistence layer.** Every component — price history, signals, portfolio, forward trades, outcomes — goes through a single SQLite file. There is no message queue, no time-series store, no cache layer. This is a hard ceiling on throughput, concurrent writes, and the ability to replay events.

### What is Missing Fundamentally

- **Any form of machine learning.** The system has never fit a model, cross-validated a hypothesis, or adjusted behavior based on observed outcomes.
- **Market regime awareness.** The same RSI thresholds fire in a bull run, a bear crash, and a sideways chop. The system treats all market conditions identically.
- **Multi-asset correlation.** Every symbol is evaluated in isolation. Sector rotation, index-level momentum, and cross-asset divergence signals are completely absent.
- **Position sizing intelligence.** The paper portfolio uses fixed position sizing. There is no Kelly criterion, no volatility-adjusted sizing, no drawdown-aware allocation.
- **A closed feedback loop.** There is no pathway from outcome data back to strategy parameters. The system cannot improve itself.
- **A defined risk model.** There is no portfolio-level VaR, no correlation-adjusted exposure limit, no maximum sector concentration constraint.

---

## 2. Core Gaps (Critical)

### Data

**Gap 1 — Single intraday source with no OHLCV integrity.** The scraper produces a close price every 15 seconds. There is no confirmed OHLC per candle — `high` and `low` in the price buffer represent daily extremes scraped from the market summary, not the high/low within each 15-second interval. Every indicator that relies on wicks (Bollinger squeeze, ATR, candlestick patterns) is operating on incorrect data.

**Gap 2 — No historical intraday dataset.** The EOD download provides daily closes with no intraday structure. The in-session price buffer (200 ticks × 15s ≈ 50 minutes) is the only intraday window available. Strategy research requiring weeks of intraday patterns cannot be done.

**Gap 3 — No volume microstructure.** Volume is reported once per day from the market summary scrape. There is no tick-level trade count, no bid/ask volume split, no VWAP anchor point. Volume-based signals (VWAP deviation, volume-weighted RSI, accumulation/distribution) are impossible.

**Gap 4 — Zero alternative data.** No news sentiment, no corporate event calendar (dividend dates, results announcements, AGMs), no PSX circular data, no Pakistan macro data (SBP rate decisions, CPI releases, PKR/USD moves). These are the events that move PSX stocks more than any technical indicator.

### Modeling

**Gap 5 — No feature engineering pipeline.** Raw indicator values (RSI=45, SMA_short=280) are persisted but never transformed into ML-ready features. There is no lag structure, no cross-sectional ranking, no normalization by sector or market cap, no interaction terms.

**Gap 6 — No trained model of any kind.** The confidence score (0.0–0.99) is not calibrated against actual outcomes. A 0.85 confidence prediction is not demonstrably more accurate than a 0.50 confidence prediction. The number is produced by a formula, not learned from data.

**Gap 7 — No regime detection.** A 14-period RSI performs differently in a trending market than in a mean-reverting one. The system applies identical thresholds across all regimes. It has no mechanism to detect when its own assumptions are violated.

### Execution

**Gap 8 — Live paper trades execute at requested price with no market impact model.** The user specifies a price in the API request body. There is no check against the last known market price. A user could submit a buy at PKR 1 for a PKR 300 stock and the system accepts it.

**Gap 9 — No slippage model in live paper trading.** The backtester applies 0.1% slippage. The live paper portfolio does not. This creates a performance discrepancy — backtests will always look better than live paper results.

**Gap 10 — No order queue or pending order support.** All trades are market orders executed immediately. There are no limit orders, no conditional orders, no bracket orders. A trader cannot say "buy ENGRO if it drops below PKR 270."

### Evaluation

**Gap 11 — The prediction engine is unvalidated.** There is no process that checks whether `prediction.direction` correlated with subsequent price movement. The model produces outputs that are never scored.

**Gap 12 — Signal → prediction → trade → outcome is not a unified pipeline.** These four stages exist as separate tables (`signals_log`, `prediction_log`, `forward_trades`, `signal_outcomes`) with no foreign key links between them. Attribution analysis — "which combination of indicators, at what confidence, under what market conditions, produced profitable outcomes" — is impossible from the current schema.

**Gap 13 — No confidence calibration.** The confidence output is not a probability. There is no reliability diagram, no Brier score, no calibration curve. A well-calibrated confidence of 0.70 should mean the prediction is correct 70% of the time.

### Scalability

**Gap 14 — Monolithic process.** The scraper, signal engine, prediction engine, forward tracker, signal evaluator, portfolio manager, and WebSocket server all run inside a single Python process. A crash in any component takes down all of them. There is no independent scaling of high-throughput components.

**Gap 15 — No event bus.** The system uses `asyncio.create_task()` as its messaging primitive. There is no durable message queue — if the server restarts mid-tick, in-flight tasks are lost. There is no replay capability.

---

## 3. Phase-Based Evolution Plan

### Phase A — Intelligence Layer

**Objective:** Replace heuristic prediction with a trained, validated, continuously updating ML model.

#### A1 — Feature Engineering Pipeline

Build a `FeatureEngine` class that transforms raw price buffers into model-ready features. This should be computed offline (not in the poll loop) on the full historical dataset.

**Feature categories:**

- **Price-based:** Returns at multiple horizons (1-tick, 5-tick, 20-tick, 100-tick), log-return volatility (realized vol over 20/60/200 ticks), drawdown from rolling peak, distance from 52-week high/low (using EOD data).
- **Indicator-derived:** RSI at multiple periods (7, 14, 21), normalized by sector average RSI. SMA ratio (price / SMA_long, price / SMA_short). Bollinger %B and bandwidth. ATR (requires fixing the OHLC data problem first — see Phase E).
- **Cross-sectional (sector-relative):** Symbol's 5-day return minus sector median 5-day return. Symbol's RSI rank within sector (percentile 0–1). Volume rank within sector.
- **Temporal:** Hour of day (PKT), day of week, days since last BUY/SELL signal, days since last earnings release (requires corporate event data).
- **Signal history:** Last 3 signal types as one-hot encoded features, time since last signal change, action_score trend (rising vs. falling).
- **Outcome feedback:** Rolling 30-day win rate for each signal type for this symbol (derived from `signal_outcomes`). This is the key loop-closing feature — the model learns from its own history.

All features must be stored in a `feature_store` table or a columnar flat-file (Parquet recommended) to avoid recomputing on every training run.

#### A2 — Target Definition

Three separate binary classification targets (one per horizon), matching the existing evaluation horizons:

- `target_short`: 1 if `outcome_short = "correct"` given the signal direction, else 0.
- `target_medium`: 1 if `outcome_medium = "correct"`, else 0.
- `target_long`: 1 if `outcome_long = "correct"`, else 0.

Only rows where the outcome is not NULL are used for supervised training. This is a natural consequence of the progressive-fill design in `signal_outcomes`.

Avoid the "peeking" trap: features must be computed using only data available at `signal.generated_at`. No future price data in features.

#### A3 — Model Selection

Start with **gradient boosted trees** (XGBoost or LightGBM) for two reasons: they handle tabular financial data well, they produce well-calibrated probabilities with Platt scaling, and they are interpretable via SHAP values. Do not start with neural networks — the dataset is too small and tabular financial data rarely benefits from deep learning at this scale.

**Training protocol:**
- Time-series cross-validation only. No random train/test split — this would cause look-ahead leakage.
- Walk-forward expanding window: train on months 1–6, test on month 7. Train on months 1–7, test on month 8. Etc.
- Minimum training set: 2,000 labeled samples per target. Until this threshold is met, the heuristic prediction engine remains active.
- Feature importance tracked per training run — automatically surfaces which indicators are predictive.

**Calibration:** Apply isotonic regression or Platt scaling to convert raw model scores to calibrated probabilities. After calibration, a 0.70 confidence should mean ~70% empirical accuracy. Track calibration drift weekly — if the model's calibration degrades, trigger retraining.

#### A4 — Model Evaluation Framework

Build `ModelEvaluator` with the following metrics computed at every training run:

- **Calibration:** Brier score, reliability diagram (10 probability bins).
- **Discrimination:** ROC-AUC, precision-recall AUC (class imbalance is expected — HOLD dominates).
- **Financial relevance:** Precision at top-K confidence decile (the signals that matter are the highest-confidence ones, not average performance). Expected return per predicted-correct trade.
- **Temporal stability:** Per-month accuracy breakdown — catches regime shifts where the model degrades.
- **Feature stability:** SHAP value distribution over time — detects when important features stop being predictive.

Store all evaluation artifacts in a `model_registry` table: model_id, trained_at, train_window, all metrics, feature_importances (JSON), serialized model path.

#### A5 — Online Update vs Full Retrain

Distinguish two learning cadences:

- **Weekly full retrain:** Re-fit the model on all available labeled data. Replaces the active model if evaluation metrics meet minimum thresholds (Brier score < 0.25, AUC > 0.55 for at least 2 of 3 horizons). Otherwise, keep the previous model and log a degradation warning.
- **Daily calibration update:** Refit only the calibration layer (isotonic regression) using the most recent 2 weeks of outcomes. This keeps the probability estimates fresh without full model instability.

---

### Phase B — Strategy Layer

**Objective:** Move from 5 hard-coded indicator strategies to a composable, backtestable, selectable strategy system.

#### B1 — Strategy Abstraction

Define a formal `Strategy` interface:

```python
class Strategy(ABC):
    name: str
    version: str
    parameters: dict          # typed, validated, searchable

    @abstractmethod
    def generate_signal(self, features: FeatureRow) -> SignalOutput:
        """Returns signal + confidence + sources. No side effects."""

    @abstractmethod
    def describe(self) -> str:
        """Human-readable description of the strategy's logic."""
```

Every strategy — whether rule-based, ML-driven, or RL-agent — implements this interface. The signal engine becomes a generic orchestrator that calls registered strategies, collects outputs, and resolves them.

This also closes the current backtester/live-engine divergence: the backtester should call the same `Strategy.generate_signal()` method the live engine calls, not a separate reimplemented copy.

#### B2 — Multi-Strategy Registry

Maintain a versioned registry of strategies with metadata:

- `strategy_id`, `name`, `version`, `created_at`, `parameter_hash`
- `backtest_summary`: aggregate backtest metrics across all symbols
- `live_performance_summary`: forward-test expectancy from `forward_trades`
- `status`: `active | shadow | archived`

**Shadow mode:** A new strategy can run in shadow mode — it generates signals and logs them but does not influence the live broadcast or paper portfolio. Shadow-mode signals are evaluated by the signal evaluator and forward tracker exactly like active signals. Graduation from shadow to active requires meeting performance thresholds over a minimum observation period (e.g., 30 trading days, Sharpe > 0.5, expectancy > 0.3%).

#### B3 — Ensemble Signal Resolution

Replace the current `FORCE_SELL > SELL > BUY > HOLD` priority rule with a weighted ensemble:

```
ensemble_signal = argmax(Σ strategy_weight_i × signal_confidence_i)
ensemble_confidence = Σ weight_i × confidence_i  (for the winning direction)
agreement_ratio = (winning_votes / total_votes)
```

Strategy weights are derived from recent out-of-sample performance (e.g., rolling 60-day Sharpe of that strategy's forward trades). A strategy that has been consistently wrong in recent weeks gets low weight automatically. A newly shadow-promoted strategy starts with a small weight that grows as it accumulates live performance data.

This creates a system that is simultaneously conservative (consensus required for high-conviction signals) and adaptive (weights shift as performance changes).

#### B4 — Regime-Conditional Strategy Selection

Add a `RegimeClassifier` (see Phase D / Section 4) that tags each trading session with a market regime: `trending_up`, `trending_down`, `ranging`, `volatile`, `low_liquidity`. Each strategy registers preferred regimes:

```python
class RSIMeanReversionStrategy(Strategy):
    preferred_regimes = ["ranging", "low_volatility"]
    avoid_regimes     = ["trending_up", "trending_down"]
```

The ensemble weight for a strategy is multiplied by a regime suitability score. In a trending market, momentum strategies dominate. In a ranging market, mean-reversion strategies dominate. This replaces the current fixed preset system (conservative/aggressive/momentum) with dynamic, evidence-driven strategy weighting.

---

### Phase C — Execution Layer

**Objective:** Make the paper trading simulation realistic enough that live performance is predictable from paper performance.

#### C1 — Market-Price Anchored Execution

All trade requests must validate the submitted price against the last known market price:

```
if |submitted_price − last_known_price| / last_known_price > 0.02:
    raise PriceDeviationError("Submitted price deviates >2% from market")
```

For sell orders, additionally check: `submitted_price ≤ last_known_price × 1.01` (you can't sell above market).

#### C2 — Realistic Slippage Model

Move from constant 0.1% slippage in the backtester to a dynamic model for both backtesting and paper trading:

```
slippage_pct = base_spread + market_impact_component

base_spread:            0.10% (default PSX discount broker spread)
market_impact_component: f(order_size / avg_daily_volume)
    = 0.0  if order_size < 1% of ADV   (negligible impact)
    = 0.05% per 1% of ADV above that   (linear market impact)
    capped at 0.50%                    (thin stocks)
```

ADV (average daily volume) is computed from the rolling 20-day volume in `price_history`. This means large orders on thin symbols incur realistic costs — a critical constraint for PSX mid-caps.

The same slippage function is shared between the backtester, paper trading, and the forward tracker's entry/exit price calculation. No more three-way divergence.

#### C3 — Order Types

Add limit and stop-limit orders to the paper portfolio:

- **Limit buy:** Execute only if `market_price ≤ limit_price` in a subsequent poll tick.
- **Limit sell:** Execute only if `market_price ≥ limit_price`.
- **Stop-limit:** Becomes a limit order when the stop is triggered.
- **Good-till-cancelled (GTC):** Order persists across sessions until filled or manually cancelled.

Pending orders are stored in an `orders` table with `status=PENDING`. Each poll tick, the `OrderManager` checks all pending orders against current prices and executes eligible ones. This requires a new background task but no architectural changes.

#### C4 — Portfolio-Level Risk Controls

Add hard limits enforced at the PortfolioManager level:

- **Max position size:** No single position can exceed X% of portfolio value (configurable, default 20%).
- **Max sector exposure:** Sum of all positions in one sector cannot exceed Y% of portfolio (configurable, default 40%).
- **Max drawdown circuit breaker:** If portfolio total value drops > Z% from peak (configurable, default 15%), all new buys are blocked until manual reset. Existing positions remain.
- **Minimum cash reserve:** Portfolio must maintain at least W% cash at all times (configurable, default 10%).

These constraints are checked at `execute_buy()` time and return a new `RiskConstraintViolationError` with a structured message identifying which limit was breached.

---

### Phase D — Evaluation Layer

**Objective:** Build a single unified view of the system's performance from signal generation through trade outcome, with attribution and calibration.

#### D1 — Unified Event Schema

Replace the four disconnected tables (`signals_log`, `prediction_log`, `forward_trades`, `signal_outcomes`) with a unified `signal_events` table that links every stage by a shared `event_id`:

```sql
CREATE TABLE signal_events (
    event_id        TEXT PRIMARY KEY,      -- UUID generated at signal time
    symbol          TEXT,
    generated_at    INTEGER,               -- signal timestamp

    -- Signal layer
    signal          TEXT,
    signal_sources  TEXT,                  -- JSON
    action_score    REAL,
    horizon_mode    TEXT,

    -- Prediction layer (filled at signal time)
    pred_direction  TEXT,
    pred_confidence REAL,
    pred_hold_days  INTEGER,
    pred_trade_action TEXT,
    pred_expected_move_pct REAL,
    pred_reward_risk_ratio REAL,

    -- Forward trade layer (filled when trade opens, if applicable)
    ft_entry_price  REAL,
    ft_entry_time   INTEGER,
    ft_exit_price   REAL,
    ft_exit_time    INTEGER,
    ft_status       TEXT,
    ft_outcome      TEXT,
    ft_mfe_pct      REAL,
    ft_mae_pct      REAL,
    ft_duration_min REAL,

    -- Outcome evaluation layer (filled progressively)
    price_at_signal REAL,
    price_short     REAL,
    price_medium    REAL,
    price_long      REAL,
    outcome_short   TEXT,
    outcome_medium  TEXT,
    outcome_long    TEXT,

    -- Regime context (filled at signal time from RegimeClassifier)
    market_regime   TEXT,
    sector_momentum REAL,

    -- Model attribution (filled at prediction time if ML model active)
    model_id        TEXT,
    top_feature     TEXT,
    shap_values     TEXT                   -- JSON
);
```

This schema makes the following queries trivially answerable:
- "What is the forward-test win rate of signals with `pred_confidence > 0.60` in a `ranging` regime?"
- "Does the RSI indicator contribute positively to outcome_medium accuracy?"
- "Which model_id version has the best calibrated expectancy on SELL signals?"

#### D2 — Attribution Analysis

Build `AttributionAnalyzer` that answers three questions on demand:

**Indicator attribution:** For each signal source (rsi, sma_crossover, change_pct, etc.), compute `accuracy_lift = accuracy_when_source_present − overall_accuracy`. Positive lift means this indicator adds predictive value. Negative lift means it adds noise.

**Confidence attribution:** Bin predictions by decile (0–10%, 10–20%, ..., 90–100%). Plot actual accuracy per bin. This is the calibration curve. A well-calibrated model produces a diagonal line. The current system's heuristic confidence will not — but this will be visible and measurable.

**Regime attribution:** For each market regime, compute separate accuracy and expectancy metrics. This directly drives the regime-conditional strategy weighting in Phase B4.

#### D3 — Confidence Calibration Feedback Loop

After each weekly model retrain (Phase A5), run calibration validation:

1. Compute the reliability diagram for the held-out test period.
2. If the max calibration error (MCE) > 5%, flag the model as "poorly calibrated."
3. Apply Platt scaling to adjust raw model scores to match empirical frequencies.
4. Log `calibration_correction_factor` per decile in the model registry.

The frontend should display a "Calibration Quality" indicator that shows whether the confidence numbers are trustworthy. If MCE > 10%, show a warning: "Confidence scores are uncalibrated — treat as directional indicators only."

---

### Phase E — Data Layer

**Objective:** Build a data infrastructure that is independent of the PSX HTML scraper and rich enough to support ML training.

#### E1 — Multi-Source Data Ingestion

Replace the single-scraper architecture with a `DataIngestionService` that manages multiple sources with independent health monitoring:

**Source 1 — PSX Market Summary (existing):** Current 15-second HTML scraper. Treat this as an "intraday snapshot" source. Add circuit breaker: if 3 consecutive scrapes fail, log an alert and switch to snapshot mode immediately (already implemented) but also attempt a secondary source.

**Source 2 — PSX Data Portal EOD (existing, but underused):** The `dps.psx.com.pk/timeseries/eod/{symbol}` endpoint is already integrated for historical download. Add scheduled nightly sync (after market close) to keep the EOD history current without manual triggering.

**Source 3 — PSX Announcements Feed:** PSX publishes corporate announcements (financial results, dividend declarations, AGMs, bonus issues) in a structured format. Build a scraper for `psx.com.pk/announcements`. Store in an `events` table. Corporate events are among the most powerful predictive signals for individual stock movement on PSX.

**Source 4 — SBP / REER / Macro Data (Pakistan):** State Bank of Pakistan publishes weekly T-bill rates, monthly CPI, FX reserves. These drive sector-level moves (banking stocks respond to rate changes; oil & gas stocks respond to PKR/USD). Even one macro feature (SBP rate change flag) would add signal for sector-level predictions.

**Source 5 — Alternative intraday OHLCV:** If a reliable PSX intraday data vendor becomes available (Bloom, investing.com scrape, or direct PSX data subscription), plug it into the ingestion service. This unlocks proper ATR, candlestick patterns, and VWAP calculations.

#### E2 — Intraday vs EOD Separation

The current `price_history` table conflates two conceptually different things: intraday poll snapshots (15-second close prices during session) and EOD daily closes (source="historical"). These should be separate tables:

```
intraday_ticks:     15-second poll snapshots — used for live signal generation
eod_prices:         daily OHLCV — used for overnight analytics, ML training, backtesting
```

The backtester should run on `eod_prices` for date-range studies and on `intraday_ticks` for intraday strategy research. Mixing the two is the source of the current confusion where 200 ticks might represent 50 minutes or 8 months depending on data source.

#### E3 — Feature Store

Build a columnar feature store (Parquet files on disk, or DuckDB for queryability) that stores pre-computed ML features at the EOD grain. Schema: `(symbol, date, feature_1, ..., feature_N, target_short, target_medium, target_long)`.

This decouples feature computation (slow, runs nightly) from model training (fast, reads from feature store) and inference (faster, computes only the features needed for the current tick's prediction).

#### E4 — Data Quality Monitoring

Add a `DataQualityMonitor` that runs after each scrape and flags anomalies:

- **Price jump detection:** If `|change_pct| > 7.5%`, flag as potential data error (PSX has circuit breakers at this level — legitimate moves are possible but rare).
- **Volume anomaly:** If volume = 0 for a symbol that was active yesterday, flag as possible scraper gap.
- **Missing symbols:** If a symbol that was in the last tick is absent from the current tick, log as "dropped symbol" — may indicate delisteing or scraper gap.
- **Timestamp gap detection:** If `scraped_at − previous_scraped_at > 2 × POLL_INTERVAL`, log the gap as a data hole. Flag all signals generated after a gap as "gap-adjacent" — indicators spanning the gap are unreliable.

Data quality flags are stored in a `data_quality_log` table and surfaced in the system status API.

---

### Phase F — UX / Visualization

**Objective:** Shift from "data display" to "decision support." Every screen should answer a specific question a trader would actually ask.

#### F1 — Decision Dashboard Redesign

The current Dashboard shows all signals in a table sorted by action_score. Replace this with a **three-panel decision interface:**

**Panel 1 — The Shortlist:** Top 5–8 high-conviction signals right now, displayed as cards. Each card shows: symbol, signal direction, ML confidence (calibrated), regime suitability, time horizon, expected move, reward/risk. Cards are color-coded by risk level. One-click to pre-fill the Trade Panel.

**Panel 2 — The Evidence:** Expandable per-symbol detail — price chart with indicator overlays, signal history timeline (when did this symbol last give this signal? what happened?), attribution breakdown (which features drove this prediction?), SHAP waterfall chart showing the top 5 contributing factors.

**Panel 3 — The Portfolio Context:** Before acting on a signal, show: current exposure to this sector, current cash available, how this trade would affect max drawdown buffer, whether risk controls would allow it. This is the "pre-trade checklist" — currently absent.

#### F2 — Signal Performance Tracker

A dedicated screen (replacing the current performance page structure) that shows:

- **Calibration curve** for the active ML model: expected vs. actual accuracy by confidence decile.
- **Rolling win rate by signal type** over the last 30/60/90 trading days — from `signal_outcomes`.
- **Regime performance heatmap:** Rows = regimes, columns = signal types, cells = accuracy. Immediately shows which signals work in which conditions.
- **Attribution breakdown:** Bar chart of indicator lift scores. Shows which indicators are currently adding value and which are adding noise.
- **Expectancy trend:** Rolling 30-day expectancy for forward trades — is the system getting better or worse over time?

#### F3 — Backtesting UX Overhaul

Replace the current single-config form with a proper research environment:

- **Parameter sweep visualization:** Run backtest with a grid of RSI thresholds and display a heatmap of returns. Identify the stable region (not just the peak) to avoid curve-fitting.
- **Drawdown decomposition:** Show max drawdown timeline annotated with the specific trades that caused it.
- **Regime overlay:** Color the equity curve by market regime — see where the strategy profits and where it bleeds.
- **Benchmark comparison:** Plot strategy equity curve against a buy-and-hold KSE-100 equivalent. If the strategy underperforms buy-and-hold, it is not adding value regardless of absolute returns.
- **Out-of-sample warning:** Visually distinguish in-sample from out-of-sample periods on the equity chart. Gray out in-sample results. Only the out-of-sample curve is the honest signal.

#### F4 — Trader Workflow Integration

Add a **Trade Journal** screen: every paper trade shows its pre-trade signal context (what was the signal, what was the confidence, what was the regime) alongside the actual outcome (P&L, duration, MFE/MAE). Traders can add notes. The journal is the human-in-the-loop feedback mechanism — patterns in "I sold early" or "I held too long" surface from the annotated data.

---

## 4. Advanced Features

### Self-Learning Strategy Parameters

Replace static preset parameters (RSI=30/70, SMA=5/20) with parameters that adapt weekly based on recent performance. Implement a `ParameterOptimizer` that:

1. At the end of each week, takes the last 90 days of `signal_outcomes` for each symbol.
2. Runs a lightweight Bayesian optimization (not a full grid search) over the parameter space: `rsi_oversold ∈ [20, 40]`, `rsi_overbought ∈ [60, 80]`, `sma_short ∈ [3, 15]`, `sma_long ∈ [10, 50]`.
3. Objective function: expectancy of forward trades generated by those parameters.
4. Updates parameters only if the improvement is statistically significant (bootstrap test, p < 0.05) and robust across the previous 3 monthly windows (not a single fluke).

This is not full RL but it is a first step toward parameter-adaptive strategies. The key constraint: parameter changes must pass an out-of-sample validation before going live.

### Reinforcement Learning Agent (Long-term)

Replace the rule-based signal generation entirely with a Deep Q-Network or Proximal Policy Optimization agent trained to maximize risk-adjusted returns in a simulated PSX environment. The agent's action space: `{BUY, SELL, HOLD}` per symbol per tick. State space: the full feature vector from Phase A1, including portfolio state (current cash ratio, open positions, unrealized P&L). Reward: per-tick P&L adjusted by a risk penalty (Sharpe-based).

The critical engineering requirement: a fast, stateful simulation environment that can run thousands of episodes per training iteration. This likely requires extracting the backtester into a standalone C-extension or using an existing RL trading environment library.

**Do not attempt this before Phase A–D are complete.** RL requires a stable reward signal, a large dataset, and a well-validated environment. Building RL on top of a broken evaluation loop is how you produce a confidently wrong agent.

### Auto Capital Allocation (Kelly-Based)

Implement fractional Kelly position sizing:

```
kelly_f = (p × b − q) / b
    where p = predicted win probability (calibrated confidence)
          q = 1 − p
          b = avg_win_pct / avg_loss_pct  (reward-to-risk ratio from recent outcomes)

fractional_kelly = kelly_f × 0.25   (quarter-Kelly — standard risk reduction)
position_size = portfolio_value × fractional_kelly
```

This produces position sizes that grow when the model is confident and the reward/risk is favorable, and shrink when uncertainty is high. The 0.25 multiplier guards against the Kelly criterion's known tendency to produce ruin under model misspecification.

Requires: calibrated confidence from Phase A3, validated reward/risk ratios from Phase D forward-trade data, portfolio-level risk controls from Phase C4.

### Anomaly Detection

Add an `AnomalyDetector` that runs on every tick and flags unusual patterns before signal generation:

- **Price anomaly:** Symbol moved > 2σ beyond its 60-day volatility-normalized distribution. Could be a real breakout or a data error — flag for human review before acting.
- **Volume anomaly:** Volume > 3× 20-day average with no corresponding PSX announcement. Could indicate informed trading ahead of an announcement.
- **Indicator divergence:** Price making a new high but RSI making a lower high (RSI bearish divergence). Price making a new low but RSI making a higher low (RSI bullish divergence). These divergences are among the most reliable reversal signals but are not detectable by single-tick evaluation.
- **Correlation anomaly:** A stock is moving strongly against its sector. Either it has stock-specific news, or the move is noise and will revert.

Anomaly flags are attached to signals as metadata and displayed in the UI. Traders can filter for anomaly-flagged signals specifically.

### Market Regime Detection

Implement a `RegimeClassifier` that runs daily (not per-tick) and tags the current market environment. Two complementary approaches:

**Approach 1 — Index-based (immediate, no ML required):** Compute KSE-100 (derived from the tracked symbols as a proxy index): 20-day return > 5% = `trending_up`; < -5% = `trending_down`; realized vol > 2% daily = `volatile`; otherwise = `ranging`. Simple, explainable, and directly useful.

**Approach 2 — Hidden Markov Model (after Phase A data is available):** Fit a 4-state HMM on the KSE-100 return and volatility series. States emerge from data rather than being manually thresholded. The HMM posterior probability over states replaces the binary rule above. This is the correct long-term approach.

The current regime is stored in `app_state` and broadcast on every WS update. All strategy weights (Phase B4), signal interpretations, and position size recommendations are conditioned on it.

### Meta-Strategy Selector

Build a portfolio of strategies (from the Phase B registry) and a meta-learner that selects or weights them based on current conditions. The meta-learner's inputs: current regime, recent strategy performance, market volatility level, sector momentum dispersion. Its output: a weight vector over active strategies.

This is the system-level version of ensemble learning. Individual strategies are the base learners. The meta-strategy is the stacker. The meta-learner is retrained weekly alongside the base prediction model.

---

## 5. Architecture Upgrades

### From Monolith to Modular Services

The current system is a single FastAPI process. The target architecture separates responsibilities into independently deployable, independently scalable components:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Event Bus (Redis Streams)                        │
│  streams: tick_raw · signal_generated · trade_opened · outcome_resolved  │
└─────────────────────┬───────────────────────────────────────────────────┘
                       │
      ┌────────────────┼────────────────────────────────┐
      ▼                ▼                                 ▼
┌──────────┐   ┌──────────────┐   ┌──────────────────────────────────────┐
│  Ingest  │   │ Signal+Pred  │   │  API Gateway (FastAPI)               │
│  Service │   │  Service     │   │  REST + WebSocket                     │
│  (scraper│   │ (engine +    │   │  Reads from read-replica DB          │
│   +      │   │  ML model)   │   │  Broadcasts from event bus           │
│  EOD     │   │              │   └──────────────────────────────────────┘
│  sync)   │   └──────────────┘
└──────────┘           │
                       ▼
            ┌──────────────────┐   ┌──────────────────┐
            │  Forward Tracker │   │ Signal Evaluator  │
            │  Service         │   │ Service           │
            │ (trade lifecycle)│   │ (outcome filling) │
            └──────────────────┘   └──────────────────┘
                       │
                       ▼
            ┌──────────────────┐   ┌──────────────────┐
            │   Portfolio      │   │  ML Training      │
            │   Service        │   │  Service          │
            │ (paper trading)  │   │ (offline, weekly) │
            └──────────────────┘   └──────────────────┘
                       │
                       ▼
            ┌──────────────────────────────────────────┐
            │         TimescaleDB (primary store)       │
            │  time-series optimized, hypertables for   │
            │  price_history, signal_events, outcomes   │
            └──────────────────────────────────────────┘
```

Each service communicates via the event bus, not direct function calls. This enables independent deployment, independent scaling, and replay capability (if the signal service crashes mid-tick, the raw tick is still in the bus and can be reprocessed).

### Event-Driven Core

Replace the poll loop's direct function call chain:

```
current:  scrape() → process() → enrich() → create_task(save_tick) → create_task(forward_trade) → broadcast()

target:   scrape()
            → publish("tick_raw", tick_data)
              → SignalService consumes, publishes "signal_generated"
                → PredictionService consumes, publishes "signal_enriched"
                  → DBWriter consumes, writes to DB
                  → ForwardTracker consumes, opens/closes trades, publishes "trade_event"
                  → APIGateway consumes, broadcasts to WS clients
```

The event bus is the single source of truth for ordering. Dead-letter queuing handles failed consumers without data loss. New services can be added (e.g., a notification service, an alerting service) by subscribing to existing streams — no changes to producers.

### Database Migration

Move from SQLite to **TimescaleDB** (PostgreSQL extension optimized for time-series data):

- `price_history` → TimescaleDB hypertable partitioned by `scraped_at`. Automatic compression for data older than 7 days. Continuous aggregates for OHLCV candles at 1min/5min/15min/60min resolution.
- `signal_events` → Hypertable partitioned by `generated_at`.
- Portfolio tables (portfolio, positions, trades) → standard PostgreSQL tables (low write volume, relational integrity more important than throughput).
- Feature store → DuckDB (analytical queries on Parquet files; no OLTP needed).
- Model registry → PostgreSQL (low volume, structured metadata).

This migration unblocks: multi-instance deployment (shared DB), significantly higher write throughput, SQL-based candle aggregation (eliminates the need for a separate OHLCV pipeline), and native time-series functions.

### Async Pipeline Improvements

Even before the full service decomposition, several immediate improvements apply within the monolith:

- Replace `asyncio.create_task()` for DB writes with a proper async worker pool pattern: a fixed-size pool of DB workers reads from an in-process async queue. This bounds memory usage and provides backpressure.
- Add per-component health checks with structured logging: each background task reports its last successful run timestamp. If a task hasn't run within 2× its interval, mark it unhealthy and surface this in `/api/system/status`.
- Implement graceful degradation: if the ML prediction service is unavailable (model loading failure, timeout), fall back to the heuristic prediction engine automatically. The API response includes a `prediction_source: "ml" | "heuristic" | "unavailable"` field.

---

## 6. Production Readiness

### Reliability

**Structured error handling:** Every background task should distinguish recoverable errors (transient DB write failure, scraper HTTP 429) from fatal errors (schema migration required, model file corrupted). Recoverable errors trigger exponential backoff retry. Fatal errors trigger a controlled shutdown with an alert.

**Circuit breakers per external dependency:** The PSX scraper, each external data source, and the DB write path should each have a circuit breaker. If the PSX scraper fails > 3 consecutive times, open the circuit (switch to snapshot mode immediately, stop attempting). After 5 minutes, half-open the circuit (attempt one probe request). This prevents thundering-herd recovery after a PSX outage.

**Data retention policy:** Define and enforce retention policies per table:
- `intraday_ticks`: retain 30 days (older data compresses to EOD)
- `signal_events`: retain forever (this is the training dataset)
- `signal_outcomes`: retain forever
- `forward_trades`: retain forever
- `prediction_log`: retain 90 days (it's currently unresolved anyway — trim it)
- `portfolio_snapshots`: retain 1 year rolling

### Observability

**Structured logging with correlation IDs.** Every poll tick gets a `tick_id` UUID. All log messages, DB writes, and WS broadcasts for that tick carry the same `tick_id`. This makes it possible to trace the full journey of a single tick through the system from scrape to broadcast in a log aggregator.

**Metrics pipeline.** Export the following as Prometheus metrics (scraped every 15 seconds):
- `psx_poll_duration_seconds` (histogram)
- `psx_signal_generation_duration_seconds`
- `psx_db_flush_duration_seconds` and `psx_db_flush_batch_size`
- `psx_ws_connected_clients`
- `psx_forward_trades_open_count`
- `psx_prediction_confidence_p50` / `p95` (gauge, per tick)
- `psx_signal_evaluator_last_run_seconds` (age of last run)
- `psx_model_calibration_mce` (max calibration error, updated weekly)

**Alerting rules (Alertmanager or equivalent):**
- `psx_poll_duration_seconds p99 > 10s` → poll loop is stalling
- `psx_signal_evaluator_last_run_seconds > 1800` → evaluator is not running
- `psx_prediction_confidence_p50 < 0.10` → model confidence has collapsed (possible feature drift)
- DB disk usage > 80% → approaching storage limit

**Distributed tracing** (Jaeger or OpenTelemetry): Trace the full request path for each API call, including DB query time, external HTTP calls, and WS broadcast time. This is essential for diagnosing latency spikes that affect trading responsiveness.

### Testing Strategy

**Unit tests (fast, no DB):**
- `SignalEngine.process()` with mocked `PriceBuffer` — deterministic signal output for a given price sequence.
- `calculate_fee()` — exact PKR values for known trade sizes.
- `_classify()` in signal_evaluator — all outcome combinations.
- `_calc_tp_sl()` — volatility-threshold boundary cases.
- `_generate_signal()` in backtester — every signal type with minimal price sequences.
- `_combine_votes()` in PredictionEngine — edge cases (all same direction, perfect split, single vote).

**Integration tests (with test DB):**
- Full poll cycle: mock scraper output → full pipeline → assert signal in `app_state`, tick in `signals_log`, forward trade created.
- PortfolioManager: buy → position created → sell → position closed → realized P&L correct.
- Signal evaluator: insert signal with known timestamps, insert price history at known horizons, run evaluator, assert correct outcome classification.
- Backtester walk-forward: deterministic price sequence → assert metrics match hand-calculated values.

**Backtest regression tests:** Save a snapshot of backtester output (trade log + metrics) for a fixed symbol, date range, and config. Run this snapshot test on every code change. If the output changes, it is either a bug or an intentional modification — either way it must be reviewed. This prevents the backtester/live divergence problem from silently getting worse.

**Data pipeline tests:**
- Scraper parser: saved PSX HTML fixture → assert correct symbol count, fields populated, no NaNs in close price.
- EOD ingestion: mock PSX data portal response → assert correct row insertion, `ldcp` and `change_pct` back-fill.
- Feature engineering: known price sequence → assert RSI/SMA values match reference implementation.

**Shadow strategy validation:** Before any strategy change goes to production, it must run in shadow mode for a minimum observation period and pass these gates: (1) forward-test expectancy > 0, (2) accuracy on `signal_outcomes` ≥ existing strategy accuracy, (3) no statistically significant degradation on any individual signal type (Fisher's exact test, p > 0.05).

### Deployment Model

**Near-term (single server, improved):**
- Docker Compose with separate containers: `backend`, `db` (PostgreSQL/TimescaleDB), `redis` (event bus), `ml-worker` (weekly training job as a cron container).
- Reverse proxy (Caddy or nginx) with automatic HTTPS.
- Persistent volumes for DB data, model artifacts, Parquet feature files.
- `docker compose watch` for hot-reload in development, `docker compose up -d` with `--no-recreate` for zero-downtime config updates.
- Health check endpoints consumed by Docker's built-in health check — if the backend container fails its health check, the reverse proxy stops routing to it immediately.

**Medium-term (multi-instance):**
- Move to Kubernetes (or a managed equivalent). Backend API is stateless after moving to TimescaleDB — horizontal scaling is immediate.
- Signal/prediction service runs as a single-replica deployment (maintains `_prev_signals` state) but with a persistent volume for state snapshots. On restart, state is recovered from the snapshot within 1 tick.
- ML training runs as a Kubernetes CronJob — one run per week, resource-isolated from the real-time path.

---

## 7. Prioritized Roadmap

### Short Term (1–2 Weeks)

**Week 1 — Fix the broken feedback loop:**
1. Build the `prediction_log` outcome resolution job — the same pattern as the signal evaluator but for prediction_log rows. Joins `prediction_log` to `price_history` at `predicted_at + time_horizon_days × 86400`. Classifies `outcome` as `correct` / `incorrect` based on `predicted_direction` vs. actual price movement. This is 1–2 days of work and immediately produces the data needed for model training.
2. Unify signal_changed behavior across restarts: on startup, populate `_prev_signals` from the most recent row per symbol in `signals_log`. A 5-line change that fixes a persistent accuracy gap in the data.
3. Fix volume spike strategy: compute rolling 20-day average volume from `price_history` per symbol rather than using the 1M hard-coded baseline. Requires one DB query at startup to initialize per-symbol volume baselines.

**Week 2 — Data integrity and evaluation hygiene:**
1. Add the data quality monitor: price jump detection, volume anomaly, missing symbol alerts. Log to `data_quality_log`. Surface in `/api/system/status`.
2. Close the backtester/live divergence: make both use the same `generate_signal()` function. Requires extracting signal logic into a shared module with no runtime dependencies (no DB, no `app_state`, no `price_buffer` singleton). This also makes signal logic unit-testable.
3. Add `/api/system/status` → `signals.stale_note` to the frontend UI as a visible banner when signal data is stale. (The backend already produces this field — it is not displayed in the frontend.)
4. Add market-price validation to paper trade execution: reject trades where submitted price deviates > 2% from `app_state.stocks[symbol]["close"]`.

### Medium Term (1–2 Months)

**Month 1 — Feature engineering and data foundation:**
1. Separate `intraday_ticks` from `eod_prices` in the database schema. Migrate existing `price_history` data accordingly based on `source` field.
2. Build `FeatureEngine` with 15–20 features covering price-based, indicator-derived, and cross-sectional categories. Store daily feature snapshots in Parquet files.
3. Build the unified `signal_events` table schema. Write a migration that backfills historical data from the existing four disconnected tables, matching records by `(symbol, timestamp)`.
4. Build the `AttributionAnalyzer` API endpoint (`GET /api/analytics/attribution`) that returns indicator lift scores and confidence calibration data. Build the corresponding frontend chart (calibration curve + indicator lift bar chart).
5. Implement dynamic TP/SL in paper trading (same volatility-derived formula as the forward tracker). Unify the slippage model across backtester and forward tracker.

**Month 2 — First ML model:**
1. Train the first XGBoost model on the feature store. Short-horizon target only (`target_short`) as the initial scope.
2. Implement time-series cross-validation. Evaluate calibration (Brier score, reliability diagram). Document expected accuracy range per signal type.
3. Deploy the ML model in shadow mode alongside the heuristic engine. Both produce predictions; the heuristic predictions go live, the ML predictions are logged and evaluated. Compare calibration and accuracy after 2 weeks.
4. Add the `RegimeClassifier` (index-based rule approach — simple and immediate). Attach current regime to every signal event and WS broadcast.
5. Implement the Strategy abstraction interface. Port the existing 5 strategies to implement it. Merge the backtester signal logic and the live engine signal logic into the same implementations.
6. Begin Redis event bus integration. Start with one stream: publish `signal_generated` events after each poll tick. Have the forward tracker consume from this stream rather than being called directly. This is the first step toward service decomposition.

### Long Term (3–6 Months)

**Months 3–4 — Full intelligence layer:**
1. Promote the ML model from shadow to active (if calibration metrics are met). Replace heuristic confidence with calibrated ML confidence across the UI and all downstream consumers.
2. Implement medium and long horizon ML targets. Train separate models per horizon or a multi-output model.
3. Implement weekly automated retraining pipeline (CronJob). Add model registry with version tracking. Implement automatic rollback if new model degrades below previous model's Brier score.
4. Implement the ensemble signal resolution (Phase B3): weighted combination of strategy outputs using recent forward-test performance as weights.
5. Build the corporate events data pipeline (PSX announcements scraper). Add `days_to_next_results` and `days_since_last_dividend` as features.
6. Implement pending orders (limit + stop-limit) in the paper portfolio. Add the `orders` table and `OrderManager` background task.

**Months 5–6 — Production hardening and advanced features:**
1. Migrate from SQLite to TimescaleDB. This is a data migration (not a code migration — the ORM is already database-agnostic). Requires a planned downtime window or a live migration with dual-write.
2. Implement portfolio-level risk controls (Phase C4): max position size, max sector exposure, drawdown circuit breaker.
3. Implement Kelly-based position sizing as an advisory recommendation (not enforced — display the Kelly-recommended size alongside the trade panel, let the user decide whether to follow it).
4. Complete the UX overhaul: Decision Dashboard (three-panel layout), Signal Performance Tracker, backtesting parameter sweep visualization with regime overlay.
5. Build the self-learning parameter optimizer (Bayesian optimization over RSI/SMA thresholds, weekly cadence, statistical significance gating).
6. Implement Prometheus metrics export and configure alerting for the five critical rules. Deploy with Docker Compose. Add end-to-end integration test suite as a pre-deployment gate.
7. Begin HMM-based regime classifier research using 6+ months of accumulated KSE-100 proxy data. Target production deployment at month 9+.
