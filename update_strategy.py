#!/usr/bin/env python3
"""
update_strategy.py — refresh strategy.json price levels from latest snapshot.

Reads:  signals_snapshot.json   (written by scraper after every live scrape)
Writes: config/strategy.json    (hot-reloaded by backend within 5s)

Levels are set relative to each stock's open price for the day:
  buy_below  = open × (1 - BUY_PCT)    default: 2.5% below open
  sell_above = open × (1 + SELL_PCT)   default: 4.0% above open
  stop_loss  = open × (1 - STOP_PCT)   default: 4.5% below open

Run once at/after market open (09:30 PKT). Re-run any time to refresh.
"""

import json
import os
import tempfile
import time
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
BUY_PCT  = 0.025   # 2.5% below open → buy_below
SELL_PCT = 0.040   # 4.0% above open → sell_above
STOP_PCT = 0.045   # 4.5% below open → stop_loss

ROOT          = Path(__file__).parent
SNAPSHOT_PATH = ROOT / "backend" / "last_snapshot.json"   # written by scraper every 5s
STRATEGY_PATH = ROOT / "config" / "strategy.json"

# ── Load snapshot ─────────────────────────────────────────────────────────────
if not SNAPSHOT_PATH.exists():
    print(f"✗  Snapshot not found at {SNAPSHOT_PATH}")
    print("   Start the backend first so the scraper can write a snapshot.")
    raise SystemExit(1)

with open(SNAPSHOT_PATH) as f:
    snap = json.load(f)

rows = snap.get("stocks", [])
if not rows:
    print("✗  Snapshot is empty — no stocks found.")
    raise SystemExit(1)

snap_age = time.time() - snap.get("saved_at", 0)
print(f"Snapshot: {len(rows)} stocks, age {snap_age:.0f}s")

# ── Build symbol levels ───────────────────────────────────────────────────────
symbols = {}
skipped = []

for row in rows:
    sym  = row.get("symbol", "").strip().upper()
    open_price = row.get("open") or row.get("current")   # fallback to current if open missing

    if not sym or not open_price or open_price <= 0:
        skipped.append(sym or "?")
        continue

    symbols[sym] = {
        "buy_below":  round(open_price * (1 - BUY_PCT),  2),
        "sell_above": round(open_price * (1 + SELL_PCT), 2),
        "stop_loss":  round(open_price * (1 - STOP_PCT), 2),
    }

print(f"Levels computed: {len(symbols)} symbols  |  skipped: {len(skipped)}")

# ── Preserve existing global config block ────────────────────────────────────
existing_global = {}
if STRATEGY_PATH.exists():
    try:
        with open(STRATEGY_PATH) as f:
            existing = json.load(f)
        existing_global = existing.get("global", {})
    except Exception:
        pass

global_cfg = {
    "poll_interval_seconds":    existing_global.get("poll_interval_seconds", 5),
    "volume_spike_threshold":   existing_global.get("volume_spike_threshold", 2.0),
    "enable_volume_filter":     existing_global.get("enable_volume_filter", True),
    "enable_change_pct_filter": existing_global.get("enable_change_pct_filter", True),
    "change_pct_alert_threshold": existing_global.get("change_pct_alert_threshold", 2.5),
}

# ── Write atomically ──────────────────────────────────────────────────────────
updated_at = time.strftime("%Y-%m-%d %H:%M PKT", time.localtime())
payload = {
    "_comment": f"PSX Short-Term Strategy — auto-refreshed {updated_at}. "
                f"Levels: {BUY_PCT*100:.1f}%/{SELL_PCT*100:.1f}%/{STOP_PCT*100:.1f}% from open prices.",
    "symbols": symbols,
    "global":  global_cfg,
}

STRATEGY_PATH.parent.mkdir(parents=True, exist_ok=True)
tmp_fd, tmp_path = tempfile.mkstemp(dir=STRATEGY_PATH.parent, suffix=".tmp")
try:
    with os.fdopen(tmp_fd, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp_path, STRATEGY_PATH)
except Exception as e:
    os.unlink(tmp_path)
    print(f"✗  Write failed: {e}")
    raise SystemExit(1)

print(f"✓  strategy.json updated — {len(symbols)} symbols @ {updated_at}")
print(f"   Backend will hot-reload within 5s.")
