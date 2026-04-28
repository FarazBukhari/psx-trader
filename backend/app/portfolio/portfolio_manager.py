"""
PortfolioManager — single source of truth for all portfolio state.

Design principles:
  - Every public method reads and writes the database. No in-memory caching.
  - P&L is always re-derived from DB + current live prices. Never stored as
    a running total (avoids drift from rounding or missed updates).
  - Realized P&L is computed at the moment of each SELL and stored immutably
    in the trades table.
  - The Portfolio.cash_available column is the authoritative cash balance.
    It is modified by execute_buy / execute_sell only.
  - Positions are the authoritative record of open holdings.
  - This class has no knowledge of market hours. Callers enforce that.
  - signal_engine.py remains read-only — this class never calls it.

Thread / concurrency safety:
  SQLite with aiosqlite is single-writer by default. Since this is a
  single-user system, this is acceptable. PostgreSQL upgrade path:
  replace get_session() with a PostgreSQL-backed session — everything else
  is identical.
"""

from __future__ import annotations

import logging
import time as _time
from typing import Optional

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.database import get_session
from ..db.models import Portfolio, Position, Trade, PortfolioSnapshot
from .fees import calculate_fee, FeeBreakdown
from .schemas import (
    PortfolioSummary,
    PositionView,
    TradeView,
    FeeView,
    BuyingPowerView,
)

logger = logging.getLogger(__name__)

# Default portfolio ID — the system always operates against portfolio #1.
# Multi-portfolio support is schema-ready; just pass portfolio_id explicitly.
DEFAULT_PORTFOLIO_ID = 1
DEFAULT_PORTFOLIO_NAME = "Main Portfolio"


class PortfolioManager:
    """
    Async portfolio manager. All state is derived from the database.

    Usage:
        pm = PortfolioManager()
        await pm.ensure_default_portfolio()   # call once on startup
        summary = await pm.get_portfolio(current_prices)
        trade   = await pm.execute_buy("ENGRO", 100, 285.40)
    """

    # ──────────────────────────────────────────────────────────────────────
    # Startup
    # ──────────────────────────────────────────────────────────────────────

    async def ensure_default_portfolio(self) -> None:
        """
        Create portfolio #1 if it doesn't exist.
        Safe to call on every startup — is a no-op if already present.
        """
        async with get_session() as session:
            existing = await session.get(Portfolio, DEFAULT_PORTFOLIO_ID)
            if existing is None:
                portfolio = Portfolio(
                    id=DEFAULT_PORTFOLIO_ID,
                    name=DEFAULT_PORTFOLIO_NAME,
                    cash_available=0.0,
                    created_at=int(_time.time()),
                    updated_at=int(_time.time()),
                )
                session.add(portfolio)
                logger.info("Created default portfolio #%d", DEFAULT_PORTFOLIO_ID)
            else:
                logger.debug("Default portfolio #%d already exists", DEFAULT_PORTFOLIO_ID)

    # ──────────────────────────────────────────────────────────────────────
    # Read operations
    # ──────────────────────────────────────────────────────────────────────

    async def get_portfolio(
        self,
        current_prices: dict[str, float],
        portfolio_id: int = DEFAULT_PORTFOLIO_ID,
    ) -> PortfolioSummary:
        """
        Return full portfolio summary with live P&L.

        Args:
            current_prices: symbol → current price from app_state.stocks.
                            Pass {} when market is closed; positions will
                            show current_price=None and unrealized_pl=None.
        """
        async with get_session() as session:
            portfolio  = await self._require_portfolio(session, portfolio_id)
            positions  = await self._get_positions(session, portfolio_id)
            realized   = await self._total_realized_pl(session, portfolio_id)

        position_views, total_invested, unrealized = self._enrich_positions(
            positions, current_prices
        )

        cost_basis = sum(p.total_invested for p in positions)
        total_pl   = unrealized + realized
        pl_pct     = round(total_pl / cost_basis * 100, 2) if cost_basis else None

        return PortfolioSummary(
            portfolio_id=portfolio_id,
            name=portfolio.name,
            cash_available=round(portfolio.cash_available, 2),
            total_invested=round(total_invested, 2),
            total_portfolio_value=round(portfolio.cash_available + total_invested, 2),
            unrealized_pl=round(unrealized, 2),
            realized_pl=round(realized, 2),
            total_pl=round(total_pl, 2),
            total_pl_pct=pl_pct,
            position_count=len(positions),
            positions=position_views,
            updated_at=portfolio.updated_at,
        )

    async def get_position(
        self,
        symbol: str,
        portfolio_id: int = DEFAULT_PORTFOLIO_ID,
    ) -> Optional[Position]:
        """Return the raw ORM Position or None."""
        async with get_session() as session:
            return await self._get_position(session, symbol.upper(), portfolio_id)

    async def get_trades(
        self,
        portfolio_id: int = DEFAULT_PORTFOLIO_ID,
        symbol: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[TradeView], int]:
        """
        Return (trades, total_count) for pagination.
        Sorted newest-first.
        """
        async with get_session() as session:
            q = select(Trade).where(Trade.portfolio_id == portfolio_id)
            if symbol:
                q = q.where(Trade.symbol == symbol.upper())

            count_q = select(func.count()).select_from(q.subquery())
            total   = (await session.execute(count_q)).scalar_one()

            q = q.order_by(Trade.executed_at.desc()).limit(limit).offset(offset)
            rows = (await session.execute(q)).scalars().all()

        return [_trade_to_view(t) for t in rows], total

    async def buying_power(
        self,
        symbol: str,
        current_price: float,
        portfolio_id: int = DEFAULT_PORTFOLIO_ID,
    ) -> BuyingPowerView:
        """
        How many shares can be bought with available cash?
        Accounts for brokerage fees so we don't overshoot.
        """
        async with get_session() as session:
            portfolio = await self._require_portfolio(session, portfolio_id)
            cash      = portfolio.cash_available

        # Binary-search the exact max shares we can afford including fee
        # (fee is applied on top of trade value, so solve: shares×price + fee(shares×price) ≤ cash)
        # Since fee(x) ≈ 0.162% × x + 10, the formula is:
        #   shares × price × (1 + commission + secp) + cdc ≤ cash
        # Rearranging:
        #   shares ≤ (cash - cdc) / (price × (1 + commission + secp))
        from .fees import _DEFAULT_COMMISSION_RATE, _DEFAULT_SECP_RATE, _DEFAULT_CDC_CHARGE
        fee_rate    = _DEFAULT_COMMISSION_RATE + _DEFAULT_SECP_RATE
        max_shares  = int((cash - _DEFAULT_CDC_CHARGE) / (current_price * (1 + fee_rate)))
        max_shares  = max(0, max_shares)
        fee_for_max = calculate_fee(max_shares * current_price).total if max_shares > 0 else 0.0
        cost        = round(max_shares * current_price + fee_for_max, 2)

        return BuyingPowerView(
            symbol=symbol.upper(),
            current_price=current_price,
            cash_available=round(cash, 2),
            shares_buyable=max_shares,
            estimated_cost=cost,
            fee_estimate=round(fee_for_max, 2),
        )

    # ──────────────────────────────────────────────────────────────────────
    # Portfolio mutation (cash + manual positions)
    # ──────────────────────────────────────────────────────────────────────

    async def set_cash(
        self,
        amount: float,
        portfolio_id: int = DEFAULT_PORTFOLIO_ID,
    ) -> None:
        """Set the cash balance. Replaces whatever was there."""
        if amount < 0:
            raise ValueError("Cash cannot be negative")
        async with get_session() as session:
            portfolio = await self._require_portfolio(session, portfolio_id)
            portfolio.cash_available = round(amount, 2)
            portfolio.updated_at     = int(_time.time())
        logger.info("Cash set to PKR %.2f (portfolio #%d)", amount, portfolio_id)

    async def add_position_manual(
        self,
        symbol:        str,
        shares:        float,
        avg_buy_price: float,
        notes:         Optional[str] = None,
        portfolio_id:  int = DEFAULT_PORTFOLIO_ID,
    ) -> Position:
        """
        Manually record a pre-existing holding.
        If position already exists: adds shares and recalculates avg price.
        Does NOT deduct cash — this is for recording holdings bought outside
        this system.
        """
        symbol = symbol.upper()
        async with get_session() as session:
            await self._require_portfolio(session, portfolio_id)
            existing = await self._get_position(session, symbol, portfolio_id)

            if existing:
                # Weighted average
                new_total         = existing.total_invested + (shares * avg_buy_price)
                new_shares        = existing.shares + shares
                existing.shares        = round(new_shares, 6)
                existing.avg_buy_price = round(new_total / new_shares, 4)
                existing.total_invested= round(new_total, 2)
                pos = existing
            else:
                pos = Position(
                    portfolio_id=portfolio_id,
                    symbol=symbol,
                    shares=round(shares, 6),
                    avg_buy_price=round(avg_buy_price, 4),
                    total_invested=round(shares * avg_buy_price, 2),
                    opened_at=int(_time.time()),
                    notes=notes,
                )
                session.add(pos)

        logger.info("Manual position: %s ×%.2f @ %.4f", symbol, shares, avg_buy_price)
        return pos

    async def remove_position_manual(
        self,
        symbol:       str,
        portfolio_id: int = DEFAULT_PORTFOLIO_ID,
    ) -> None:
        """Remove a position entirely (no trade record created)."""
        symbol = symbol.upper()
        async with get_session() as session:
            await session.execute(
                delete(Position).where(
                    Position.portfolio_id == portfolio_id,
                    Position.symbol       == symbol,
                )
            )
        logger.info("Manual removal: position %s deleted from portfolio #%d", symbol, portfolio_id)

    # ──────────────────────────────────────────────────────────────────────
    # Trade execution
    # ──────────────────────────────────────────────────────────────────────

    async def execute_buy(
        self,
        symbol:       str,
        shares:       float,
        price:        float,
        notes:        Optional[str] = None,
        signal_id:    Optional[int] = None,
        portfolio_id: int           = DEFAULT_PORTFOLIO_ID,
    ) -> TradeView:
        """
        Execute a BUY order.

        Validation:
          - shares > 0, price > 0  (enforced by Pydantic before reaching here)
          - cash >= total cost including fees
          - symbol is a non-empty string

        Side effects:
          - Deducts (shares × price + fee) from cash
          - Creates or updates Position (weighted avg cost basis)
          - Creates immutable Trade record

        Returns the completed TradeView.
        """
        symbol      = symbol.upper()
        trade_value = round(shares * price, 2)
        fee         = calculate_fee(trade_value)
        net_cost    = round(trade_value + fee.total, 2)   # what we pay out

        async with get_session() as session:
            portfolio = await self._require_portfolio(session, portfolio_id)

            # Cash sufficiency check
            if portfolio.cash_available < net_cost:
                raise InsufficientCashError(
                    f"Insufficient cash. Need PKR {net_cost:,.2f}, "
                    f"have PKR {portfolio.cash_available:,.2f}."
                )

            # Deduct cash
            portfolio.cash_available = round(portfolio.cash_available - net_cost, 2)
            portfolio.updated_at     = int(_time.time())

            # Create or update position
            existing = await self._get_position(session, symbol, portfolio_id)
            if existing:
                new_total               = existing.total_invested + trade_value
                new_shares              = existing.shares + shares
                existing.shares         = round(new_shares, 6)
                existing.avg_buy_price  = round(new_total / new_shares, 4)
                existing.total_invested = round(new_total, 2)
            else:
                session.add(Position(
                    portfolio_id=portfolio_id,
                    symbol=symbol,
                    shares=round(shares, 6),
                    avg_buy_price=round(price, 4),
                    total_invested=round(trade_value, 2),
                    opened_at=int(_time.time()),
                    notes=notes,
                ))

            # Record trade (immutable ledger)
            trade = Trade(
                portfolio_id=portfolio_id,
                symbol=symbol,
                trade_type="BUY",
                shares=round(shares, 6),
                price_per_share=round(price, 4),
                total_value=trade_value,
                brokerage_fee=fee.total,
                net_value=net_cost,
                realized_pl=None,          # BUY has no realized P&L
                signal_id=signal_id,
                executed_at=int(_time.time()),
                notes=notes,
            )
            session.add(trade)
            await session.flush()           # populate trade.id before returning
            trade_id = trade.id

        logger.info(
            "BUY  %s ×%.2f @ %.4f | fee: %.2f | net cost: %.2f | portfolio #%d",
            symbol, shares, price, fee.total, net_cost, portfolio_id,
        )

        # Re-fetch trade to get its DB-assigned id
        async with get_session() as session:
            trade_row = await session.get(Trade, trade_id)

        return _trade_to_view(trade_row, fee)

    async def execute_sell(
        self,
        symbol:       str,
        shares:       float,
        price:        float,
        notes:        Optional[str] = None,
        signal_id:    Optional[int] = None,
        portfolio_id: int           = DEFAULT_PORTFOLIO_ID,
        trade_type:   str           = "SELL",
    ) -> TradeView:
        """
        Execute a SELL (or FORCE_SELL) order.

        Validation:
          - Position must exist
          - position.shares >= shares_to_sell
          - price > 0

        P&L formula (pre-CGT):
          realized_pl = (price × shares − sell_fee) − (avg_buy_price × shares)
                      = net_proceeds − cost_basis

        Side effects:
          - Adds net proceeds to cash
          - Reduces or deletes Position
          - Creates immutable Trade record with realized_pl
        """
        symbol      = symbol.upper()
        trade_type  = trade_type.upper()
        if trade_type not in ("SELL", "FORCE_SELL"):
            raise ValueError(f"Invalid trade_type: {trade_type!r}")

        trade_value  = round(shares * price, 2)
        fee          = calculate_fee(trade_value)
        net_proceeds = round(trade_value - fee.total, 2)   # what we receive

        async with get_session() as session:
            portfolio = await self._require_portfolio(session, portfolio_id)
            position  = await self._get_position(session, symbol, portfolio_id)

            if position is None:
                raise PositionNotFoundError(
                    f"No open position found for '{symbol}' in portfolio #{portfolio_id}."
                )

            if position.shares < shares - 1e-9:     # tolerance for float rounding
                raise InsufficientSharesError(
                    f"Cannot sell {shares:.4f} shares of {symbol} — "
                    f"only {position.shares:.4f} held."
                )

            # Realized P&L: net proceeds minus cost basis for these shares
            cost_basis   = round(position.avg_buy_price * shares, 2)
            realized_pl  = round(net_proceeds - cost_basis, 2)

            # Add proceeds to cash
            portfolio.cash_available = round(portfolio.cash_available + net_proceeds, 2)
            portfolio.updated_at     = int(_time.time())

            # Reduce or delete position
            remaining = round(position.shares - shares, 6)
            if remaining <= 1e-9:
                # All shares sold — close the position
                await session.delete(position)
                logger.debug("Position %s closed (all shares sold)", symbol)
            else:
                # Partial sell — avg_buy_price stays the same (FIFO-equivalent for avg method)
                position.shares         = remaining
                position.total_invested = round(remaining * position.avg_buy_price, 2)

            # Record trade
            trade = Trade(
                portfolio_id=portfolio_id,
                symbol=symbol,
                trade_type=trade_type,
                shares=round(shares, 6),
                price_per_share=round(price, 4),
                total_value=trade_value,
                brokerage_fee=fee.total,
                net_value=net_proceeds,
                realized_pl=realized_pl,
                signal_id=signal_id,
                executed_at=int(_time.time()),
                notes=notes,
            )
            session.add(trade)
            await session.flush()
            trade_id = trade.id

        logger.info(
            "%s %s ×%.2f @ %.4f | fee: %.2f | net: %.2f | realized P&L: %.2f | portfolio #%d",
            trade_type, symbol, shares, price, fee.total,
            net_proceeds, realized_pl, portfolio_id,
        )

        async with get_session() as session:
            trade_row = await session.get(Trade, trade_id)

        return _trade_to_view(trade_row, fee)

    # ──────────────────────────────────────────────────────────────────────
    # Snapshot
    # ──────────────────────────────────────────────────────────────────────

    async def take_snapshot(
        self,
        current_prices: dict[str, float],
        portfolio_id:   int = DEFAULT_PORTFOLIO_ID,
    ) -> None:
        """
        Persist a point-in-time portfolio snapshot for P&L charting.
        Called periodically (e.g. every market close).
        """
        summary = await self.get_portfolio(current_prices, portfolio_id)

        async with get_session() as session:
            snap = PortfolioSnapshot(
                portfolio_id=portfolio_id,
                total_value=summary.total_portfolio_value,
                cash=summary.cash_available,
                invested_value=summary.total_invested,
                total_pl=summary.total_pl,
                unrealized_pl=summary.unrealized_pl,
                realized_pl=summary.realized_pl,
                snapshotted_at=int(_time.time()),
            )
            session.add(snap)

        logger.info(
            "Snapshot saved: total=PKR %.2f pl=PKR %.2f (portfolio #%d)",
            summary.total_portfolio_value, summary.total_pl, portfolio_id,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    async def _require_portfolio(session: AsyncSession, portfolio_id: int) -> Portfolio:
        portfolio = await session.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise PortfolioNotFoundError(
                f"Portfolio #{portfolio_id} not found. "
                f"Call ensure_default_portfolio() on startup."
            )
        return portfolio

    @staticmethod
    async def _get_positions(session: AsyncSession, portfolio_id: int) -> list[Position]:
        q   = select(Position).where(Position.portfolio_id == portfolio_id)
        res = await session.execute(q)
        return list(res.scalars().all())

    @staticmethod
    async def _get_position(
        session: AsyncSession, symbol: str, portfolio_id: int
    ) -> Optional[Position]:
        q   = select(Position).where(
            Position.portfolio_id == portfolio_id,
            Position.symbol       == symbol,
        )
        res = await session.execute(q)
        return res.scalar_one_or_none()

    @staticmethod
    async def _total_realized_pl(session: AsyncSession, portfolio_id: int) -> float:
        """Sum of all realized_pl across SELL/FORCE_SELL trades."""
        q   = select(func.coalesce(func.sum(Trade.realized_pl), 0.0)).where(
            Trade.portfolio_id == portfolio_id,
            Trade.realized_pl.isnot(None),
        )
        res = await session.execute(q)
        return float(res.scalar_one())

    @staticmethod
    def _enrich_positions(
        positions: list[Position],
        current_prices: dict[str, float],
    ) -> tuple[list[PositionView], float, float]:
        """
        Attach live prices and compute unrealized P&L.

        Returns:
          (list[PositionView], total_market_value, total_unrealized_pl)
        """
        views: list[PositionView] = []
        total_value  = 0.0
        total_unreal = 0.0

        for pos in positions:
            cur_price = current_prices.get(pos.symbol)
            cur_value = round(cur_price * pos.shares, 2) if cur_price else None
            unreal    = round(cur_value - pos.total_invested, 2) if cur_value is not None else None
            unreal_pct = (
                round(unreal / pos.total_invested * 100, 2)
                if unreal is not None and pos.total_invested > 0 else None
            )

            # Breakeven = avg_buy_price × (1 + round-trip fee ≈ 0.323% + PKR 20/trade)
            # Approximate: use fixed 0.35% round-trip as simple scalar
            breakeven = round(pos.avg_buy_price * 1.0035, 4)

            if cur_value:
                total_value  += cur_value
            else:
                total_value  += pos.total_invested   # fallback to cost basis if no price
            if unreal is not None:
                total_unreal += unreal

            views.append(PositionView(
                symbol=pos.symbol,
                shares=pos.shares,
                avg_buy_price=pos.avg_buy_price,
                total_invested=pos.total_invested,
                current_price=cur_price,
                current_value=cur_value,
                unrealized_pl=unreal,
                unrealized_pl_pct=unreal_pct,
                breakeven_price=breakeven,
                opened_at=pos.opened_at,
                notes=pos.notes,
            ))

        return views, round(total_value, 2), round(total_unreal, 2)


# ──────────────────────────────────────────────────────────────────────────────
# Domain exceptions (raised by manager, caught by route handlers)
# ──────────────────────────────────────────────────────────────────────────────

class PortfolioError(Exception):
    """Base class for portfolio domain errors."""

class PortfolioNotFoundError(PortfolioError):
    pass

class InsufficientCashError(PortfolioError):
    pass

class InsufficientSharesError(PortfolioError):
    pass

class PositionNotFoundError(PortfolioError):
    pass


# ──────────────────────────────────────────────────────────────────────────────
# Serialization helpers
# ──────────────────────────────────────────────────────────────────────────────

def _trade_to_view(trade: Trade, fee: Optional[FeeBreakdown] = None) -> TradeView:
    fee_view = FeeView(
        commission=fee.commission if fee else 0.0,
        cdc=fee.cdc if fee else 0.0,
        secp=fee.secp if fee else 0.0,
        total=fee.total if fee else trade.brokerage_fee,
    )
    return TradeView(
        id=trade.id,
        symbol=trade.symbol,
        trade_type=trade.trade_type,
        shares=trade.shares,
        price_per_share=trade.price_per_share,
        total_value=trade.total_value,
        brokerage_fee=trade.brokerage_fee,
        net_value=trade.net_value,
        realized_pl=trade.realized_pl,
        executed_at=trade.executed_at,
        notes=trade.notes,
        fees=fee_view,
    )
