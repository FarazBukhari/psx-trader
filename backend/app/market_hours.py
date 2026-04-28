"""
PSX Market Hours — Pakistan Stock Exchange
Single source of truth for every market-open check in the system.

Session map (Mon–Fri, PKT = UTC+5):
  00:00 – 09:29  →  PRE_MARKET   (no trading; includes PSX pre-open queue 09:15–09:30)
  09:30 – 15:30  →  OPEN         (live execution window — signals are actionable)
  15:31 – 23:59  →  POST_MARKET  (session ended; last prices are final for the day)
  Sat / Sun      →  CLOSED       (full-day weekend closure)

Trade execution is only permitted during OPEN.
All other states → data is stale, signals are informational only.

No third-party dependencies — stdlib datetime only.
"""

from __future__ import annotations

import math
from datetime import datetime, time, timezone, timedelta
from enum import Enum
from typing import NamedTuple, Optional

# ---------------------------------------------------------------------------
# Pakistan Standard Time — UTC+5, no DST
# ---------------------------------------------------------------------------
PKT = timezone(timedelta(hours=5), name="PKT")

# Canonical session boundaries
_SESSION_OPEN  = time(9, 30)
_SESSION_CLOSE = time(15, 30)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class MarketState(str, Enum):
    OPEN         = "open"          # 09:30–15:30, Mon–Fri
    PRE_MARKET   = "pre_market"    # 00:00–09:29, Mon–Fri  (was PRE_OPEN + early CLOSED)
    POST_MARKET  = "post_market"   # 15:31–23:59, Mon–Fri
    CLOSED       = "closed"        # Saturday / Sunday (full-day closure)

    # Backward-compat alias — kept so existing code using PRE_OPEN doesn't break
    PRE_OPEN = "pre_market"


# Human-readable badge label for each state
_PHASE_LABELS: dict[MarketState, str] = {
    MarketState.OPEN:        "Open",
    MarketState.PRE_MARKET:  "Pre-Market",
    MarketState.POST_MARKET: "Post-Market",
    MarketState.CLOSED:      "Closed",
}


class MarketStatus(NamedTuple):
    state:              MarketState
    is_open:            bool            # True ONLY for MarketState.OPEN
    reason:             str             # human-readable for logs / UI banners
    phase_label:        str             # short badge label: "Open", "Pre-Market", etc.
    pkt_now:            datetime        # current wall-clock in PKT
    next_open:          datetime        # next 09:30 PKT on a business day
    seconds_to_open:    Optional[int]   # None while market is open
    seconds_to_close:   Optional[int]   # None while market is closed / not open


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------

def market_status(now: datetime | None = None) -> MarketStatus:
    """
    Return the full market status for the given instant.

    Args:
        now: UTC datetime to evaluate against. Defaults to utcnow().
             Always pass timezone-aware datetimes in tests.

    Holiday note:
        PSX has ~15 public holidays/year. We don't embed a static calendar
        because it changes annually and the source of truth is the PSX website.
        On a holiday the live scrape will fail → snapshot fallback kicks in →
        data is automatically marked stale. No special handling needed here.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    pkt_now  = now.astimezone(PKT)
    weekday  = pkt_now.weekday()        # 0=Mon … 4=Fri, 5=Sat, 6=Sun
    pkt_time = pkt_now.time().replace(microsecond=0)

    # ── Weekend ──────────────────────────────────────────────────────────────
    if weekday >= 5:
        next_open = _next_business_open(pkt_now)
        secs_to_open = _seconds_between(pkt_now, next_open)
        day_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][weekday]
        return MarketStatus(
            state=MarketState.CLOSED,
            is_open=False,
            reason=f"Weekend ({day_name}) — market reopens {next_open.strftime('%a %d %b at %H:%M PKT')}",
            phase_label=_PHASE_LABELS[MarketState.CLOSED],
            pkt_now=pkt_now,
            next_open=next_open,
            seconds_to_open=secs_to_open,
            seconds_to_close=None,
        )

    # ── Weekday ───────────────────────────────────────────────────────────────
    session_open_today  = pkt_now.replace(hour=9,  minute=30, second=0, microsecond=0)
    session_close_today = pkt_now.replace(hour=15, minute=30, second=0, microsecond=0)

    # PRE_MARKET: 00:00 – 09:29
    if pkt_time < _SESSION_OPEN:
        secs_to_open = _seconds_between(pkt_now, session_open_today)
        return MarketStatus(
            state=MarketState.PRE_MARKET,
            is_open=False,
            reason=f"Pre-market — session opens at 09:30 PKT (in {_fmt_duration(secs_to_open)})",
            phase_label=_PHASE_LABELS[MarketState.PRE_MARKET],
            pkt_now=pkt_now,
            next_open=session_open_today,
            seconds_to_open=secs_to_open,
            seconds_to_close=None,
        )

    # OPEN: 09:30 – 15:30
    if pkt_time <= _SESSION_CLOSE:
        next_open  = _next_business_open(pkt_now, skip_today=True)
        secs_close = _seconds_between(pkt_now, session_close_today)
        return MarketStatus(
            state=MarketState.OPEN,
            is_open=True,
            reason=f"Market open — closes at 15:30 PKT (in {_fmt_duration(secs_close)})",
            phase_label=_PHASE_LABELS[MarketState.OPEN],
            pkt_now=pkt_now,
            next_open=next_open,
            seconds_to_open=None,
            seconds_to_close=secs_close,
        )

    # POST_MARKET: 15:31 – 23:59
    next_open    = _next_business_open(pkt_now, skip_today=True)
    secs_to_open = _seconds_between(pkt_now, next_open)
    return MarketStatus(
        state=MarketState.POST_MARKET,
        is_open=False,
        reason=f"Post-market — session ended at 15:30 PKT. Next open: {next_open.strftime('%a %d %b %H:%M PKT')} (in {_fmt_duration(secs_to_open)})",
        phase_label=_PHASE_LABELS[MarketState.POST_MARKET],
        pkt_now=pkt_now,
        next_open=next_open,
        seconds_to_open=secs_to_open,
        seconds_to_close=None,
    )


def is_market_open(now: datetime | None = None) -> bool:
    """Convenience wrapper — True only during the live 09:30–15:30 session."""
    return market_status(now).is_open


def trading_disabled_reason(now: datetime | None = None) -> Optional[str]:
    """
    Return a human-readable reason why trading is disabled, or None if open.
    Used by the trade execution guard to populate error responses.
    """
    status = market_status(now)
    if status.is_open:
        return None
    return (
        f"Trade execution is disabled — market is {status.phase_label.lower()}. "
        f"PSX trading hours: 09:30–15:30 PKT, Monday–Friday. "
        f"Next open: {status.next_open.strftime('%a %d %b at %H:%M PKT')}."
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _next_business_open(pkt_now: datetime, skip_today: bool = False) -> datetime:
    """Return the next 09:30 PKT on a Monday–Friday."""
    base = pkt_now.replace(hour=9, minute=30, second=0, microsecond=0)
    days = 1 if skip_today else 0
    while True:
        candidate = base + timedelta(days=days)
        if candidate.weekday() < 5:
            return candidate
        days += 1


def _seconds_between(start: datetime, end: datetime) -> int:
    """Positive integer seconds between two datetimes. Clamps to 0 if past."""
    delta = (end - start).total_seconds()
    return max(0, int(delta))


def _fmt_duration(seconds: int) -> str:
    """Format seconds into 'Xh Ym' or 'Ym Zs' for human display."""
    h, rem = divmod(seconds, 3600)
    m, s   = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"
