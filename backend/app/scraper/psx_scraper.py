"""
PSX Scraper — fetches live market data from www.psx.com.pk/market-summary

Snapshot persistence guarantee:
  - Every successful live scrape is saved to `last_snapshot.json`.
  - On startup the snapshot is loaded into memory immediately.
  - If the market is closed: snapshot is returned as stale (no HTTP request made).
  - If the market is open but the scrape fails: snapshot is returned as stale.
  - If no snapshot exists at all: mock data is returned as stale (last resort).
  - Result is NEVER an empty list.

Each returned stock dict carries:
  "stale"  : bool  — True when data is not from a live scrape this cycle
  "source" : str   — "live" | "snapshot" | "mock"
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Optional

import httpx

from ..market_hours import market_status, MarketStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PSX_URL = "https://www.psx.com.pk/market-summary"
PSX_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Referer": "https://www.psx.com.pk/",
}

# Snapshot stored next to the backend package — survives restarts
_SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "last_snapshot.json"

# ---------------------------------------------------------------------------
# Mock data (fallback of last resort when no snapshot exists)
# ---------------------------------------------------------------------------

_MOCK_BASE: dict[str, dict] = {
    "ENGRO":  {"ldcp": 285.0,  "sector": "FERTILIZER"},
    "LUCK":   {"ldcp": 820.0,  "sector": "CEMENT"},
    "HBL":    {"ldcp": 145.0,  "sector": "BANKING"},
    "PSO":    {"ldcp": 345.0,  "sector": "OIL & GAS"},
    "OGDC":   {"ldcp": 160.0,  "sector": "OIL & GAS"},
    "PPL":    {"ldcp": 100.0,  "sector": "OIL & GAS"},
    "MCB":    {"ldcp": 210.0,  "sector": "BANKING"},
    "UBL":    {"ldcp": 172.0,  "sector": "BANKING"},
    "MARI":   {"ldcp": 1850.0, "sector": "OIL & GAS"},
    "HUBC":   {"ldcp": 105.0,  "sector": "POWER"},
    "UNITY":  {"ldcp": 24.0,   "sector": "SUGAR"},
    "FFBL":   {"ldcp": 18.0,   "sector": "FERTILIZER"},
    "PTCL":   {"ldcp": 14.5,   "sector": "TELECOM"},
    "SYS":    {"ldcp": 680.0,  "sector": "TECHNOLOGY"},
    "TRG":    {"ldcp": 138.0,  "sector": "TECHNOLOGY"},
}

_mock_state: dict[str, float] = {sym: d["ldcp"] for sym, d in _MOCK_BASE.items()}


def _generate_mock_data(stale: bool = False) -> list[dict]:
    """Simulate realistic tick-by-tick price movement for dev/fallback."""
    global _mock_state
    rows = []
    for symbol, base in _MOCK_BASE.items():
        prev = _mock_state[symbol]
        change = prev * random.uniform(-0.015, 0.015)
        current = round(prev + change, 2)
        _mock_state[symbol] = current

        change_val = round(current - base["ldcp"], 2)
        change_pct = round((change_val / base["ldcp"]) * 100, 2)
        volume = random.randint(50_000, 5_000_000)

        rows.append({
            "symbol":     symbol,
            "sector":     base["sector"],
            "ldcp":       base["ldcp"],
            "open":       round(base["ldcp"] * random.uniform(0.995, 1.005), 2),
            "high":       round(max(base["ldcp"], current) * 1.002, 2),
            "low":        round(min(base["ldcp"], current) * 0.998, 2),
            "current":    current,
            "change":     change_val,
            "change_pct": change_pct,
            "volume":     volume,
            "source":     "mock",
            "stale":      stale,
            "timestamp":  time.time(),
        })
    return rows


# ---------------------------------------------------------------------------
# Snapshot I/O helpers
# ---------------------------------------------------------------------------

def _load_snapshot(path: Path) -> Optional[list[dict]]:
    """
    Load the last saved live snapshot from disk.
    Returns None if the file doesn't exist or is corrupted.
    """
    try:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        stocks = data.get("stocks", [])
        if not stocks:
            return None
        logger.info(
            "Loaded snapshot: %d stocks, saved at %s",
            len(stocks),
            data.get("saved_at_human", "unknown"),
        )
        return stocks
    except Exception as exc:
        logger.warning("Could not load snapshot from %s: %s", path, exc)
        return None


def _save_snapshot(stocks: list[dict], path: Path) -> None:
    """
    Atomically write the current stock list to disk.
    Uses write-to-tmp then rename to avoid corruption on crash.
    """
    try:
        tmp = path.with_suffix(".json.tmp")
        payload = {
            "saved_at":       time.time(),
            "saved_at_human": time.strftime("%Y-%m-%d %H:%M:%S PKT", time.localtime()),
            "count":          len(stocks),
            "stocks":         stocks,
        }
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
        tmp.replace(path)   # atomic on POSIX; near-atomic on Windows
        logger.debug("Snapshot saved: %d stocks → %s", len(stocks), path)
    except Exception as exc:
        logger.warning("Could not save snapshot to %s: %s", path, exc)


def _mark_stale(stocks: list[dict]) -> list[dict]:
    """
    Return a copy of the stock list with:
      - stale=True on every row
      - source set to "snapshot"
      - timestamp updated to now (so age is calculable by consumers)
    The prices themselves are unchanged — they are the last known prices.
    """
    now = time.time()
    return [
        {**s, "stale": True, "source": "snapshot", "snapshot_age": round(now - s.get("timestamp", now))}
        for s in stocks
    ]


# ---------------------------------------------------------------------------
# Main async scraper
# ---------------------------------------------------------------------------

class PSXScraper:
    """
    Async PSX market data scraper with snapshot fallback.

    Fetch priority:
      1. Market open + scrape succeeds  → live data  (stale=False, source="live")
      2. Market closed                  → snapshot   (stale=True,  source="snapshot")
      3. Market open + scrape fails     → snapshot   (stale=True,  source="snapshot")
      4. No snapshot at all             → mock       (stale=True,  source="mock")

    Result is NEVER an empty list.
    """

    def __init__(
        self,
        timeout: float = 15.0,
        snapshot_path: Path = _SNAPSHOT_PATH,
    ):
        self._timeout  = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._snapshot_path = snapshot_path

        # Forced mock mode (--mock flag or PSX_MOCK=true env)
        self._force_mock = False

        # Last known good data (in-memory cache)
        self._last_snapshot: Optional[list[dict]] = _load_snapshot(snapshot_path)

        # Track consecutive live failures for logging
        self._consecutive_failures = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch(self) -> list[dict]:
        """
        Return a list of stock dicts. Never empty.

        Each dict includes:
          "stale"        : bool  — whether data is from this cycle's live scrape
          "source"       : str   — "live" | "snapshot" | "mock"
          "snapshot_age" : int   — seconds since snapshot was taken (if stale)
        """
        # Forced mock mode (dev/test)
        if self._force_mock:
            logger.debug("Force-mock mode active")
            return _generate_mock_data(stale=False)

        # Check market hours first — avoid hammering PSX when closed
        status: MarketStatus = market_status()

        if not status.is_open:
            return self._serve_stale(
                reason=f"Market closed — {status.reason}",
                attempt_live=False,
            )

        # Market is open — attempt live scrape
        # Outer try is the last-resort boundary: _fetch_live has its own
        # try/except but we guard here too so fetch() is guaranteed non-empty.
        try:
            return await self._fetch_live()
        except Exception as exc:
            logger.error("Unexpected error in _fetch_live: %s", exc)
            return self._serve_stale(reason=f"Unexpected error: {type(exc).__name__}")

    @property
    def last_snapshot(self) -> Optional[list[dict]]:
        """Expose the last known snapshot for external consumers (e.g. tests)."""
        return self._last_snapshot

    @property
    def has_snapshot(self) -> bool:
        return bool(self._last_snapshot)

    def enable_mock(self) -> None:
        """Force mock mode — useful for testing / --mock CLI flag."""
        self._force_mock = True

    def disable_mock(self) -> None:
        self._force_mock = False
        self._consecutive_failures = 0

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_live(self) -> list[dict]:
        """Attempt a live scrape. On any failure, fall back to stale snapshot."""
        try:
            client = await self._get_client()
            resp   = await client.get(PSX_URL)
            resp.raise_for_status()

            rows = self._parse_html(resp.text)

            if not rows:
                logger.warning("PSX response parsed 0 rows — using snapshot")
                return self._serve_stale(reason="Scrape returned 0 rows")

            # Success — tag each row as live and persist
            self._consecutive_failures = 0
            live_rows = [{**r, "stale": False, "source": "live"} for r in rows]
            self._last_snapshot = live_rows
            _save_snapshot(live_rows, self._snapshot_path)
            logger.info("Live scrape: %d stocks", len(live_rows))
            return live_rows

        except Exception as exc:
            self._consecutive_failures += 1
            logger.warning(
                "PSX live scrape failed (#%d): %s — using snapshot",
                self._consecutive_failures,
                exc,
            )
            return self._serve_stale(reason=f"Scrape error: {type(exc).__name__}")

    def _serve_stale(
        self,
        reason: str = "unknown",
        attempt_live: bool = True,   # informational only
    ) -> list[dict]:
        """
        Return the last known snapshot marked as stale.
        Falls back to mock data only if no snapshot exists at all.
        """
        if self._last_snapshot:
            stale_rows = _mark_stale(self._last_snapshot)
            logger.info(
                "Serving stale snapshot (%d stocks) — %s",
                len(stale_rows),
                reason,
            )
            return stale_rows

        # Absolute last resort — no snapshot and no live data
        logger.warning("No snapshot available — serving mock data as stale. Reason: %s", reason)
        mock_rows = _generate_mock_data(stale=True)
        # Save mock as snapshot so the next stale serve has something real-ish
        self._last_snapshot = mock_rows
        return mock_rows

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers=PSX_HEADERS,
                verify=False,   # PSX site; macOS missing system certs
            )
        return self._client

    @staticmethod
    def _parse_html(html: str) -> list[dict]:
        """
        Parse PSX market-summary page into stock dicts.
        Returns empty list on any parse failure (caller handles fallback).
        """
        try:
            from bs4 import BeautifulSoup  # noqa: PLC0415

            soup   = BeautifulSoup(html, "html.parser")
            result: list[dict] = []

            for table in soup.find_all("table"):
                sector_th = table.find("th", attrs={"colspan": "8"})
                sector = sector_th.get_text(strip=True) if sector_th else "N/A"

                for tr in table.find_all("tr"):
                    tds = tr.find_all("td")
                    if len(tds) < 8:
                        continue

                    scrip_td = tds[0]
                    symbol = (
                        scrip_td.get("data-srip") or scrip_td.get_text(strip=True)
                    ).strip().upper()

                    if not symbol or symbol in {"SCRIP"}:
                        continue

                    def _val(td_el, default: float = 0.0) -> float:
                        txt = td_el.get_text(strip=True).replace(",", "").replace(" ", "")
                        try:
                            return float(txt)
                        except (ValueError, TypeError):
                            return default

                    ldcp    = _val(tds[1])
                    open_p  = _val(tds[2])
                    high    = _val(tds[3])
                    low     = _val(tds[4])
                    current = _val(tds[5])
                    change  = _val(tds[6])
                    volume  = int(_val(tds[7]))

                    change_pct = round((change / ldcp) * 100, 2) if ldcp else 0.0

                    result.append({
                        "symbol":     symbol,
                        "sector":     sector,
                        "ldcp":       ldcp,
                        "open":       open_p,
                        "high":       high,
                        "low":        low,
                        "current":    current,
                        "change":     change,
                        "change_pct": change_pct,
                        "volume":     volume,
                        "timestamp":  time.time(),
                    })

            return result
        except Exception as exc:
            logger.warning("HTML parse failed: %s", exc)
            return []
