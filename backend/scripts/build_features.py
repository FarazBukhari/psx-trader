#!/usr/bin/env python3
"""
PSX Feature Builder
====================
CLI runner for FeatureEngine — computes and persists ML features to Parquet.

Usage (from the backend/ directory):
  python -m scripts.build_features                         # incremental update
  python -m scripts.build_features --force                 # full recompute
  python -m scripts.build_features --symbols ENGRO LUCK   # specific symbols
  python -m scripts.build_features --symbols ENGRO --verbose
  python -m scripts.build_features --info                  # show current store stats
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Allow running as `python -m scripts.build_features` from backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("build_features")


async def main(
    symbols: list[str] | None,
    force: bool,
    verbose: bool,
    info: bool,
) -> None:
    from app.db import init_db
    from app.prediction.feature_engine import PARQUET_PATH, FeatureEngine

    if verbose:
        logging.getLogger("psx.feature_engine").setLevel(logging.DEBUG)

    # --info: just print store stats and exit
    if info:
        engine = FeatureEngine()
        df = engine.load()
        if df.empty:
            logger.info("Feature store is empty (no Parquet file yet)")
        else:
            logger.info("Feature store: %s", PARQUET_PATH)
            logger.info("  Rows      : %d", len(df))
            logger.info("  Symbols   : %d  (%s)", df["symbol"].nunique(), ", ".join(sorted(df["symbol"].unique())))
            logger.info("  Date range: %s → %s", df["date_key"].min(), df["date_key"].max())
            logger.info("  Columns   : %s", list(df.columns))
            labeled = df[["target_short", "target_medium", "target_long"]].notna().sum()
            logger.info("  Labeled rows (not-NaN targets):")
            for col, n in labeled.items():
                logger.info("    %s: %d", col, n)
        return

    await init_db()

    engine = FeatureEngine()
    df = await engine.run(symbols=symbols or None, force=force)

    if df.empty:
        logger.info("No features produced.")
        return

    logger.info(
        "Done. Feature store: %d rows, %d symbols, date range %s → %s",
        len(df),
        df["symbol"].nunique(),
        df["date_key"].min(),
        df["date_key"].max(),
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build PSX ML feature store from EOD price history")
    p.add_argument(
        "--symbols", nargs="+", default=None,
        help="Specific PSX symbols to process (default: all in eod_prices)",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Recompute all rows even if already in the Parquet store",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging",
    )
    p.add_argument(
        "--info", action="store_true",
        help="Print current feature store statistics and exit (no recompute)",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(
        symbols=args.symbols,
        force=args.force,
        verbose=args.verbose,
        info=args.info,
    ))
