"""
PSX Brokerage Fee Calculator.

Fee structure (Pakistan Stock Exchange, discount broker):
  - Commission:   0.15% of trade value  (TREC holder fee)
  - CDC charge:   PKR 10.00 flat        (Central Depository Company)
  - SECP levy:    0.0115% of trade value (Securities & Exchange Commission)

Total per-side:   ~0.1615% + PKR 10
Round-trip cost:  ~0.323% + PKR 20

Note: Capital Gains Tax (CGT) applies on profitable sells, but is calculated
at year-end by the broker and deducted from the account. We do not compute it
here because it depends on holding period (FIFO) and annual income bracket.
The P&L shown in this system is PRE-CGT.

All rates are configurable via strategy.json:
  {
    "global": {
      "commission_rate": 0.0015,
      "cdc_charge": 10.0,
      "secp_rate": 0.000115
    }
  }
"""

from __future__ import annotations

from dataclasses import dataclass

# Default rates — overridable via load_fee_config()
_DEFAULT_COMMISSION_RATE = 0.0015     # 0.15%
_DEFAULT_CDC_CHARGE      = 10.0       # PKR 10 flat
_DEFAULT_SECP_RATE       = 0.000115   # 0.0115%


@dataclass(frozen=True)
class FeeBreakdown:
    commission:   float   # TREC holder commission
    cdc:          float   # CDC flat charge
    secp:         float   # SECP levy
    total:        float   # sum of above

    def __str__(self) -> str:
        return (
            f"Commission: PKR {self.commission:.2f} | "
            f"CDC: PKR {self.cdc:.2f} | "
            f"SECP: PKR {self.secp:.2f} | "
            f"Total: PKR {self.total:.2f}"
        )


def calculate_fee(
    trade_value: float,
    commission_rate: float = _DEFAULT_COMMISSION_RATE,
    cdc_charge:      float = _DEFAULT_CDC_CHARGE,
    secp_rate:       float = _DEFAULT_SECP_RATE,
) -> FeeBreakdown:
    """
    Calculate PSX brokerage fees for a single-side trade.

    Args:
        trade_value:     gross trade value in PKR (shares × price)
        commission_rate: broker commission as a decimal (default 0.0015)
        cdc_charge:      CDC flat fee in PKR (default 10.0)
        secp_rate:       SECP levy as a decimal (default 0.000115)

    Returns:
        FeeBreakdown with commission, cdc, secp, and total.
    """
    if trade_value <= 0:
        return FeeBreakdown(0.0, 0.0, 0.0, 0.0)

    commission = round(trade_value * commission_rate, 2)
    cdc        = round(cdc_charge, 2)
    secp       = round(trade_value * secp_rate, 2)
    total      = round(commission + cdc + secp, 2)

    return FeeBreakdown(commission=commission, cdc=cdc, secp=secp, total=total)


def fee_from_config(trade_value: float, config: dict) -> FeeBreakdown:
    """
    Calculate fees using rates from strategy.json global config block.

    config example:
        {"commission_rate": 0.0015, "cdc_charge": 10.0, "secp_rate": 0.000115}
    """
    g = config.get("global", {})
    return calculate_fee(
        trade_value,
        commission_rate=g.get("commission_rate", _DEFAULT_COMMISSION_RATE),
        cdc_charge=     g.get("cdc_charge",      _DEFAULT_CDC_CHARGE),
        secp_rate=      g.get("secp_rate",        _DEFAULT_SECP_RATE),
    )
