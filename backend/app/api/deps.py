"""
FastAPI dependencies — shared across route modules.

require_market_open
-------------------
Dependency that enforces PSX trading hours on all trade-execution endpoints.
Raises HTTP 423 Locked when:
  (a) the market is not in OPEN state, OR
  (b) data_stale is True (prices are from snapshot — unsafe to trade on)

require_trade_rate_limit
------------------------
Sliding-window in-memory rate limiter for trade endpoints.
Allows max 5 trades per 60-second window per portfolio.
Raises HTTP 429 Too Many Requests when the limit is exceeded.

Usage:
    from .deps import require_market_open, require_trade_rate_limit

    @router.post("/trades/buy")
    async def buy(
        payload: BuyRequest,
        _market: None = Depends(require_market_open),
        _rate:   None = Depends(require_trade_rate_limit),
    ):
        ...

The 423 response body is structured so the frontend can display a clear
banner rather than a generic error:
    {
      "detail": {
        "code":        "MARKET_CLOSED",
        "message":     "Trade execution is disabled — market is post_market. ...",
        "market_state": "post_market",
        "phase_label":  "Post-Market",
        "next_open":    "Wed 29 Apr at 09:30 PKT",
        "seconds_to_open": 57397,
        "stale":        false
      }
    }
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request

from ..market_hours import market_status, MarketState
from ..state import app_state

# ---------------------------------------------------------------------------
# Rate-limiter state (module-level, lives for the process lifetime)
# ---------------------------------------------------------------------------

_TRADE_LIMIT     = 5    # max trades per window
_TRADE_WINDOW    = 60   # seconds

# portfolio_id → deque of Unix timestamps for recent trade attempts
_trade_timestamps: dict[int, deque[float]] = defaultdict(deque)


class MarketClosedError(HTTPException):
    """
    HTTP 423 Locked — resource temporarily unavailable due to market state.
    423 is semantically correct: the resource exists but is locked by an
    external condition (market hours), not by an auth or logic error.
    """

    def __init__(self, detail: dict) -> None:
        super().__init__(status_code=423, detail=detail)


async def require_market_open() -> None:
    """
    FastAPI dependency — call as `Depends(require_market_open)`.

    Raises MarketClosedError (HTTP 423) if:
      - Market state is not OPEN (pre-market, post-market, or weekend/closed)
      - OR current data is stale (snapshot prices — not safe to trade on)

    Does nothing when market is open and data is live.
    """
    status = market_status()

    # Stale data check runs first — even if market is open (e.g. scrape lag)
    if app_state.data_stale:
        raise MarketClosedError({
            "code":           "STALE_DATA",
            "message":        (
                "Trade execution is disabled — current prices are from a snapshot, "
                "not a live scrape. Trades cannot be safely executed on stale data."
            ),
            "market_state":   status.state.value,
            "phase_label":    status.phase_label,
            "next_open":      status.next_open.strftime("%a %d %b at %H:%M PKT"),
            "seconds_to_open": status.seconds_to_open,
            "stale":          True,
            "stale_reason":   app_state.stale_reason,
        })

    # Market hours check
    if not status.is_open:
        raise MarketClosedError({
            "code":           "MARKET_CLOSED",
            "message":        (
                f"Trade execution is disabled — market is {status.phase_label.lower()}. "
                f"PSX trading hours: 09:30–15:30 PKT, Monday–Friday. "
                f"Next open: {status.next_open.strftime('%a %d %b at %H:%M PKT')}."
            ),
            "market_state":   status.state.value,
            "phase_label":    status.phase_label,
            "next_open":      status.next_open.strftime("%a %d %b at %H:%M PKT"),
            "seconds_to_open": status.seconds_to_open,
            "stale":          False,
            "stale_reason":   None,
        })


# ---------------------------------------------------------------------------
# Trade rate limiter
# ---------------------------------------------------------------------------

async def require_trade_rate_limit(
    portfolio_id: int = 1,
) -> None:
    """
    FastAPI dependency — sliding-window rate limiter for trade endpoints.

    Allows at most TRADE_LIMIT (5) trades within any rolling TRADE_WINDOW (60s)
    window per portfolio.  The window is per-process (in-memory); it resets on
    server restart, which is intentional — a restart should not inherit
    accumulated trade debt.

    Raises HTTP 429 Too Many Requests with a Retry-After header when the limit
    is exceeded.  The detail body mirrors the 423 structure for frontend parity:
        {
          "code":        "RATE_LIMITED",
          "message":     "...",
          "retry_after": 23          # seconds until oldest timestamp expires
        }
    """
    now = time.monotonic()
    window_start = now - _TRADE_WINDOW
    bucket: deque[float] = _trade_timestamps[portfolio_id]

    # Evict timestamps outside the rolling window
    while bucket and bucket[0] <= window_start:
        bucket.popleft()

    if len(bucket) >= _TRADE_LIMIT:
        # How long until the oldest trade in the window expires
        retry_after = int(bucket[0] - window_start) + 1
        raise HTTPException(
            status_code=429,
            detail={
                "code":        "RATE_LIMITED",
                "message":     (
                    f"Trade rate limit reached: {_TRADE_LIMIT} trades per "
                    f"{_TRADE_WINDOW}s per portfolio. "
                    f"Retry in {retry_after}s."
                ),
                "retry_after": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )

    # Record this trade attempt (committed after dependency clears)
    bucket.append(now)


def record_trade(portfolio_id: int = 1) -> None:
    """
    Call this AFTER a trade is successfully committed to DB to stamp the
    timestamp.  If you use require_trade_rate_limit as a Depends(), it already
    stamps on entry — call record_trade() instead if you prefer post-commit
    stamping (more accurate for retries).

    For simplicity the dependency stamps on entry (pessimistic); this helper
    exists for future use if the approach changes.
    """
    _trade_timestamps[portfolio_id].append(time.monotonic())
