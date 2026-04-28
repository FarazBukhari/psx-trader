"""
End-to-end portfolio test.

Flow:
  1. Set cash to PKR 100,000
  2. BUY 100 × ENGRO @ 285.00
  3. BUY 50 more × ENGRO @ 295.00  → verify weighted avg price
  4. Partial SELL 80 × ENGRO @ 310.00 → verify realized P&L
  5. Full SELL remaining 70 × ENGRO @ 320.00 → verify position closed
  6. Verify final cash balance
  7. Verify total realized P&L
  8. Verify portfolio snapshot creation
  9. Error cases: sell more than held, buy with insufficient cash

All assertions use exact PKR arithmetic.
"""

from __future__ import annotations

import pytest

from app.portfolio.portfolio_manager import (
    PortfolioManager,
    InsufficientCashError,
    InsufficientSharesError,
    PositionNotFoundError,
)
from app.portfolio.fees import calculate_fee

# pm fixture is provided by conftest.py — fresh isolated DB per test


# ── Helpers ─────────────────────────────────────────────────────────────────

def fee(value: float) -> float:
    return calculate_fee(value).total


# ── Tests ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_cash(pm: PortfolioManager):
    await pm.set_cash(100_000.0)
    summary = await pm.get_portfolio({})
    assert summary.cash_available == 100_000.0, f"Expected 100000, got {summary.cash_available}"


@pytest.mark.asyncio
async def test_buy_creates_position(pm: PortfolioManager):
    await pm.set_cash(100_000.0)

    # BUY 100 × ENGRO @ 285.00
    trade_value_1 = 100 * 285.00          # 28,500
    fee_1         = fee(trade_value_1)    # ~46.08
    net_cost_1    = round(trade_value_1 + fee_1, 2)

    trade = await pm.execute_buy("ENGRO", 100, 285.00)

    assert trade.symbol        == "ENGRO"
    assert trade.trade_type    == "BUY"
    assert trade.shares        == 100.0
    assert trade.price_per_share == 285.00
    assert trade.total_value   == trade_value_1
    assert abs(trade.brokerage_fee - fee_1) < 0.01
    assert abs(trade.net_value - net_cost_1) < 0.01
    assert trade.realized_pl   is None

    # Verify cash deducted
    summary = await pm.get_portfolio({})
    expected_cash = round(100_000.0 - net_cost_1, 2)
    assert abs(summary.cash_available - expected_cash) < 0.01, \
        f"Cash mismatch: expected {expected_cash}, got {summary.cash_available}"

    # Verify position created
    assert summary.position_count == 1
    pos = summary.positions[0]
    assert pos.symbol        == "ENGRO"
    assert pos.shares        == 100.0
    assert pos.avg_buy_price == 285.00


@pytest.mark.asyncio
async def test_buy_more_weighted_average(pm: PortfolioManager):
    """Second buy must recalculate weighted average cost basis."""
    await pm.set_cash(200_000.0)

    # BUY 100 @ 285.00
    await pm.execute_buy("ENGRO", 100, 285.00)

    # BUY 50 more @ 295.00
    await pm.execute_buy("ENGRO", 50, 295.00)

    summary = await pm.get_portfolio({})
    pos = summary.positions[0]

    # Weighted avg = (100×285 + 50×295) / 150 = (28500 + 14750) / 150 = 43250 / 150 ≈ 288.3333
    expected_avg = round((100 * 285.0 + 50 * 295.0) / 150, 4)
    assert pos.shares == 150.0, f"Expected 150 shares, got {pos.shares}"
    assert abs(pos.avg_buy_price - expected_avg) < 0.01, \
        f"Avg price mismatch: expected {expected_avg}, got {pos.avg_buy_price}"


@pytest.mark.asyncio
async def test_partial_sell_realized_pl(pm: PortfolioManager):
    """
    Partial sell: 80 shares of 150.
    Realized P&L = net_proceeds − cost_basis
    """
    await pm.set_cash(200_000.0)
    await pm.execute_buy("ENGRO", 100, 285.00)
    await pm.execute_buy("ENGRO", 50, 295.00)

    # SELL 80 @ 310.00
    sell_value    = 80 * 310.00           # 24,800
    sell_fee      = fee(sell_value)       # ~40.07
    net_proceeds  = round(sell_value - sell_fee, 2)

    # Cost basis at weighted avg
    avg_price     = round((100 * 285.0 + 50 * 295.0) / 150, 4)
    cost_basis    = round(avg_price * 80, 2)
    expected_pl   = round(net_proceeds - cost_basis, 2)

    trade = await pm.execute_sell("ENGRO", 80, 310.00)

    assert trade.trade_type == "SELL"
    assert trade.shares     == 80.0
    assert abs(trade.realized_pl - expected_pl) < 0.02, \
        f"Realized P&L mismatch: expected {expected_pl}, got {trade.realized_pl}"

    # Position should still exist with 70 shares remaining
    summary = await pm.get_portfolio({})
    assert summary.position_count == 1
    pos = summary.positions[0]
    assert abs(pos.shares - 70.0) < 1e-6, f"Expected 70 shares, got {pos.shares}"


@pytest.mark.asyncio
async def test_full_sell_closes_position(pm: PortfolioManager):
    """Selling all remaining shares must remove the position."""
    await pm.set_cash(200_000.0)
    await pm.execute_buy("ENGRO", 100, 285.00)
    await pm.execute_buy("ENGRO", 50, 295.00)
    await pm.execute_sell("ENGRO", 80, 310.00)

    # Sell remaining 70 @ 320.00
    trade = await pm.execute_sell("ENGRO", 70, 320.00)
    assert trade.trade_type == "SELL"
    assert trade.shares     == 70.0

    # Position should be gone
    summary = await pm.get_portfolio({})
    assert summary.position_count == 0, f"Expected 0 positions, got {summary.position_count}"


@pytest.mark.asyncio
async def test_cash_balance_after_round_trip(pm: PortfolioManager):
    """
    After a complete buy-in / sell-out cycle the cash should equal:
      starting_cash − buy_fees_total + sell_pl
    (i.e. no phantom cash created/lost).
    """
    start_cash = 200_000.0
    await pm.set_cash(start_cash)

    t1 = await pm.execute_buy("ENGRO", 100, 285.00)
    t2 = await pm.execute_buy("ENGRO", 50,  295.00)
    t3 = await pm.execute_sell("ENGRO", 80,  310.00)
    t4 = await pm.execute_sell("ENGRO", 70,  320.00)

    # Expected cash = start − buy1 net − buy2 net + sell1 net + sell2 net
    expected_cash = round(
        start_cash
        - t1.net_value    # cost of buy 1
        - t2.net_value    # cost of buy 2
        + t3.net_value    # proceeds of sell 1
        + t4.net_value,   # proceeds of sell 2
        2,
    )

    summary = await pm.get_portfolio({})
    assert abs(summary.cash_available - expected_cash) < 0.02, \
        f"Cash mismatch: expected {expected_cash}, got {summary.cash_available}"


@pytest.mark.asyncio
async def test_realized_pl_total(pm: PortfolioManager):
    """Total realized P&L in the portfolio summary must equal sum of individual trades."""
    await pm.set_cash(200_000.0)
    await pm.execute_buy("ENGRO",  100, 285.00)
    await pm.execute_buy("ENGRO",  50,  295.00)
    t3 = await pm.execute_sell("ENGRO", 80,  310.00)
    t4 = await pm.execute_sell("ENGRO", 70,  320.00)

    expected_realized = round((t3.realized_pl or 0) + (t4.realized_pl or 0), 2)
    summary = await pm.get_portfolio({})
    assert abs(summary.realized_pl - expected_realized) < 0.02, \
        f"Realized P&L mismatch: expected {expected_realized}, got {summary.realized_pl}"


@pytest.mark.asyncio
async def test_unrealized_pl_with_live_price(pm: PortfolioManager):
    """Unrealized P&L is computed from live prices, not stored."""
    await pm.set_cash(100_000.0)
    await pm.execute_buy("ENGRO", 100, 285.00)

    live_prices   = {"ENGRO": 300.00}
    summary       = await pm.get_portfolio(live_prices)
    pos           = summary.positions[0]

    expected_unrealized = round(100 * 300.00 - 100 * 285.00, 2)  # 1,500
    assert pos.current_price == 300.00
    assert abs(pos.unrealized_pl - expected_unrealized) < 0.02


@pytest.mark.asyncio
async def test_insufficient_cash_raises(pm: PortfolioManager):
    """Buying beyond cash balance must raise InsufficientCashError."""
    await pm.set_cash(1_000.0)  # only 1,000 PKR

    with pytest.raises(InsufficientCashError):
        await pm.execute_buy("ENGRO", 100, 285.00)  # needs ~28,546 PKR


@pytest.mark.asyncio
async def test_sell_more_than_held_raises(pm: PortfolioManager):
    """Selling more shares than held must raise InsufficientSharesError."""
    await pm.set_cash(100_000.0)
    await pm.execute_buy("ENGRO", 50, 285.00)

    with pytest.raises(InsufficientSharesError):
        await pm.execute_sell("ENGRO", 100, 310.00)


@pytest.mark.asyncio
async def test_sell_nonexistent_position_raises(pm: PortfolioManager):
    """Selling a symbol with no position must raise PositionNotFoundError."""
    await pm.set_cash(100_000.0)

    with pytest.raises(PositionNotFoundError):
        await pm.execute_sell("LUCK", 50, 310.00)


@pytest.mark.asyncio
async def test_trade_history_recorded(pm: PortfolioManager):
    """All executed trades must appear in get_trades()."""
    await pm.set_cash(200_000.0)
    await pm.execute_buy("ENGRO",  100, 285.00)
    await pm.execute_buy("ENGRO",  50,  295.00)
    await pm.execute_sell("ENGRO", 80,  310.00)

    trades, total = await pm.get_trades()
    assert total == 3, f"Expected 3 trades, got {total}"
    # Newest-first: last executed is SELL
    assert trades[0].trade_type == "SELL"
    assert trades[1].trade_type == "BUY"
    assert trades[2].trade_type == "BUY"


@pytest.mark.asyncio
async def test_trade_history_filtered_by_symbol(pm: PortfolioManager):
    """get_trades(symbol=...) must only return that symbol's trades."""
    await pm.set_cash(200_000.0)
    await pm.execute_buy("ENGRO", 100, 285.00)
    await pm.execute_buy("LUCK",  50,  110.00)

    engro_trades, total = await pm.get_trades(symbol="ENGRO")
    assert total == 1
    assert engro_trades[0].symbol == "ENGRO"


@pytest.mark.asyncio
async def test_portfolio_snapshot(pm: PortfolioManager):
    """take_snapshot() must not raise and portfolio must still be intact afterward."""
    await pm.set_cash(100_000.0)
    await pm.execute_buy("ENGRO", 100, 285.00)

    prices = {"ENGRO": 290.00}
    await pm.take_snapshot(prices)   # should not raise

    summary = await pm.get_portfolio(prices)
    assert summary.position_count == 1


@pytest.mark.asyncio
async def test_manual_position_add_and_remove(pm: PortfolioManager):
    """Manual position recording bypasses cash; removal erases without trade."""
    await pm.set_cash(50_000.0)

    await pm.add_position_manual("MARI", 200, 1_500.00)
    summary = await pm.get_portfolio({})
    assert summary.position_count == 1
    assert summary.positions[0].symbol == "MARI"
    # Cash must NOT change
    assert summary.cash_available == 50_000.0

    await pm.remove_position_manual("MARI")
    summary = await pm.get_portfolio({})
    assert summary.position_count == 0

    # No trade records created
    _, total = await pm.get_trades()
    assert total == 0


@pytest.mark.asyncio
async def test_buying_power(pm: PortfolioManager):
    """buying_power() must return <= floor(cash / (price × (1+fee_rate)))."""
    await pm.set_cash(100_000.0)
    bp = await pm.buying_power("ENGRO", 285.00)

    assert bp.symbol == "ENGRO"
    assert bp.current_price == 285.00
    assert bp.cash_available == 100_000.0
    assert bp.shares_buyable > 0
    # Estimated cost must not exceed available cash
    assert bp.estimated_cost <= 100_000.0 + 0.02   # tiny float tolerance
