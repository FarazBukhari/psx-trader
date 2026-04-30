"""
Backtester — replay historical price data and simulate strategy performance.

Architecture:
  - Pure simulation: NO database writes, NO side effects.
  - Reuses calculate_fee() from portfolio.fees — identical cost model.
  - Indicator math is self-contained (SMA, RSI) — same formulas as signal_engine,
    but applied over a pre-loaded price series rather than a live rolling buffer.
  - Deterministic: given the same price history + config → same result every run.

Usage:
    bt = Backtester()
    result = await bt.run(symbol="ENGRO", config=StrategyConfig())
    wf     = await bt.run_walk_forward(symbol="ENGRO", config=StrategyConfig())

Strategy variants:
    config = StrategyConfig(
        rsi_oversold=25, rsi_overbought=75,
        sma_short=5, sma_long=20,
        stop_loss_pct=5.0,
    )

Metrics computed:
  - total_return_pct    : (final_equity - start_equity) / start_equity × 100
  - win_rate            : winning trades / total closed trades
  - avg_gain_pct        : mean return of winning trades
  - avg_loss_pct        : mean return of losing trades (negative)
  - max_drawdown_pct    : largest peak-to-trough drop in equity curve
  - profit_factor       : gross_profit / gross_loss
  - sharpe_ratio        : annualised Sharpe using per-trade returns
  - trades              : count of closed trades
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

from ..db.database import get_session
from ..db.models import PriceHistory
from ..portfolio.fees import calculate_fee
from sqlalchemy import select

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Simulation realism constants
# ---------------------------------------------------------------------------
SLIPPAGE_PCT    = 0.001     # 0.1% — models bid/ask spread on execution
MAX_POSITION_PCT = 0.25     # cap each trade to 25% of current equity
MIN_AVG_VOLUME  = 100_000   # skip symbols with thin liquidity
MIN_TICKS       = 50        # minimum rows required to run a backtest

# ---------------------------------------------------------------------------
# Strategy configuration (one variant per backtest run)
# ---------------------------------------------------------------------------

@dataclass
class StrategyConfig:
    """All tunable parameters for a single backtest run."""
    name: str = "default"

    # RSI
    rsi_period:      int   = 14
    rsi_oversold:    float = 30.0
    rsi_overbought:  float = 70.0

    # SMA crossover
    sma_short: int = 5
    sma_long:  int = 20

    # Price threshold (% drop from entry to trigger stop-loss)
    stop_loss_pct: float = 5.0

    # Change % threshold for momentum signal
    change_pct_threshold: float = 3.0

    # Position sizing: fraction of capital per trade (1.0 = all-in)
    position_size_pct: float = 1.0

    # Starting cash for simulation
    starting_cash: float = 100_000.0


# ---------------------------------------------------------------------------
# Simulation state (internal — one per run)
# ---------------------------------------------------------------------------

@dataclass
class _SimState:
    cash:             float
    shares:           float            = 0.0
    avg_cost:         float            = 0.0
    entry_price:      float            = 0.0
    in_position:      bool             = False
    peak_equity:      float            = 0.0
    max_dd:           float            = 0.0
    trades:           list             = field(default_factory=list)
    equity_curve:     list             = field(default_factory=list)
    unrealized_pl:    float            = 0.0   # mark-to-market of open position at end
    last_market_value: float           = 0.0   # shares × last_price (for total_equity)
    skipped:          Optional[str]    = None  # set when sim is skipped early


# ---------------------------------------------------------------------------
# Result structures
# ---------------------------------------------------------------------------

@dataclass
class TradeRecord:
    symbol:      str
    trade_type:  str        # BUY | SELL | FORCE_SELL
    price:       float
    shares:      float
    value:       float
    fee:         float
    net_value:   float
    realized_pl: Optional[float]
    timestamp:   int
    signal:      str
    sources:     list[str]


@dataclass
class BacktestResult:
    strategy:          str
    symbol:            str
    start_ts:          Optional[int]
    end_ts:            Optional[int]
    ticks_used:        int
    starting_cash:     float
    final_equity:      float
    return_pct:        float
    win_rate:          float
    avg_gain_pct:      float
    avg_loss_pct:      float
    max_drawdown_pct:  float
    profit_factor:     Optional[float]   # None when no losing trades (perfect record)
    sharpe_ratio:      Optional[float]   # NOTE: trade-based Sharpe proxy (not time-normalized)
    trades:            int
    winning_trades:    int
    losing_trades:     int
    realized_pl:       float             # sum of closed-trade P&L (after fees)
    unrealized_pl:     float             # mark-to-market of any still-open position
    skipped:           Optional[str]     # reason if simulation was skipped
    trade_log:         list[dict]
    equity_curve:      list[dict]
    config:            dict

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WalkForwardResult:
    strategy:    str
    symbol:      str
    windows:     list[dict]          # each window's BacktestResult summary
    avg_return:  float
    avg_win_rate: float
    stability:   float               # std-dev of returns — lower = more stable


# ---------------------------------------------------------------------------
# Indicator math (pure functions over price lists)
# ---------------------------------------------------------------------------

def _sma(prices: list[float], n: int) -> Optional[float]:
    if len(prices) < n:
        return None
    return sum(prices[-n:]) / n


def _rsi(prices: list[float], period: int = 14) -> Optional[float]:
    """Standard Wilder RSI over the last (period+1) prices."""
    if len(prices) < period + 1:
        return None
    recent = prices[-(period + 1):]
    deltas = [recent[i] - recent[i - 1] for i in range(1, len(recent))]
    gains  = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _prev_sma(prices: list[float], n: int) -> Optional[float]:
    """SMA of the tick *before* the last one — used for crossover detection."""
    if len(prices) < n + 1:
        return None
    return sum(prices[-(n + 1):-1]) / n


# ---------------------------------------------------------------------------
# Signal generation (stateless, applied per tick)
# ---------------------------------------------------------------------------

def _generate_signal(
    prices:     list[float],
    change_pct: Optional[float],
    config:     StrategyConfig,
    avg_cost:   float,              # current position cost (for stop-loss)
) -> tuple[str, list[str]]:
    """
    Return (signal, sources) for the current tick.
    Mirrors signal_engine.py rules but applied over a historical series.
    """
    if not prices:
        return "HOLD", []

    cur = prices[-1]
    signals: list[str] = []
    sources: list[str] = []

    # 1. Stop-loss: current price has dropped stop_loss_pct % below avg entry
    if avg_cost > 0 and cur <= avg_cost * (1 - config.stop_loss_pct / 100):
        return "FORCE_SELL", ["stop_loss"]

    # 2. RSI
    rsi = _rsi(prices, config.rsi_period)
    if rsi is not None:
        if rsi <= config.rsi_oversold:
            signals.append("BUY")
            sources.append("rsi")
        elif rsi >= config.rsi_overbought:
            signals.append("SELL")
            sources.append("rsi")

    # 3. SMA crossover
    sma_s_now  = _sma(prices, config.sma_short)
    sma_l_now  = _sma(prices, config.sma_long)
    sma_s_prev = _prev_sma(prices, config.sma_short)
    sma_l_prev = _prev_sma(prices, config.sma_long)

    if all(v is not None for v in [sma_s_now, sma_l_now, sma_s_prev, sma_l_prev]):
        if sma_s_prev <= sma_l_prev and sma_s_now > sma_l_now:   # golden cross
            signals.append("BUY")
            sources.append("sma_crossover")
        elif sma_s_prev >= sma_l_prev and sma_s_now < sma_l_now:  # death cross
            signals.append("SELL")
            sources.append("sma_crossover")

    # 4. Change % momentum — PSX circuit-breaker clamp: max ±7.5% daily move
    if change_pct is not None:
        change_pct = max(-7.5, min(7.5, change_pct))
        if change_pct <= -config.change_pct_threshold:
            signals.append("SELL")
            sources.append("change_pct")
        elif change_pct >= config.change_pct_threshold:
            signals.append("BUY")
            sources.append("change_pct")

    # Priority: FORCE_SELL > SELL > BUY > HOLD
    _priority = ["FORCE_SELL", "SELL", "BUY", "HOLD"]
    for p in _priority:
        if p in signals:
            return p, sources
    return "HOLD", []


# ---------------------------------------------------------------------------
# Core simulation loop
# ---------------------------------------------------------------------------

def _simulate(
    rows:   list[dict],
    config: StrategyConfig,
    symbol: str,
) -> _SimState:
    """
    Replay price rows and simulate trades. Returns final _SimState.

    Rules:
      - Signal generated at tick t is EXECUTED at tick t+1 (no lookahead bias).
      - Last pending signal is NOT executed (no peeking beyond history).
      - Slippage of SLIPPAGE_PCT (0.1%) applied to every execution price.
      - Position capped at MAX_POSITION_PCT (25%) of current equity per trade.
      - One position at a time (no pyramiding).
      - Brokerage fees applied on every side after slippage.
      - Open positions at end-of-history are NOT force-closed; unrealized P&L tracked.
    """
    state = _SimState(cash=config.starting_cash)
    state.peak_equity = config.starting_cash

    # ── Liquidity filter — skip thin symbols pre-loop ─────────────────────
    N_vol      = min(len(rows), 30)
    volumes    = [r.get("volume") or 0 for r in rows[-N_vol:]]
    avg_volume = sum(volumes) / N_vol if N_vol > 0 else 0
    if avg_volume < MIN_AVG_VOLUME:
        logger.warning(
            "Backtest %s: avg volume %.0f < %d — skipping (low_liquidity)",
            symbol, avg_volume, MIN_AVG_VOLUME,
        )
        state.skipped = "low_liquidity"
        return state

    prices: list[float] = []
    min_warmup = max(config.sma_long, config.rsi_period + 2)

    # Pending signal: generated at tick t, executed at tick t+1
    pending_signal:  str       = "HOLD"
    pending_sources: list[str] = []

    for row in rows:
        cur_price  = row["close"]
        change_pct = row.get("change_pct")
        ts         = row["scraped_at"]

        prices.append(cur_price)

        # Equity = cash + market value of open position
        market_val = state.shares * cur_price if state.in_position else 0.0
        equity     = state.cash + market_val
        state.last_market_value = market_val

        # Update peak equity and max drawdown
        if equity > state.peak_equity:
            state.peak_equity = equity
        dd = (state.peak_equity - equity) / state.peak_equity * 100 if state.peak_equity > 0 else 0.0
        if dd > state.max_dd:
            state.max_dd = dd

        state.equity_curve.append({
            "ts":     ts,
            "price":  cur_price,
            "equity": round(equity, 2),
            "cash":   round(state.cash, 2),
        })

        # ── Step 1: Execute the PREVIOUS tick's pending signal ────────────
        # Slippage applied to actual execution price (before fee calculation)
        exec_signal  = pending_signal
        exec_sources = list(pending_sources)
        buy_price    = round(cur_price * (1 + SLIPPAGE_PCT), 4)
        sell_price   = round(cur_price * (1 - SLIPPAGE_PCT), 4)

        if exec_signal == "BUY" and not state.in_position and state.cash > 0:
            # Position size = min(position_size_pct of cash, MAX_POSITION_PCT of equity)
            deploy_cash = min(
                state.cash * config.position_size_pct,
                equity * MAX_POSITION_PCT,
            )
            shares = math.floor(deploy_cash / (buy_price * (1 + 0.002)))  # conservative estimate
            if shares <= 0:
                pass  # insufficient cash — skip this signal
            else:
                trade_value = shares * buy_price
                actual_fee  = calculate_fee(trade_value)
                total_cost  = trade_value + actual_fee.total

                # Safety: trim 1 share if we accidentally overshoot
                if total_cost > state.cash:
                    shares -= 1
                    if shares <= 0:
                        pass
                    else:
                        trade_value = shares * buy_price
                        actual_fee  = calculate_fee(trade_value)
                        total_cost  = trade_value + actual_fee.total

                if shares > 0 and total_cost <= state.cash:
                    state.cash       -= total_cost
                    state.shares      = shares
                    state.avg_cost    = buy_price   # cost basis uses slippage price
                    state.entry_price = buy_price
                    state.in_position = True

                    state.trades.append(TradeRecord(
                        symbol=symbol, trade_type="BUY",
                        price=buy_price, shares=shares,
                        value=trade_value, fee=actual_fee.total,
                        net_value=total_cost,
                        realized_pl=None, timestamp=ts,
                        signal=exec_signal, sources=exec_sources,
                    ))

        elif exec_signal in ("SELL", "FORCE_SELL") and state.in_position and state.shares > 0:
            trade_value  = state.shares * sell_price
            sell_fee     = calculate_fee(trade_value)
            net_proceeds = trade_value - sell_fee.total
            cost_basis   = state.avg_cost * state.shares
            realized_pl  = net_proceeds - cost_basis

            state.cash += net_proceeds
            state.trades.append(TradeRecord(
                symbol=symbol, trade_type=exec_signal,
                price=sell_price, shares=state.shares,
                value=trade_value, fee=sell_fee.total,
                net_value=net_proceeds,
                realized_pl=round(realized_pl, 2),
                timestamp=ts, signal=exec_signal, sources=exec_sources,
            ))

            state.shares      = 0.0
            state.avg_cost    = 0.0
            state.entry_price = 0.0
            state.in_position = False

        # ── Step 2: Generate signal for the NEXT tick ─────────────────────
        if len(prices) >= min_warmup:
            pending_signal, pending_sources = _generate_signal(
                prices,
                change_pct,
                config,
                state.avg_cost if state.in_position else 0.0,
            )
        else:
            pending_signal, pending_sources = "HOLD", []

    # End of history: do NOT execute last pending_signal (no lookahead).
    # Track unrealized P&L for any still-open position.
    if state.in_position and state.shares > 0 and prices:
        last_price           = prices[-1]
        market_value         = state.shares * last_price
        cost_basis           = state.avg_cost * state.shares
        state.unrealized_pl  = round(market_value - cost_basis, 2)
        state.last_market_value = market_value
    else:
        state.unrealized_pl     = 0.0
        state.last_market_value = 0.0

    return state


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def _compute_metrics(state: _SimState, config: StrategyConfig) -> dict:
    """Derive all performance metrics from a completed simulation state."""
    closed = [t for t in state.trades if t.realized_pl is not None]
    wins   = [t for t in closed if t.realized_pl > 0]
    losses = [t for t in closed if t.realized_pl <= 0]

    realized_pl   = round(sum(t.realized_pl for t in closed), 2)
    unrealized_pl = round(state.unrealized_pl, 2)
    total_equity  = round(state.cash + state.last_market_value, 2)

    # Return is total equity (cash + open position MTM) vs. starting cash
    total_return_pct = round(
        (total_equity - config.starting_cash) / config.starting_cash * 100, 4
    )

    win_rate = round(len(wins) / len(closed) * 100, 2) if closed else 0.0

    avg_gain_pct = 0.0
    if wins:
        gain_pcts = [
            t.realized_pl / (t.shares * t.price - t.realized_pl) * 100
            for t in wins
        ]
        avg_gain_pct = round(sum(gain_pcts) / len(gain_pcts), 4)

    avg_loss_pct = 0.0
    if losses:
        loss_pcts = [
            t.realized_pl / (t.shares * t.price - t.realized_pl) * 100
            for t in losses
        ]
        avg_loss_pct = round(sum(loss_pcts) / len(loss_pcts), 4)

    gross_profit  = sum(t.realized_pl for t in wins)
    gross_loss    = abs(sum(t.realized_pl for t in losses))
    profit_factor = round(gross_profit / gross_loss, 4) if gross_loss > 0 else None

    # NOTE: trade-based Sharpe proxy (not time-normalized — each closed trade
    # is treated as one "period"). Useful for relative comparison only.
    sharpe: Optional[float] = None
    if len(closed) >= 3:
        returns = [t.realized_pl for t in closed]
        mean_r  = sum(returns) / len(returns)
        var_r   = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        std_r   = math.sqrt(var_r)
        if std_r > 0:
            sharpe = round(mean_r / std_r * math.sqrt(252), 4)

    return {
        "total_return_pct": total_return_pct,
        "win_rate":         win_rate,
        "avg_gain_pct":     avg_gain_pct,
        "avg_loss_pct":     avg_loss_pct,
        "max_drawdown_pct": round(state.max_dd, 4),
        "profit_factor":    profit_factor,
        "sharpe_ratio":     sharpe,
        "trades":           len(closed),
        "winning_trades":   len(wins),
        "losing_trades":    len(losses),
        "final_equity":     total_equity,
        "realized_pl":      realized_pl,
        "unrealized_pl":    unrealized_pl,
    }


# ---------------------------------------------------------------------------
# Backtester (async, reads from DB)
# ---------------------------------------------------------------------------

class Backtester:
    """
    Async backtesting engine. Reads price_history from DB, simulates trades.

    All public methods are coroutines.
    """

    async def _load_rows(
        self,
        symbol: str,
        start_ts: Optional[int] = None,
        end_ts:   Optional[int] = None,
    ) -> list[dict]:
        """Fetch price rows from DB, ordered oldest-first."""
        async with get_session() as session:
            q = select(PriceHistory).where(
                PriceHistory.symbol == symbol.upper()
            )
            if start_ts:
                q = q.where(PriceHistory.scraped_at >= start_ts)
            if end_ts:
                q = q.where(PriceHistory.scraped_at <= end_ts)
            q = q.order_by(PriceHistory.scraped_at.asc())
            result = await session.execute(q)
            rows = result.scalars().all()

        return [
            {
                "id":         r.id,
                "symbol":     r.symbol,
                "close":      r.close,
                "high":       r.high,
                "low":        r.low,
                "open":       r.open_price,
                "volume":     r.volume,
                "change_pct": r.change_pct,
                "scraped_at": r.scraped_at,
            }
            for r in rows
        ]

    async def run(
        self,
        symbol:   str,
        config:   StrategyConfig,
        start_ts: Optional[int] = None,
        end_ts:   Optional[int] = None,
    ) -> BacktestResult:
        """
        Run a single backtest for `symbol` using the given `config`.

        Args:
            symbol:   PSX ticker (e.g. "ENGRO")
            config:   StrategyConfig with all tunable params
            start_ts: Unix timestamp — only use prices after this point
            end_ts:   Unix timestamp — only use prices before this point

        Returns:
            BacktestResult with full metrics, trade log, equity curve.
        """
        rows = await self._load_rows(symbol, start_ts, end_ts)
        if len(rows) < MIN_TICKS:
            logger.warning(
                "Backtest %s: insufficient data (%d rows, need %d)",
                symbol, len(rows), MIN_TICKS,
            )
            return self._empty_result(
                symbol, config, start_ts, end_ts, len(rows),
                skipped="insufficient_data",
            )

        logger.info(
            "Backtest %s '%s': %d ticks, %s → %s",
            symbol, config.name, len(rows),
            rows[0]["scraped_at"], rows[-1]["scraped_at"],
        )

        state   = _simulate(rows, config, symbol)
        # Propagate liquidity skip from simulation layer
        if state.skipped:
            return self._empty_result(
                symbol, config,
                rows[0]["scraped_at"], rows[-1]["scraped_at"],
                len(rows), skipped=state.skipped,
            )

        metrics = _compute_metrics(state, config)

        trade_log = [
            {
                "trade_type":  t.trade_type,
                "price":       t.price,
                "shares":      t.shares,
                "value":       round(t.value, 2),
                "fee":         round(t.fee, 2),
                "net_value":   round(t.net_value, 2),
                "realized_pl": t.realized_pl,
                "timestamp":   t.timestamp,
                "signal":      t.signal,
                "sources":     t.sources,
            }
            for t in state.trades
        ]

        return BacktestResult(
            strategy=config.name,
            symbol=symbol.upper(),
            start_ts=rows[0]["scraped_at"],
            end_ts=rows[-1]["scraped_at"],
            ticks_used=len(rows),
            starting_cash=config.starting_cash,
            final_equity=metrics["final_equity"],
            return_pct=metrics["total_return_pct"],
            win_rate=metrics["win_rate"],
            avg_gain_pct=metrics["avg_gain_pct"],
            avg_loss_pct=metrics["avg_loss_pct"],
            max_drawdown_pct=metrics["max_drawdown_pct"],
            profit_factor=metrics["profit_factor"],
            sharpe_ratio=metrics["sharpe_ratio"],
            trades=metrics["trades"],
            winning_trades=metrics["winning_trades"],
            losing_trades=metrics["losing_trades"],
            realized_pl=metrics["realized_pl"],
            unrealized_pl=metrics["unrealized_pl"],
            skipped=None,
            trade_log=trade_log,
            equity_curve=state.equity_curve,
            config=asdict(config),
        )

    async def run_variants(
        self,
        symbol:   str,
        variants: list[StrategyConfig],
        start_ts: Optional[int] = None,
        end_ts:   Optional[int] = None,
    ) -> list[BacktestResult]:
        """
        Run multiple strategy configs against the same symbol and date range.
        Returns a list of results, one per variant, sorted by return_pct desc.
        """
        rows = await self._load_rows(symbol, start_ts, end_ts)
        if len(rows) < MIN_TICKS:
            return [
                self._empty_result(symbol, c, start_ts, end_ts, len(rows), skipped="insufficient_data")
                for c in variants
            ]

        results = []
        for config in variants:
            state = _simulate(rows, config, symbol)
            if state.skipped:
                results.append(self._empty_result(
                    symbol, config,
                    rows[0]["scraped_at"], rows[-1]["scraped_at"],
                    len(rows), skipped=state.skipped,
                ))
                continue

            metrics = _compute_metrics(state, config)
            trade_log = [
                {
                    "trade_type":  t.trade_type,
                    "price":       t.price,
                    "shares":      t.shares,
                    "value":       round(t.value, 2),
                    "fee":         round(t.fee, 2),
                    "net_value":   round(t.net_value, 2),
                    "realized_pl": t.realized_pl,
                    "timestamp":   t.timestamp,
                    "signal":      t.signal,
                    "sources":     t.sources,
                }
                for t in state.trades
            ]
            results.append(BacktestResult(
                strategy=config.name,
                symbol=symbol.upper(),
                start_ts=rows[0]["scraped_at"],
                end_ts=rows[-1]["scraped_at"],
                ticks_used=len(rows),
                starting_cash=config.starting_cash,
                final_equity=metrics["final_equity"],
                return_pct=metrics["total_return_pct"],
                win_rate=metrics["win_rate"],
                avg_gain_pct=metrics["avg_gain_pct"],
                avg_loss_pct=metrics["avg_loss_pct"],
                max_drawdown_pct=metrics["max_drawdown_pct"],
                profit_factor=metrics["profit_factor"],
                sharpe_ratio=metrics["sharpe_ratio"],
                trades=metrics["trades"],
                winning_trades=metrics["winning_trades"],
                losing_trades=metrics["losing_trades"],
                realized_pl=metrics["realized_pl"],
                unrealized_pl=metrics["unrealized_pl"],
                skipped=None,
                trade_log=trade_log,
                equity_curve=state.equity_curve,
                config=asdict(config),
            ))

        results.sort(key=lambda r: r.return_pct, reverse=True)
        return results

    async def run_walk_forward(
        self,
        symbol:       str,
        config:       StrategyConfig,
        n_windows:    int = 3,
        train_ratio:  float = 0.7,
    ) -> WalkForwardResult:
        """
        Walk-forward validation: split history into n_windows of
        (train → test) segments and evaluate out-of-sample stability.

        train_ratio: fraction of each window used for warm-up (in-sample).
        The strategy is always evaluated on the test (out-of-sample) portion.

        Returns WalkForwardResult with per-window summaries + aggregate stats.
        """
        rows = await self._load_rows(symbol)
        if len(rows) < 20:
            logger.warning("Walk-forward %s: too little data (%d rows)", symbol, len(rows))
            return WalkForwardResult(
                strategy=config.name,
                symbol=symbol.upper(),
                windows=[],
                avg_return=0.0,
                avg_win_rate=0.0,
                stability=0.0,
            )

        window_size = len(rows) // n_windows
        window_results = []

        for i in range(n_windows):
            w_start = i * window_size
            w_end   = w_start + window_size if i < n_windows - 1 else len(rows)
            window_rows = rows[w_start:w_end]

            split    = int(len(window_rows) * train_ratio)
            test_rows = window_rows[split:]   # evaluate on out-of-sample tail
            # But pass the full window so indicators warm up on train portion
            warmup_rows = window_rows[:split]

            # Initialise prices from train rows for indicator warmup
            # then simulate only on test rows (no trades during warmup)
            all_rows_for_window = window_rows  # simulation gets full window

            # Run simulation over full window, only count trades in test portion
            state = _simulate(all_rows_for_window, config, symbol)

            # Filter trades to test portion
            test_start_ts = test_rows[0]["scraped_at"] if test_rows else 0
            test_trades = [t for t in state.trades if t.timestamp >= test_start_ts]

            # Re-derive equity curve for test portion
            test_equity = [
                e for e in state.equity_curve
                if e["ts"] >= test_start_ts
            ]

            # Quick metrics for this window's test segment
            closed   = [t for t in test_trades if t.realized_pl is not None]
            wins     = [t for t in closed if t.realized_pl > 0]
            start_eq = test_equity[0]["equity"] if test_equity else config.starting_cash
            end_eq   = test_equity[-1]["equity"] if test_equity else config.starting_cash
            ret_pct  = round((end_eq - start_eq) / start_eq * 100, 4) if start_eq > 0 else 0.0
            win_rate = round(len(wins) / len(closed) * 100, 2) if closed else 0.0

            window_results.append({
                "window":          i + 1,
                "train_ticks":     split,
                "test_ticks":      len(test_rows),
                "test_start_ts":   test_start_ts,
                "test_end_ts":     window_rows[-1]["scraped_at"] if window_rows else None,
                "return_pct":      ret_pct,
                "win_rate":        win_rate,
                "trades":          len(closed),
                "winning_trades":  len(wins),
            })

        returns = [w["return_pct"] for w in window_results]
        avg_ret = round(sum(returns) / len(returns), 4) if returns else 0.0
        win_rates = [w["win_rate"] for w in window_results]
        avg_wr  = round(sum(win_rates) / len(win_rates), 2) if win_rates else 0.0

        # Stability = inverse of std-dev of returns (lower variation = more stable)
        if len(returns) > 1:
            mean_r = sum(returns) / len(returns)
            var_r  = sum((r - mean_r) ** 2 for r in returns) / len(returns)
            stability = round(math.sqrt(var_r), 4)
        else:
            stability = 0.0

        return WalkForwardResult(
            strategy=config.name,
            symbol=symbol.upper(),
            windows=window_results,
            avg_return=avg_ret,
            avg_win_rate=avg_wr,
            stability=stability,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_result(
        symbol:   str,
        config:   StrategyConfig,
        start_ts: Optional[int],
        end_ts:   Optional[int],
        ticks:    int,
        skipped:  Optional[str] = None,
    ) -> BacktestResult:
        return BacktestResult(
            strategy=config.name,
            symbol=symbol.upper(),
            start_ts=start_ts,
            end_ts=end_ts,
            ticks_used=ticks,
            starting_cash=config.starting_cash,
            final_equity=config.starting_cash,
            return_pct=0.0,
            win_rate=0.0,
            avg_gain_pct=0.0,
            avg_loss_pct=0.0,
            max_drawdown_pct=0.0,
            profit_factor=0.0,
            sharpe_ratio=None,
            trades=0,
            winning_trades=0,
            losing_trades=0,
            realized_pl=0.0,
            unrealized_pl=0.0,
            skipped=skipped,
            trade_log=[],
            equity_curve=[],
            config=asdict(config),
        )


# ---------------------------------------------------------------------------
# Preset strategy variants (for quick comparison)
# ---------------------------------------------------------------------------

PRESET_VARIANTS: list[StrategyConfig] = [
    StrategyConfig(
        name="conservative",
        rsi_oversold=25, rsi_overbought=75,
        sma_short=10, sma_long=30,
        stop_loss_pct=4.0,
        change_pct_threshold=4.0,
    ),
    StrategyConfig(
        name="default",
        rsi_oversold=30, rsi_overbought=70,
        sma_short=5, sma_long=20,
        stop_loss_pct=5.0,
        change_pct_threshold=3.0,
    ),
    StrategyConfig(
        name="aggressive",
        rsi_oversold=35, rsi_overbought=65,
        sma_short=3, sma_long=10,
        stop_loss_pct=8.0,
        change_pct_threshold=2.0,
    ),
    StrategyConfig(
        name="momentum",
        rsi_oversold=40, rsi_overbought=60,
        sma_short=5, sma_long=15,
        stop_loss_pct=6.0,
        change_pct_threshold=1.5,
    ),
]
