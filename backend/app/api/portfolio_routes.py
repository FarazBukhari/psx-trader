"""
Portfolio & Trade API routes.

Endpoints:
  GET  /api/portfolio                         — full summary with live P&L
  GET  /api/portfolio/buying-power/{symbol}   — how many shares can be bought
  POST /api/portfolio/cash                    — set cash balance
  GET  /api/portfolio/positions               — list open positions
  POST /api/portfolio/positions               — manually add a position
  DELETE /api/portfolio/positions/{symbol}    — manually remove a position
  POST /api/trades/buy                        — execute a BUY order
  POST /api/trades/sell                       — execute a SELL order
  GET  /api/trades                            — trade history (paginated)
  GET  /api/trades/{symbol}                   — trade history for one symbol

All trade-execution endpoints require:
  - Market to be OPEN (Depends(require_market_open)) → HTTP 423 if not
  - Rate limit not exceeded (Depends(require_trade_rate_limit)) → HTTP 429 if not
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..portfolio.portfolio_manager import (
    PortfolioManager,
    InsufficientCashError,
    InsufficientSharesError,
    PositionNotFoundError,
    PortfolioNotFoundError,
)
from ..portfolio.schemas import (
    AddPositionRequest,
    BuyRequest,
    SellRequest,
    SetCashRequest,
    PortfolioSummary,
    TradeResult,
    TradeView,
    BuyingPowerView,
)
from ..state import app_state
from .deps import require_market_open, require_trade_rate_limit

logger = logging.getLogger("psx.portfolio")

portfolio_router = APIRouter(prefix="/api", tags=["portfolio"])

# Module-level manager instance — stateless, safe to share
_pm = PortfolioManager()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _current_prices() -> dict[str, float]:
    """Extract symbol→price map from app_state.stocks."""
    return {
        sym: data["close"]
        for sym, data in app_state.stocks.items()
        if data.get("close") is not None
    }


def _handle_domain_error(exc: Exception) -> HTTPException:
    """Map PortfolioManager domain exceptions to HTTP errors."""
    if isinstance(exc, InsufficientCashError):
        return HTTPException(status_code=422, detail={"code": "INSUFFICIENT_CASH",   "message": str(exc)})
    if isinstance(exc, InsufficientSharesError):
        return HTTPException(status_code=422, detail={"code": "INSUFFICIENT_SHARES", "message": str(exc)})
    if isinstance(exc, PositionNotFoundError):
        return HTTPException(status_code=404, detail={"code": "POSITION_NOT_FOUND",  "message": str(exc)})
    if isinstance(exc, PortfolioNotFoundError):
        return HTTPException(status_code=404, detail={"code": "PORTFOLIO_NOT_FOUND", "message": str(exc)})
    # Unexpected
    logger.exception("Unhandled portfolio error: %s", exc)
    return HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": str(exc)})


# ---------------------------------------------------------------------------
# Portfolio read endpoints
# ---------------------------------------------------------------------------

@portfolio_router.get(
    "/portfolio",
    response_model=PortfolioSummary,
    summary="Get portfolio summary with live P&L",
)
async def get_portfolio() -> PortfolioSummary:
    """
    Returns the full portfolio: cash, all open positions with live unrealized
    P&L, and aggregate realized P&L from the trade ledger.

    Positions show `current_price=null` and `unrealized_pl=null` when the
    market is closed and no live prices are available.
    """
    try:
        return await _pm.get_portfolio(_current_prices())
    except Exception as exc:
        raise _handle_domain_error(exc)


@portfolio_router.get(
    "/portfolio/buying-power/{symbol}",
    response_model=BuyingPowerView,
    summary="How many shares of a symbol can be bought with available cash",
)
async def buying_power(symbol: str) -> BuyingPowerView:
    """
    Calculates the maximum number of whole shares purchasable after
    accounting for brokerage fees.  Requires a live price for the symbol.
    """
    prices = _current_prices()
    sym    = symbol.strip().upper()

    if sym not in prices:
        raise HTTPException(
            status_code=404,
            detail={
                "code":    "SYMBOL_NOT_FOUND",
                "message": f"No live price available for '{sym}'. "
                           "Market may be closed or symbol not tracked.",
            },
        )

    try:
        return await _pm.buying_power(sym, prices[sym])
    except Exception as exc:
        raise _handle_domain_error(exc)


# ---------------------------------------------------------------------------
# Portfolio mutation — cash & manual positions
# ---------------------------------------------------------------------------

@portfolio_router.post(
    "/portfolio/cash",
    response_model=PortfolioSummary,
    summary="Set available cash balance",
)
async def set_cash(payload: SetCashRequest) -> PortfolioSummary:
    """
    Set the cash balance to the given amount (replaces current value).
    Use this to record depositing / withdrawing funds.
    Returns the updated portfolio summary.
    """
    try:
        await _pm.set_cash(payload.amount)
        return await _pm.get_portfolio(_current_prices())
    except Exception as exc:
        raise _handle_domain_error(exc)


@portfolio_router.get(
    "/portfolio/positions",
    response_model=PortfolioSummary,
    summary="List all open positions",
)
async def get_positions() -> PortfolioSummary:
    """Alias for GET /api/portfolio — returns full summary including positions."""
    try:
        return await _pm.get_portfolio(_current_prices())
    except Exception as exc:
        raise _handle_domain_error(exc)


@portfolio_router.post(
    "/portfolio/positions",
    response_model=PortfolioSummary,
    status_code=201,
    summary="Manually record a pre-existing holding",
)
async def add_position(payload: AddPositionRequest) -> PortfolioSummary:
    """
    Record a holding that was purchased outside this system.
    Cash is NOT deducted — this is a manual ledger entry only.
    If the symbol already has an open position, shares are added and the
    weighted average cost is recalculated.
    """
    try:
        await _pm.add_position_manual(
            symbol=payload.symbol,
            shares=payload.shares,
            avg_buy_price=payload.avg_buy_price,
            notes=payload.notes,
        )
        return await _pm.get_portfolio(_current_prices())
    except Exception as exc:
        raise _handle_domain_error(exc)


@portfolio_router.delete(
    "/portfolio/positions/{symbol}",
    response_model=PortfolioSummary,
    summary="Manually remove a position (no trade record created)",
)
async def remove_position(symbol: str) -> PortfolioSummary:
    """
    Delete a position from the portfolio without creating a trade record.
    Use for corrections / reconciliation — not for normal selling.
    """
    try:
        await _pm.remove_position_manual(symbol.strip().upper())
        return await _pm.get_portfolio(_current_prices())
    except Exception as exc:
        raise _handle_domain_error(exc)


# ---------------------------------------------------------------------------
# Trade execution
# ---------------------------------------------------------------------------

@portfolio_router.post(
    "/trades/buy",
    response_model=TradeResult,
    status_code=201,
    summary="Execute a BUY order",
    dependencies=[
        Depends(require_market_open),
        Depends(require_trade_rate_limit),
    ],
)
async def execute_buy(payload: BuyRequest) -> TradeResult:
    """
    Execute a BUY order against the portfolio.

    Validates:
    - Market is OPEN and data is live (HTTP 423 otherwise)
    - Rate limit not exceeded (HTTP 429 otherwise)
    - Sufficient cash including fees (HTTP 422 otherwise)

    On success:
    - Deducts `shares × price + fees` from cash
    - Creates or updates position (weighted average cost basis)
    - Returns the trade record + updated portfolio summary
    """
    try:
        trade     = await _pm.execute_buy(
            symbol=payload.symbol,
            shares=payload.shares,
            price=payload.price,
            notes=payload.notes,
        )
        portfolio = await _pm.get_portfolio(_current_prices())
        return TradeResult(
            trade=trade,
            portfolio=portfolio,
            message=(
                f"BUY executed: {payload.shares:.2f} × {payload.symbol} "
                f"@ PKR {payload.price:.2f}. "
                f"Net cost: PKR {trade.net_value:,.2f} (incl. PKR {trade.brokerage_fee:.2f} fee). "
                f"Cash remaining: PKR {portfolio.cash_available:,.2f}."
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _handle_domain_error(exc)


@portfolio_router.post(
    "/trades/sell",
    response_model=TradeResult,
    status_code=201,
    summary="Execute a SELL order",
    dependencies=[
        Depends(require_market_open),
        Depends(require_trade_rate_limit),
    ],
)
async def execute_sell(payload: SellRequest) -> TradeResult:
    """
    Execute a SELL order against the portfolio.

    Validates:
    - Market is OPEN and data is live (HTTP 423 otherwise)
    - Rate limit not exceeded (HTTP 429 otherwise)
    - Position exists with sufficient shares (HTTP 422 / 404 otherwise)

    Realized P&L = net proceeds − cost basis (pre-CGT).
    On success adds net proceeds to cash and reduces / closes the position.
    """
    try:
        trade     = await _pm.execute_sell(
            symbol=payload.symbol,
            shares=payload.shares,
            price=payload.price,
            notes=payload.notes,
        )
        portfolio = await _pm.get_portfolio(_current_prices())

        pl_sign = "+" if (trade.realized_pl or 0) >= 0 else ""
        return TradeResult(
            trade=trade,
            portfolio=portfolio,
            message=(
                f"SELL executed: {payload.shares:.2f} × {payload.symbol} "
                f"@ PKR {payload.price:.2f}. "
                f"Net proceeds: PKR {trade.net_value:,.2f}. "
                f"Realized P&L: {pl_sign}PKR {trade.realized_pl:,.2f}."
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _handle_domain_error(exc)


# ---------------------------------------------------------------------------
# Trade history
# ---------------------------------------------------------------------------

@portfolio_router.get(
    "/trades",
    response_model=dict,
    summary="Trade history (paginated)",
)
async def get_trades(
    limit:  int = Query(50,  ge=1, le=200),
    offset: int = Query(0,   ge=0),
) -> dict:
    """
    Returns paginated trade history across all symbols, newest-first.

    Response shape:
        { "trades": [...], "total": N, "limit": N, "offset": N }
    """
    try:
        trades, total = await _pm.get_trades(limit=limit, offset=offset)
        return {"trades": [t.model_dump() for t in trades], "total": total, "limit": limit, "offset": offset}
    except Exception as exc:
        raise _handle_domain_error(exc)


@portfolio_router.get(
    "/trades/{symbol}",
    response_model=dict,
    summary="Trade history for a specific symbol",
)
async def get_trades_for_symbol(
    symbol: str,
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0,  ge=0),
) -> dict:
    """
    Returns paginated trade history for a single symbol, newest-first.
    """
    try:
        trades, total = await _pm.get_trades(
            symbol=symbol.strip().upper(),
            limit=limit,
            offset=offset,
        )
        return {
            "symbol": symbol.strip().upper(),
            "trades": [t.model_dump() for t in trades],
            "total":  total,
            "limit":  limit,
            "offset": offset,
        }
    except Exception as exc:
        raise _handle_domain_error(exc)
