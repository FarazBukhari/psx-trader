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
