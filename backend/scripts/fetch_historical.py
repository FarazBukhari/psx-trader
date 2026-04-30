#!/usr/bin/env python3
"""
PSX Historical Data Fetcher
============================
Downloads full EOD (end-of-day) OHLCV history from the PSX Data Portal:
  https://dps.psx.com.pk/timeseries/eod/{SYMBOL}

Response schema (JSON):
  {
    "status": 1,
    "message": "",
    "data": [
      [unix_timestamp, close_price, volume, open_price],   // newest first
      ...
    ]
  }

Data is inserted into price_history with source="historical".
high / low are left NULL (not provided by this endpoint).
Duplicate (symbol, scraped_at) rows are silently skipped.

Usage (from the backend/ directory):
  python -m scripts.fetch_historical                         # all known symbols
  python -m scripts.fetch_historical --symbols ENGRO LUCK    # specific symbols
  python -m scripts.fetch_historical --symbols ENGRO --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import httpx

# Allow running as `python -m scripts.fetch_historical` from backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("fetch_historical")

PSX_EOD_URL = "https://dps.psx.com.pk/timeseries/eod/{symbol}"

# All symbols visible on dps.psx.com.pk (KSE-100 + common mid-caps)
DEFAULT_SYMBOLS = [
    "ENGRO", "LUCK",  "HBL",  "PSO",   "OGDC",  "PPL",   "MCB",
    "UBL",   "MARI",  "HUBC", "UNITY", "FFBL",  "PTCL",  "SYS",
    "TRG",   "EFERT", "FFC",  "MEBL",  "BAHL",  "NBP",   "POL",
    "SNGPL", "SSGC",  "KAPCO","NCPL",  "DGKC",  "MLCF",  "FCCL",
    "ACPL",  "CHCC",  "KOHC", "PIOC",  "FABL",  "BAFL",  "SILK",
    "ATRL",  "APL",   "SHEL", "PSX",   "PAKT",  "GATM",  "COLG",
]


# ---------------------------------------------------------------------------
# Fetch from PSX DPS
# ---------------------------------------------------------------------------

async def fetch_eod(client: httpx.AsyncClient, symbol: str) -> list[dict]:
    """
    Fetch complete EOD history for one symbol.
    Returns list of row dicts ready for DB insert, sorted oldest-first.
    """
    url = PSX_EOD_URL.format(symbol=symbol)
    try:
        resp = await client.get(url, timeout=20.0)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        logger.warning("  %s: fetch failed — %s", symbol, exc)
        return []

    if payload.get("status") != 1 or not payload.get("data"):
        logger.warning("  %s: empty or error response", symbol)
        return []

    rows = []
    data = payload["data"]   # newest-first list of [ts, close, volume, open]

    for entry in reversed(data):   # reverse → oldest-first
        if len(entry) < 4:
            continue
        ts, close, volume, open_price = entry[0], entry[1], entry[2], entry[3]
        rows.append({
            "symbol":     symbol,
            "sector":     None,
            "ldcp":       None,   # not provided by this endpoint
            "open_price": float(open_price),
            "high":       None,   # not provided
            "low":        None,   # not provided
            "close":      float(close),
            "volume":     int(volume),
            "change_pct": None,
            "source":     "historical",
            "scraped_at": int(ts),
        })

    # Back-fill ldcp (previous day's close) now that data is sorted oldest-first
    prev_close = None
    for row in rows:
        if prev_close is not None:
            row["ldcp"] = round(prev_close, 4)
            chg = (row["close"] - prev_close) / prev_close * 100
            row["change_pct"] = round(chg, 4)
        prev_close = row["close"]

    return rows


# ---------------------------------------------------------------------------
# DB insert
# ---------------------------------------------------------------------------

async def insert_rows(rows: list[dict]) -> int:
    """Bulk-insert into price_history, skipping duplicates on (symbol, scraped_at)."""
    if not rows:
        return 0

    from app.db.database import get_session
    from app.db.models import PriceHistory
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    inserted = 0
    for i in range(0, len(rows), 500):
        batch = rows[i:i + 500]
        async with get_session() as session:
            stmt = (
                sqlite_insert(PriceHistory)
                .values(batch)
                .on_conflict_do_nothing()
            )
            result = await session.execute(stmt)
            inserted += result.rowcount or 0

    return inserted


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(symbols: list[str], verbose: bool = False) -> None:
    from app.db import init_db
    await init_db()

    if verbose:
        logger.setLevel(logging.DEBUG)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://dps.psx.com.pk/",
    }

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        total_rows = 0
        total_syms = 0

        for symbol in symbols:
            logger.info("Fetching %s …", symbol)
            rows = await fetch_eod(client, symbol)
            if rows:
                n = await insert_rows(rows)
                total_rows += n
                total_syms += 1
                logger.info("  %s: %d trading days stored (%d inserted)", symbol, len(rows), n)
            else:
                logger.info("  %s: no data", symbol)

            await asyncio.sleep(0.25)   # be polite to PSX servers

    logger.info(
        "\nDone. %d symbol(s) processed, %d rows newly inserted into price_history.",
        total_syms, total_rows,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch PSX historical EOD data from dps.psx.com.pk")
    p.add_argument(
        "--symbols", nargs="+",
        default=DEFAULT_SYMBOLS,
        help="PSX symbols to fetch (default: all known)",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    logger.info("Fetching %d symbol(s) from dps.psx.com.pk", len(args.symbols))
    asyncio.run(main(args.symbols, verbose=args.verbose))
