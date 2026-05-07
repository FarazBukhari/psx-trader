"""
PSX Brokerage Fee Calculator — Finqalab.

Fee structure confirmed from Finqalab cashbook (07/05/2026):
  - Brokerage (BRK): 0.25% of trade value — only active charge
  - CVT:             0.00  (Capital Value Tax — currently zero)
  - WHT:             0.00  (Withholding Tax — currently zero)
  - FED:             0.00  (Federal Excise Duty — currently zero)
  - CDC flat charge: 0.00  (not charged by Finqalab)
  - SECP levy:       0.00  (not charged by Finqalab)

Total per-side:   0.25% of trade value
Round-trip cost:  0.50% of trade value

Verification (from cashbook):
  SSGC BUY  209 × 28.72  = 6002.48 → BRK 15.01  (×0.0025 = 15.006 ✓)
  SSGC BUY   10 × 28.69  =  286.90 → BRK  0.72  (×0.0025 =  0.717 ✓)
  HASCOL SELL 47 × 22.92 = 1077.24 → BRK  2.69  (×0.0025 =  2.693 ✓)

Note: CGT applies on profitable sells but is handled at year-end by the broker.
P&L shown in this system is PRE-CGT.

Rates are configurable via strategy.json:
  {
    "global": {
      "commission_rate": 0.0025,
      "cdc_charge": 0.0,
      "secp_rate": 0.0
    }
  }
"""

from __future__ import annotations

from dataclasses import dataclass

# Finqalab confirmed rates (cashbook 07/05/2026)
_DEFAULT_COMMISSION_RATE = 0.0025     # 0.25% — sole active charge
_DEFAULT_CDC_CHARGE      = 0.0        # not charged by Finqalab
_DEFAULT_SECP_RATE       = 0.0        # not charged by Finqalab


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
