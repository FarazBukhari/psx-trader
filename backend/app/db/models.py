"""
SQLAlchemy ORM models — all PSX Trader tables.

Table index:
  - PriceHistory        : one row per scrape tick per symbol
  - SignalLog           : every generated signal (persisted for prediction training)
  - Portfolio           : one row per portfolio (single for now, multi later)
  - Position            : open stock positions (source of truth via PortfolioManager)
  - Trade               : immutable ledger of every executed BUY/SELL
  - PortfolioSnapshot   : periodic snapshots for P&L charting over time
  - PredictionLog       : prediction outcomes tracked for accuracy feedback
  - SignalOutcome        : validated signal outcomes across short/medium/long horizons
"""

from __future__ import annotations

import time
from typing import Optional

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


# ---------------------------------------------------------------------------
# PriceHistory
# ---------------------------------------------------------------------------

class PriceHistory(Base):
    __tablename__ = "price_history"

    id         : Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol     : Mapped[str]            = mapped_column(String(16), nullable=False, index=True)
    sector     : Mapped[Optional[str]]  = mapped_column(String(64))
    ldcp       : Mapped[Optional[float]]= mapped_column(Float)   # Last Day Closing Price
    open_price : Mapped[Optional[float]]= mapped_column(Float)
    high       : Mapped[Optional[float]]= mapped_column(Float)
    low        : Mapped[Optional[float]]= mapped_column(Float)
    close      : Mapped[float]          = mapped_column(Float, nullable=False)
    volume     : Mapped[Optional[int]]  = mapped_column(Integer)
    change_pct : Mapped[Optional[float]]= mapped_column(Float)
    source     : Mapped[str]            = mapped_column(String(8), default="live")
    scraped_at : Mapped[int]            = mapped_column(Integer, nullable=False, index=True)  # Unix ts

    __table_args__ = (
        Index("ix_ph_symbol_time", "symbol", "scraped_at"),
    )

    def __repr__(self) -> str:
        return f"<PriceHistory {self.symbol} {self.close} @ {self.scraped_at}>"


# ---------------------------------------------------------------------------
# SignalLog
# ---------------------------------------------------------------------------

class SignalLog(Base):
    __tablename__ = "signals_log"

    id             : Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol         : Mapped[str]            = mapped_column(String(16), nullable=False, index=True)
    signal         : Mapped[str]            = mapped_column(String(16), nullable=False)  # BUY|SELL|HOLD|FORCE_SELL
    prev_signal    : Mapped[Optional[str]]  = mapped_column(String(16))
    signal_changed : Mapped[bool]           = mapped_column(Boolean, default=False)
    signal_sources : Mapped[Optional[str]]  = mapped_column(Text)          # JSON: ["rsi","sma_crossover"]
    action_score   : Mapped[Optional[float]]= mapped_column(Float)
    horizon        : Mapped[str]            = mapped_column(String(8), default="short")
    rsi            : Mapped[Optional[float]]= mapped_column(Float)
    sma5           : Mapped[Optional[float]]= mapped_column(Float)
    sma20          : Mapped[Optional[float]]= mapped_column(Float)
    price          : Mapped[Optional[float]]= mapped_column(Float)
    volume         : Mapped[Optional[int]]  = mapped_column(Integer)
    # Phase 2 prediction fields (nullable — populated by PredictionEngine)
    confidence     : Mapped[Optional[float]]= mapped_column(Float)         # 0.0–0.85
    time_horizon   : Mapped[Optional[str]]  = mapped_column(String(32))    # e.g. "~3 days"
    generated_at   : Mapped[int]            = mapped_column(Integer, nullable=False, index=True)

    __table_args__ = (
        Index("ix_sl_symbol_time", "symbol", "generated_at"),
        Index("ix_sl_signal_time", "signal", "generated_at"),
    )

    def __repr__(self) -> str:
        return f"<SignalLog {self.symbol} {self.signal} @ {self.generated_at}>"


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------

class Portfolio(Base):
    __tablename__ = "portfolio"

    id             : Mapped[int]   = mapped_column(Integer, primary_key=True, autoincrement=True)
    name           : Mapped[str]   = mapped_column(String(64), nullable=False, default="Main Portfolio")
    cash_available : Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at     : Mapped[int]   = mapped_column(Integer, nullable=False, default=lambda: int(time.time()))
    updated_at     : Mapped[int]   = mapped_column(Integer, nullable=False, default=lambda: int(time.time()))

    positions  : Mapped[list["Position"]] = relationship("Position",  back_populates="portfolio", cascade="all, delete-orphan")
    trades     : Mapped[list["Trade"]]    = relationship("Trade",     back_populates="portfolio", cascade="all, delete-orphan")
    snapshots  : Mapped[list["PortfolioSnapshot"]] = relationship("PortfolioSnapshot", back_populates="portfolio", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Portfolio #{self.id} '{self.name}' cash={self.cash_available}>"


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------

class Position(Base):
    __tablename__ = "positions"

    id             : Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id   : Mapped[int]            = mapped_column(Integer, ForeignKey("portfolio.id"), nullable=False)
    symbol         : Mapped[str]            = mapped_column(String(16), nullable=False, index=True)
    shares         : Mapped[float]          = mapped_column(Float, nullable=False)
    avg_buy_price  : Mapped[float]          = mapped_column(Float, nullable=False)   # weighted avg cost basis
    total_invested : Mapped[float]          = mapped_column(Float, nullable=False)   # shares × avg_buy_price
    opened_at      : Mapped[int]            = mapped_column(Integer, nullable=False, default=lambda: int(time.time()))
    notes          : Mapped[Optional[str]]  = mapped_column(Text)

    portfolio : Mapped["Portfolio"] = relationship("Portfolio", back_populates="positions")

    __table_args__ = (
        UniqueConstraint("portfolio_id", "symbol", name="uq_portfolio_symbol"),
        Index("ix_pos_portfolio", "portfolio_id"),
    )

    def __repr__(self) -> str:
        return f"<Position {self.symbol} ×{self.shares} @ {self.avg_buy_price}>"


# ---------------------------------------------------------------------------
# Trade  (immutable ledger — never UPDATE/DELETE)
# ---------------------------------------------------------------------------

class Trade(Base):
    __tablename__ = "trades"

    id              : Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id    : Mapped[int]            = mapped_column(Integer, ForeignKey("portfolio.id"), nullable=False)
    symbol          : Mapped[str]            = mapped_column(String(16), nullable=False, index=True)
    trade_type      : Mapped[str]            = mapped_column(String(16), nullable=False)  # BUY|SELL|FORCE_SELL
    shares          : Mapped[float]          = mapped_column(Float, nullable=False)
    price_per_share : Mapped[float]          = mapped_column(Float, nullable=False)
    total_value     : Mapped[float]          = mapped_column(Float, nullable=False)   # shares × price
    brokerage_fee   : Mapped[float]          = mapped_column(Float, default=0.0)
    net_value       : Mapped[float]          = mapped_column(Float, nullable=False)   # total ± fee
    realized_pl     : Mapped[Optional[float]]= mapped_column(Float)                   # None for BUY
    signal_id       : Mapped[Optional[int]]  = mapped_column(Integer, ForeignKey("signals_log.id"))
    executed_at     : Mapped[int]            = mapped_column(Integer, nullable=False, index=True, default=lambda: int(time.time()))
    notes           : Mapped[Optional[str]]  = mapped_column(Text)

    portfolio : Mapped["Portfolio"] = relationship("Portfolio", back_populates="trades")

    __table_args__ = (
        Index("ix_trade_portfolio_time", "portfolio_id", "executed_at"),
        Index("ix_trade_symbol_time",    "symbol",       "executed_at"),
    )

    def __repr__(self) -> str:
        return f"<Trade {self.trade_type} {self.symbol} ×{self.shares} @ {self.price_per_share}>"


# ---------------------------------------------------------------------------
# PortfolioSnapshot
# ---------------------------------------------------------------------------

class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id              : Mapped[int]   = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id    : Mapped[int]   = mapped_column(Integer, ForeignKey("portfolio.id"), nullable=False)
    total_value     : Mapped[float] = mapped_column(Float, nullable=False)   # cash + market value
    cash            : Mapped[float] = mapped_column(Float, nullable=False)
    invested_value  : Mapped[float] = mapped_column(Float, nullable=False)   # market value of positions
    total_pl        : Mapped[float] = mapped_column(Float, nullable=False)   # unrealized + realized
    unrealized_pl   : Mapped[float] = mapped_column(Float, nullable=False)
    realized_pl     : Mapped[float] = mapped_column(Float, nullable=False)
    snapshotted_at  : Mapped[int]   = mapped_column(Integer, nullable=False, index=True, default=lambda: int(time.time()))

    portfolio : Mapped["Portfolio"] = relationship("Portfolio", back_populates="snapshots")

    __table_args__ = (
        Index("ix_snap_portfolio_time", "portfolio_id", "snapshotted_at"),
    )


# ---------------------------------------------------------------------------
# PredictionLog
# ---------------------------------------------------------------------------

class PredictionLog(Base):
    __tablename__ = "prediction_log"

    id                    : Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol                : Mapped[str]            = mapped_column(String(16), nullable=False, index=True)
    prediction_type       : Mapped[str]            = mapped_column(String(32), nullable=False)  # dip_expected|breakout|reversal
    predicted_at          : Mapped[int]            = mapped_column(Integer, nullable=False)
    price_at_prediction   : Mapped[float]          = mapped_column(Float, nullable=False)
    predicted_direction   : Mapped[Optional[str]]  = mapped_column(String(8))   # up|down
    confidence            : Mapped[Optional[float]]= mapped_column(Float)       # capped at 0.85
    time_horizon_days     : Mapped[Optional[int]]  = mapped_column(Integer)
    target_price          : Mapped[Optional[float]]= mapped_column(Float)
    outcome               : Mapped[str]            = mapped_column(String(16), default="pending")  # correct|incorrect|pending
    outcome_price         : Mapped[Optional[float]]= mapped_column(Float)
    outcome_at            : Mapped[Optional[int]]  = mapped_column(Integer)

    __table_args__ = (
        Index("ix_pred_symbol_time", "symbol", "predicted_at"),
    )


# ---------------------------------------------------------------------------
# SignalOutcome  (Signal Validation Engine results — immutable once evaluated)
# ---------------------------------------------------------------------------

class SignalOutcome(Base):
    __tablename__ = "signal_outcomes"

    id              : Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol          : Mapped[str]            = mapped_column(String(16), nullable=False, index=True)
    signal          : Mapped[str]            = mapped_column(String(16), nullable=False)        # BUY|SELL|HOLD|FORCE_SELL
    signal_sources  : Mapped[Optional[str]]  = mapped_column(Text)                             # JSON string from signals_log
    timestamp       : Mapped[int]            = mapped_column(Integer, nullable=False)           # signal generated_at (Unix ts)

    # Prices
    price_at_signal : Mapped[Optional[float]]= mapped_column(Float)   # last price <= signal time
    price_short     : Mapped[Optional[float]]= mapped_column(Float)   # first price >= signal+30m
    price_medium    : Mapped[Optional[float]]= mapped_column(Float)   # first price >= signal+2h
    price_long      : Mapped[Optional[float]]= mapped_column(Float)   # session-based (see evaluator)

    # Outcomes: "correct" | "incorrect" | "neutral" | NULL (not yet resolved)
    # NULL means the horizon's price data has not yet arrived — filled progressively.
    outcome_short   : Mapped[Optional[str]]  = mapped_column(String(16))
    outcome_medium  : Mapped[Optional[str]]  = mapped_column(String(16))
    outcome_long    : Mapped[Optional[str]]  = mapped_column(String(16))

    # Latency: seconds between target horizon timestamp and the actual scraped_at found.
    # Measures data freshness / execution realism. NULL if price not yet available.
    short_latency_sec  : Mapped[Optional[int]] = mapped_column(Integer)
    medium_latency_sec : Mapped[Optional[int]] = mapped_column(Integer)
    long_latency_sec   : Mapped[Optional[int]] = mapped_column(Integer)

    evaluated_at    : Mapped[int]            = mapped_column(Integer, nullable=False, default=lambda: int(time.time()))

    __table_args__ = (
        UniqueConstraint("symbol", "timestamp", name="uq_signal_outcome_symbol_ts"),
        Index("ix_so_symbol_time",  "symbol",  "timestamp"),
        Index("ix_so_signal",       "signal"),
        Index("ix_so_evaluated_at", "evaluated_at"),
    )

    def __repr__(self) -> str:
        return f"<SignalOutcome {self.symbol} {self.signal} @ {self.timestamp} short={self.outcome_short}>"


# ---------------------------------------------------------------------------
# ForwardTrade  (forward-testing engine — tracks signal performance live)
# ---------------------------------------------------------------------------

class ForwardTrade(Base):
    __tablename__ = "forward_trades"

    id              : Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol          : Mapped[str]            = mapped_column(String(16), nullable=False, index=True)
    signal          : Mapped[str]            = mapped_column(String(16), nullable=False)   # BUY|SELL|FORCE_SELL
    entry_price     : Mapped[float]          = mapped_column(Float, nullable=False)
    entry_time      : Mapped[int]            = mapped_column(Integer, nullable=False)      # Unix ts

    # Real-time extremes — updated every tick while OPEN
    max_price_seen  : Mapped[float]          = mapped_column(Float, nullable=False)
    min_price_seen  : Mapped[float]          = mapped_column(Float, nullable=False)

    # Exit fields (NULL while OPEN)
    exit_price      : Mapped[Optional[float]]= mapped_column(Float)
    exit_time       : Mapped[Optional[int]]  = mapped_column(Integer)

    # Status / outcome
    status          : Mapped[str]            = mapped_column(String(8),  nullable=False, default="OPEN")   # OPEN|CLOSED
    outcome         : Mapped[str]            = mapped_column(String(8),  nullable=False, default="NEUTRAL") # WIN|LOSS|NEUTRAL

    # Metrics — finalised on close; 0.0 while OPEN
    mfe_pct         : Mapped[float]          = mapped_column(Float, nullable=False, default=0.0)  # max favourable excursion
    mae_pct         : Mapped[float]          = mapped_column(Float, nullable=False, default=0.0)  # max adverse excursion
    duration_minutes: Mapped[float]          = mapped_column(Float, nullable=False, default=0.0)  # (exit_time - entry_time) / 60

    __table_args__ = (
        UniqueConstraint("symbol", "entry_time", name="uq_ft_symbol_entry_time"),
        Index("ix_ft_symbol_status", "symbol", "status"),
        Index("ix_ft_entry_time",    "entry_time"),
    )

    def __repr__(self) -> str:
        return f"<ForwardTrade {self.signal} {self.symbol} @ {self.entry_price} [{self.status}]>"
