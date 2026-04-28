"""
Pydantic schemas for the portfolio layer.

Naming convention:
  *Request  — inbound payload validated by FastAPI
  *View     — outbound response shape (what the API returns)
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field, field_validator


# ──────────────────────────────────────────────────────────────────────────────
# Request schemas (inbound)
# ──────────────────────────────────────────────────────────────────────────────

class SetCashRequest(BaseModel):
    amount: float = Field(..., ge=0, description="Cash available in PKR (0 = no cash)")

    model_config = {"json_schema_extra": {"example": {"amount": 500000.0}}}


class AddPositionRequest(BaseModel):
    """Manually record a pre-existing holding (bypasses market hours)."""
    symbol:        str   = Field(..., min_length=1, max_length=16)
    shares:        float = Field(..., gt=0, description="Number of shares held")
    avg_buy_price: float = Field(..., gt=0, description="Weighted average purchase price in PKR")
    notes:         Optional[str] = Field(None, max_length=500)

    @field_validator("symbol")
    @classmethod
    def upper_symbol(cls, v: str) -> str:
        return v.strip().upper()

    model_config = {"json_schema_extra": {
        "example": {"symbol": "ENGRO", "shares": 100, "avg_buy_price": 285.0}
    }}


class BuyRequest(BaseModel):
    symbol: str   = Field(..., min_length=1, max_length=16)
    shares: float = Field(..., gt=0, description="Number of shares to buy")
    price:  float = Field(..., gt=0, description="Execution price per share in PKR")
    notes:  Optional[str] = Field(None, max_length=500)

    @field_validator("symbol")
    @classmethod
    def upper_symbol(cls, v: str) -> str:
        return v.strip().upper()

    model_config = {"json_schema_extra": {
        "example": {"symbol": "ENGRO", "shares": 100, "price": 285.40}
    }}


class SellRequest(BaseModel):
    symbol: str   = Field(..., min_length=1, max_length=16)
    shares: float = Field(..., gt=0, description="Number of shares to sell")
    price:  float = Field(..., gt=0, description="Execution price per share in PKR")
    notes:  Optional[str] = Field(None, max_length=500)

    @field_validator("symbol")
    @classmethod
    def upper_symbol(cls, v: str) -> str:
        return v.strip().upper()

    model_config = {"json_schema_extra": {
        "example": {"symbol": "ENGRO", "shares": 50, "price": 310.0}
    }}


# ──────────────────────────────────────────────────────────────────────────────
# Response / view schemas (outbound)
# ──────────────────────────────────────────────────────────────────────────────

class FeeView(BaseModel):
    commission:   float
    cdc:          float
    secp:         float
    total:        float


class PositionView(BaseModel):
    symbol:             str
    shares:             float
    avg_buy_price:      float
    total_invested:     float            # shares × avg_buy_price (cost basis)
    current_price:      Optional[float]  # None if market is closed and no live price
    current_value:      Optional[float]  # shares × current_price
    unrealized_pl:      Optional[float]  # current_value − total_invested
    unrealized_pl_pct:  Optional[float]  # unrealized_pl / total_invested × 100
    breakeven_price:    float            # avg_buy_price × (1 + ~0.323% round-trip fee)
    opened_at:          int              # Unix timestamp
    notes:              Optional[str]


class BuyingPowerView(BaseModel):
    symbol:            str
    current_price:     float
    cash_available:    float
    shares_buyable:    int              # floor(cash / (price × (1 + fee_rate)))
    estimated_cost:    float           # shares_buyable × price + fee
    fee_estimate:      float


class TradeView(BaseModel):
    id:              int
    symbol:          str
    trade_type:      str               # BUY | SELL | FORCE_SELL
    shares:          float
    price_per_share: float
    total_value:     float             # shares × price (gross)
    brokerage_fee:   float
    net_value:       float             # total_value ± fee (cost for BUY, proceeds for SELL)
    realized_pl:     Optional[float]   # None for BUY
    executed_at:     int
    notes:           Optional[str]
    fees:            Optional[FeeView]


class PortfolioSummary(BaseModel):
    portfolio_id:          int
    name:                  str
    cash_available:        float
    total_invested:        float       # current market value of open positions
    total_portfolio_value: float       # cash + total_invested
    unrealized_pl:         float
    realized_pl:           float
    total_pl:              float       # unrealized + realized
    total_pl_pct:          Optional[float]   # total_pl / cost_basis × 100
    position_count:        int
    positions:             list[PositionView]
    updated_at:            int


class TradeResult(BaseModel):
    """Immediate response after executing a trade."""
    trade:          TradeView
    portfolio:      PortfolioSummary
    message:        str
