"""
Data Quality Monitor — detects anomalies in raw scraper output per poll tick.

Three detection rules (pure functions, O(n) over symbols):

  1. price_jump      |change_pct| > 7.5 %
                     PSX circuit-breaker fires at 7.5 % — anything beyond that
                     is either a legitimate extreme move or a data error. Either
                     way it warrants a flag before signal generation sees it.
                     Severity: always HIGH.

  2. volume_anomaly  volume > 3 × per-symbol 20-day rolling average
                     Uses the same rolling buffer maintained by VolumeSpikeStrategy
                     so there is zero duplication of volume tracking logic.
                     Severity: HIGH if > 5 ×, MEDIUM if 3–5 ×.

  3. missing_symbol  a symbol that was present in the previous tick is absent
                     from the current scrape result. May indicate a scraper gap
                     or a delisting. The `expected` set is taken from app_state.stocks
                     at the time of detection (before app_state is updated).
                     Severity: always HIGH.

Performance contract:
  - detect_data_issues() is synchronous — no DB access, no I/O.
  - flush_data_quality() is async — one INSERT per call, zero per-issue round-trips.
  - get_data_quality_summary() issues one SQL query (aggregate, indexed on created_at).

Safe under async: flush acquires no lock — it is called via asyncio.create_task()
and issues a single batched INSERT which SQLite handles atomically.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from typing import Optional

from sqlalchemy import func, select

from ..db.database import get_session
from ..db.models import DataQualityLog

logger = logging.getLogger("psx.data_quality")

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

_PRICE_JUMP_PCT      = 7.5    # PSX circuit-breaker level — flag anything beyond this
_PRICE_JUMP_HIGH_PCT = 10.0   # escalate to HIGH above this; 7.5–10 % → MEDIUM
_VOL_ANOMALY_X       = 3.0    # flag if volume > 3 × rolling avg
_VOL_HIGH_X          = 5.0    # escalate to HIGH if volume > 5 × rolling avg
_SCRAPER_MIN_RATIO   = 0.8    # flag scraper_degraded if < 80 % of expected symbols returned
_COOLDOWN_SECONDS    = 300    # suppress repeated same-issue logs within this window

# Per-symbol, per-issue-type cooldown state (symbol → issue_type → last_logged_ts).
# Module-level dict — intentionally NOT reset between ticks (that would defeat the purpose).
_last_logged: dict[str, dict[str, int]] = defaultdict(dict)


# ---------------------------------------------------------------------------
# Rate-limit helper — suppresses log/issue spam from a noisy symbol
# ---------------------------------------------------------------------------

def _rate_limited(symbol: str, issue_type: str, now_ts: int) -> bool:
    """
    Return True if this (symbol, issue_type) pair was already emitted within
    _COOLDOWN_SECONDS.  Side-effect: updates _last_logged when NOT limited.
    """
    last = _last_logged[symbol].get(issue_type, 0)
    if now_ts - last < _COOLDOWN_SECONDS:
        return True
    _last_logged[symbol][issue_type] = now_ts
    return False


# ---------------------------------------------------------------------------
# Detection rules (pure, synchronous, O(1) per symbol)
# ---------------------------------------------------------------------------

def _check_price_jump(stock: dict, now_ts: int) -> Optional[dict]:
    """
    Flag if |change_pct| > 7.5 % (PSX circuit-breaker level).

    Severity is escalated based on magnitude:
      7.5 – 10 %  → MEDIUM  (circuit-breaker territory, possible data error)
      > 10 %      → HIGH    (almost certainly a data error or extreme event)

    change_pct is supplied by the scraper directly from the PSX page.
    """
    chg = stock.get("change_pct")
    if chg is None:
        return None
    abs_chg = abs(chg)
    if abs_chg <= _PRICE_JUMP_PCT:
        return None

    severity = "high" if abs_chg > _PRICE_JUMP_HIGH_PCT else "medium"

    return {
        "symbol":     stock["symbol"],
        "timestamp":  now_ts,
        "issue_type": "price_jump",
        "severity":   severity,
        "details":    json.dumps({
            "change_pct": round(float(chg), 4),
            "close":      stock.get("current"),
            "threshold":  _PRICE_JUMP_PCT,
            "high_threshold": _PRICE_JUMP_HIGH_PCT,
        }),
    }


def _check_volume_anomaly(stock: dict, avg_vol: float, now_ts: int) -> Optional[dict]:
    """
    Flag if current volume > _VOL_ANOMALY_X × 20-day rolling average.

    avg_vol is provided by the caller (looked up from VolumeSpikeStrategy's
    rolling buffer — O(1), no DB access).  If avg_vol is 0 (no baseline yet),
    this check is skipped rather than generating a false positive.
    """
    if avg_vol <= 0:
        return None

    vol = stock.get("volume") or 0
    if vol <= 0:
        return None

    ratio = vol / avg_vol
    if ratio < _VOL_ANOMALY_X:
        return None

    severity = "high" if ratio >= _VOL_HIGH_X else "medium"

    return {
        "symbol":     stock["symbol"],
        "timestamp":  now_ts,
        "issue_type": "volume_anomaly",
        "severity":   severity,
        "details":    json.dumps({
            "volume":    vol,
            "avg_vol":   round(avg_vol, 0),
            "ratio":     round(ratio, 2),
            "threshold": _VOL_ANOMALY_X,
        }),
    }


def _check_missing_symbols(
    current_symbols: set[str],
    expected: set[str],
    now_ts: int,
) -> list[dict]:
    """
    Flag any symbol that was in the previous tick but is absent now.

    `expected` is taken from app_state.stocks before it is updated for this
    tick, so it correctly represents what the scraper produced last time.
    Skip if expected is empty (first poll tick after startup).
    """
    if not expected:
        return []

    missing = expected - current_symbols
    issues = []
    for sym in sorted(missing):
        issues.append({
            "symbol":     sym,
            "timestamp":  now_ts,
            "issue_type": "missing_symbol",
            "severity":   "high",
            "details":    json.dumps({
                "reason": "symbol absent from current scrape result",
            }),
        })
    return issues


# ---------------------------------------------------------------------------
# Public entry point — called from poll loop
# ---------------------------------------------------------------------------

def detect_data_issues(
    stocks: list[dict],
    expected: Optional[set[str]] = None,
) -> list[dict]:
    """
    Run all detection rules over one poll tick's stock list.

    Args:
        stocks:   raw list of stock dicts from the scraper (before signal generation)
        expected: set of symbol strings present in the PREVIOUS tick
                  (pass set(app_state.stocks.keys()) before updating app_state)

    Returns:
        List of issue dicts ready for batch insert.  Empty if all clear.
        Never raises — catches all exceptions internally and logs them.
    """
    if not stocks:
        return []

    # Import here to avoid circular import at module load time.
    # signal_engine is always loaded before analytics at runtime.
    try:
        from ..strategy.signal_engine import _volume_spike_strategy
    except ImportError:
        _volume_spike_strategy = None  # type: ignore[assignment]

    now_ts = int(time.time())
    issues: list[dict] = []
    current_symbols: set[str] = set()
    _expected = expected or set()

    try:
        # Rule 0: scraper degraded — partial fetch (< 80 % of expected symbols returned)
        if _expected and len(stocks) < len(_expected) * _SCRAPER_MIN_RATIO:
            if not _rate_limited("__system__", "scraper_degraded", now_ts):
                ratio = len(stocks) / len(_expected)
                issues.append({
                    "symbol":     None,
                    "timestamp":  now_ts,
                    "issue_type": "scraper_degraded",
                    "severity":   "high",
                    "details":    json.dumps({
                        "received": len(stocks),
                        "expected": len(_expected),
                        "ratio":    round(ratio, 2),
                        "threshold": _SCRAPER_MIN_RATIO,
                    }),
                })
                logger.error(
                    "DQ scraper_degraded: received %d/%d symbols (%.0f%% of expected)",
                    len(stocks), len(_expected), ratio * 100,
                )

        for stock in stocks:
            sym = stock.get("symbol", "")
            if not sym:
                continue
            current_symbols.add(sym)

            # Rule 1: price jump
            pj = _check_price_jump(stock, now_ts)
            if pj and not _rate_limited(sym, "price_jump", now_ts):
                issues.append(pj)
                logger.warning(
                    "DQ price_jump [%s]: %s change_pct=%.2f%%",
                    pj["severity"], sym, stock.get("change_pct", 0),
                )

            # Rule 2: volume anomaly
            avg_vol = (
                _volume_spike_strategy.avg_volume(sym)
                if _volume_spike_strategy is not None
                else 0.0
            )
            va = _check_volume_anomaly(stock, avg_vol, now_ts)
            if va and not _rate_limited(sym, "volume_anomaly", now_ts):
                issues.append(va)
                logger.warning(
                    "DQ volume_anomaly [%s]: %s vol=%s avg=%.0f ratio=%.1f×",
                    va["severity"],
                    sym,
                    stock.get("volume"),
                    avg_vol,
                    (stock.get("volume") or 0) / avg_vol if avg_vol > 0 else 0,
                )

        # Rule 3: missing symbols
        missing_issues = _check_missing_symbols(
            current_symbols,
            _expected,
            now_ts,
        )
        for mi in missing_issues:
            if not _rate_limited(mi["symbol"], "missing_symbol", now_ts):
                issues.append(mi)
                logger.warning("DQ missing_symbol: %s absent from scrape result", mi["symbol"])

    except Exception as exc:
        logger.error("detect_data_issues failed (non-fatal): %s", exc)

    return issues


# ---------------------------------------------------------------------------
# Async batch writer — fire-and-forget safe
# ---------------------------------------------------------------------------

async def flush_data_quality(issues: list[dict]) -> None:
    """
    Batch-insert all detected issues into data_quality_log in one transaction.

    Designed to be called via asyncio.create_task() — never blocks the poll loop.
    Safe to call with an empty list (no-op).
    """
    if not issues:
        return

    rows = [
        DataQualityLog(
            symbol     = r.get("symbol"),
            timestamp  = r["timestamp"],
            issue_type = r["issue_type"],
            severity   = r["severity"],
            details    = r.get("details"),
            created_at = int(time.time()),
        )
        for r in issues
    ]

    try:
        async with get_session() as session:
            session.add_all(rows)
        logger.debug("data_quality: flushed %d issue(s) to DB", len(rows))
    except Exception as exc:
        logger.error("data_quality: flush failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Summary query — used by /api/system/status
# ---------------------------------------------------------------------------

async def get_data_quality_summary(window_seconds: int = 3600) -> dict:
    """
    Return a lightweight summary of recent data quality issues.

    Issues older than `window_seconds` (default 1 h) are excluded.
    Returns safe defaults if the table is empty or the query fails.

    Result shape:
        {
          "recent_issues": int,    # total issues in the last window
          "high_severity": int,    # subset with severity == "high"
          "last_issue_at": int | None,  # Unix ts of most recent issue, or None
        }
    """
    cutoff = int(time.time()) - window_seconds

    try:
        async with get_session() as session:
            stmt = (
                select(
                    func.count(DataQualityLog.id).label("total"),
                    func.sum(
                        # SQLite-compatible CASE expression for conditional count
                        (DataQualityLog.severity == "high").cast(
                            __import__("sqlalchemy").Integer
                        )
                    ).label("high_count"),
                    func.max(DataQualityLog.created_at).label("last_at"),
                )
                .where(DataQualityLog.created_at >= cutoff)
            )
            row = (await session.execute(stmt)).one()

        return {
            "recent_issues": int(row.total or 0),
            "high_severity": int(row.high_count or 0),
            "last_issue_at": row.last_at,
        }

    except Exception as exc:
        logger.warning("data_quality summary query failed (non-fatal): %s", exc)
        return {
            "recent_issues": 0,
            "high_severity": 0,
            "last_issue_at": None,
        }
