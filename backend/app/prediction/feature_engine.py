"""
FeatureEngine — offline ML feature computation pipeline.
=========================================================

Reads EOD price history (eod_prices → price_history fallback), signal_outcomes
(targets), and signals_log (signal-history features). Computes 20 features per
(symbol, date_key) row and persists them incrementally to a Parquet file.

Feature catalogue
-----------------
Price-based (8):
  ret_1d         1-day log return
  ret_5d         5-day log return
  ret_20d        20-day log return
  ret_100d       100-day log return
  vol_20d        Realised vol (std of log returns) over 20 days
  vol_60d        Realised vol over 60 days
  drawdown_60d   Rolling drawdown from 60-day peak (always ≤ 0)
  dist_52w_high  (close - rolling_252d_max) / rolling_252d_max  (always ≤ 0)

Indicator-derived (7):
  rsi_7          RSI with 7-period window (simple, matches core.py)
  rsi_14         RSI with 14-period window
  rsi_21         RSI with 21-period window
  sma_ratio_5    close / SMA_5  − 1  (positive = above SMA)
  sma_ratio_20   close / SMA_20 − 1
  bb_pct_b       Bollinger %B  (20-period, 2σ): 0 = lower band, 1 = upper band
  bb_bandwidth   (upper − lower) / middle  (normalised band width)

Cross-sectional / sector-relative (3):
  sector_ret5d_rank   Percentile rank of ret_5d within sector (0–1)
  sector_rsi14_rank   Percentile rank of rsi_14 within sector (0–1)
  sector_vol_rank     Percentile rank of vol_20d within sector (0–1)

Signal-history (2):
  days_since_buy    Calendar days since last BUY signal (NaN if never)
  days_since_sell   Calendar days since last SELL or FORCE_SELL signal (NaN if never)

Targets (3):
  target_short    1 if signal_outcomes.outcome_short  == "correct" else 0 (NaN if NULL)
  target_medium   1 if signal_outcomes.outcome_medium == "correct" else 0
  target_long     1 if signal_outcomes.outcome_long   == "correct" else 0

Key identifiers stored alongside features:
  symbol, date_key, sector, close, scraped_at

RSI formula (matches core.py — simple, non-Wilder):
  delta  = close.diff()
  gains  = delta.clip(lower=0)
  losses = (-delta).clip(lower=0)
  avg_g  = gains.rolling(period).mean()
  avg_l  = losses.rolling(period).mean()
  rsi    = 100 − 100 / (1 + avg_g / avg_l)
  rsi where avg_l == 0 → 100.0

Incremental update:
  On each run, the engine skips (symbol, date_key) rows already present in the
  Parquet file and only appends newly computed rows.  Pass force=True to
  recompute everything from scratch.

Usage:
  python -m scripts.build_features                    # incremental
  python -m scripts.build_features --force            # full recompute
  python -m scripts.build_features --symbols ENGRO    # one symbol
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import select, text

from ..db.database import get_session
from ..db.models import EODPrice, PriceHistory, SignalLog, SignalOutcome

logger = logging.getLogger("psx.feature_engine")

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

_BACKEND_DIR  = Path(__file__).resolve().parents[2]   # backend/
PARQUET_PATH  = _BACKEND_DIR / "data" / "features.parquet"

_PKT          = timezone(timedelta(hours=5))
_BB_PERIOD    = 20
_BB_STD       = 2.0
_MIN_ROWS     = 22    # minimum EOD rows needed to compute any feature


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts_to_date_key(ts: int) -> int:
    dt = datetime.fromtimestamp(ts, tz=_PKT)
    return dt.year * 10_000 + dt.month * 100 + dt.day


def _date_key_to_date(dk: int) -> pd.Timestamp:
    y, md = divmod(dk, 10_000)
    m, d  = divmod(md, 100)
    return pd.Timestamp(year=y, month=m, day=d)


# ---------------------------------------------------------------------------
# RSI (simple, non-Wilder — matches core.py)
# ---------------------------------------------------------------------------

def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta  = close.diff()
    gains  = delta.clip(lower=0)
    losses = (-delta).clip(lower=0)
    avg_g  = gains.rolling(window=period, min_periods=period).mean()
    avg_l  = losses.rolling(window=period, min_periods=period).mean()
    rs     = avg_g / avg_l.replace(0, np.nan)
    rsi    = 100.0 - 100.0 / (1.0 + rs)
    # Where avg_l is 0 (all gains) → RSI = 100
    rsi = rsi.where(avg_l != 0, other=100.0)
    return rsi


# ---------------------------------------------------------------------------
# DB loaders
# ---------------------------------------------------------------------------

async def _load_eod_prices(symbols: Optional[list[str]]) -> pd.DataFrame:
    """
    Load EOD prices for all (or specified) symbols.
    Tries eod_prices first; falls back to price_history WHERE source='historical'.
    Returns DataFrame sorted by (symbol, date_key) ascending.
    """
    async with get_session() as session:
        # Primary: eod_prices
        q = select(
            EODPrice.symbol,
            EODPrice.date_key,
            EODPrice.sector,
            EODPrice.close,
            EODPrice.volume,
            EODPrice.scraped_at,
        )
        if symbols:
            q = q.where(EODPrice.symbol.in_(symbols))
        q = q.order_by(EODPrice.symbol, EODPrice.date_key)
        rows = (await session.execute(q)).all()

        if rows:
            df = pd.DataFrame(rows, columns=["symbol", "date_key", "sector", "close", "volume", "scraped_at"])
            logger.info("Loaded %d EOD rows from eod_prices", len(df))
            return df

        # Fallback: price_history
        logger.warning("eod_prices empty — falling back to price_history WHERE source='historical'")
        q2 = select(
            PriceHistory.symbol,
            PriceHistory.scraped_at,
            PriceHistory.sector,
            PriceHistory.close,
            PriceHistory.volume,
        ).where(PriceHistory.source == "historical")
        if symbols:
            q2 = q2.where(PriceHistory.symbol.in_(symbols))
        q2 = q2.order_by(PriceHistory.symbol, PriceHistory.scraped_at)
        rows2 = (await session.execute(q2)).all()

        if not rows2:
            logger.warning("No historical price data found")
            return pd.DataFrame(columns=["symbol", "date_key", "sector", "close", "volume", "scraped_at"])

        df2 = pd.DataFrame(rows2, columns=["symbol", "scraped_at", "sector", "close", "volume"])
        df2["date_key"] = df2["scraped_at"].apply(_ts_to_date_key)
        df2 = df2.sort_values(["symbol", "date_key"])
        logger.info("Loaded %d rows from price_history fallback", len(df2))
        return df2[["symbol", "date_key", "sector", "close", "volume", "scraped_at"]]


async def _load_signal_outcomes() -> pd.DataFrame:
    """
    Load signal_outcomes for target derivation.
    Returns DataFrame with columns: symbol, timestamp, outcome_short, outcome_medium, outcome_long.
    """
    async with get_session() as session:
        q = select(
            SignalOutcome.symbol,
            SignalOutcome.timestamp,
            SignalOutcome.outcome_short,
            SignalOutcome.outcome_medium,
            SignalOutcome.outcome_long,
        )
        rows = (await session.execute(q)).all()

    if not rows:
        return pd.DataFrame(columns=["symbol", "timestamp", "outcome_short", "outcome_medium", "outcome_long"])

    df = pd.DataFrame(rows, columns=["symbol", "timestamp", "outcome_short", "outcome_medium", "outcome_long"])
    df["date_key"] = df["timestamp"].apply(_ts_to_date_key)
    return df


async def _load_signal_log() -> pd.DataFrame:
    """
    Load signals_log for days_since_buy / days_since_sell features.
    Returns DataFrame with columns: symbol, signal, generated_at.
    """
    async with get_session() as session:
        q = select(
            SignalLog.symbol,
            SignalLog.signal,
            SignalLog.generated_at,
        ).order_by(SignalLog.symbol, SignalLog.generated_at)
        rows = (await session.execute(q)).all()

    if not rows:
        return pd.DataFrame(columns=["symbol", "signal", "generated_at"])

    df = pd.DataFrame(rows, columns=["symbol", "signal", "generated_at"])
    df["date_key"] = df["generated_at"].apply(_ts_to_date_key)
    return df


# ---------------------------------------------------------------------------
# Feature computation per symbol
# ---------------------------------------------------------------------------

def _compute_symbol_features(symbol: str, df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all price-based and indicator-derived features for one symbol.
    Input: df sorted by date_key ascending, columns: date_key, close, volume, sector.
    Returns feature DataFrame with date_key as the join key.
    """
    df = df.copy().reset_index(drop=True)
    close  = df["close"].astype(float)
    volume = df["volume"].astype(float)

    log_ret = np.log(close / close.shift(1))

    # -- Price-based ----------------------------------------------------------
    df["ret_1d"]       = log_ret
    df["ret_5d"]       = np.log(close / close.shift(5))
    df["ret_20d"]      = np.log(close / close.shift(20))
    df["ret_100d"]     = np.log(close / close.shift(100))
    df["vol_20d"]      = log_ret.rolling(20).std()
    df["vol_60d"]      = log_ret.rolling(60).std()
    df["drawdown_60d"] = (close / close.rolling(60).max()) - 1.0
    df["dist_52w_high"]= (close / close.rolling(252).max()) - 1.0

    # -- Indicator-derived ----------------------------------------------------
    df["rsi_7"]  = _rsi(close, 7)
    df["rsi_14"] = _rsi(close, 14)
    df["rsi_21"] = _rsi(close, 21)

    sma5  = close.rolling(5).mean()
    sma20 = close.rolling(20).mean()
    df["sma_ratio_5"]  = (close / sma5)  - 1.0
    df["sma_ratio_20"] = (close / sma20) - 1.0

    bb_mid = sma20
    bb_std = close.rolling(_BB_PERIOD).std()
    bb_upper = bb_mid + _BB_STD * bb_std
    bb_lower = bb_mid - _BB_STD * bb_std
    band_width = bb_upper - bb_lower
    df["bb_pct_b"]    = (close - bb_lower) / band_width.replace(0, np.nan)
    df["bb_bandwidth"]= band_width / bb_mid.replace(0, np.nan)

    return df[[
        "date_key", "sector", "close", "scraped_at",
        "ret_1d", "ret_5d", "ret_20d", "ret_100d",
        "vol_20d", "vol_60d", "drawdown_60d", "dist_52w_high",
        "rsi_7", "rsi_14", "rsi_21",
        "sma_ratio_5", "sma_ratio_20",
        "bb_pct_b", "bb_bandwidth",
    ]]


def _add_signal_history(
    feat: pd.DataFrame,
    symbol: str,
    sig_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Attach days_since_buy / days_since_sell features.
    sig_df: filtered to this symbol, sorted by date_key ascending.
    """
    if sig_df.empty:
        feat["days_since_buy"]  = np.nan
        feat["days_since_sell"] = np.nan
        return feat

    buy_dates  = sig_df.loc[sig_df["signal"] == "BUY",  "date_key"].values
    sell_dates = sig_df.loc[sig_df["signal"].isin(["SELL", "FORCE_SELL"]), "date_key"].values

    def _days_since(date_key: int, event_keys: np.ndarray) -> float:
        if len(event_keys) == 0:
            return np.nan
        # find the last event on or before this date
        idx = np.searchsorted(event_keys, date_key, side="right") - 1
        if idx < 0:
            return np.nan
        d_event = _date_key_to_date(int(event_keys[idx]))
        d_now   = _date_key_to_date(int(date_key))
        return float((d_now - d_event).days)

    feat["days_since_buy"]  = feat["date_key"].apply(lambda dk: _days_since(dk, buy_dates))
    feat["days_since_sell"] = feat["date_key"].apply(lambda dk: _days_since(dk, sell_dates))
    return feat


def _add_targets(
    feat: pd.DataFrame,
    symbol: str,
    out_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Attach target_short / target_medium / target_long from signal_outcomes.
    Outcome 'correct' → 1, 'incorrect'/'neutral' → 0, NULL → NaN.
    One signal_outcome row may map to multiple EOD dates via date_key; we
    apply the latest outcome for each date to avoid double-counting.
    """
    if out_df.empty:
        feat["target_short"]  = np.nan
        feat["target_medium"] = np.nan
        feat["target_long"]   = np.nan
        return feat

    sym_out = out_df[out_df["symbol"] == symbol].copy()
    if sym_out.empty:
        feat["target_short"]  = np.nan
        feat["target_medium"] = np.nan
        feat["target_long"]   = np.nan
        return feat

    def _encode(col: pd.Series) -> pd.Series:
        return col.map({"correct": 1.0, "incorrect": 0.0, "neutral": 0.0})

    sym_out["target_short"]  = _encode(sym_out["outcome_short"])
    sym_out["target_medium"] = _encode(sym_out["outcome_medium"])
    sym_out["target_long"]   = _encode(sym_out["outcome_long"])

    # Keep one row per date_key (last evaluated_at wins — but we don't have that here,
    # so just drop_duplicates keeping last)
    tgt = sym_out[["date_key", "target_short", "target_medium", "target_long"]] \
        .drop_duplicates(subset="date_key", keep="last") \
        .set_index("date_key")

    feat = feat.join(tgt, on="date_key", how="left")
    return feat


def _add_cross_sectional(feat_all: pd.DataFrame) -> pd.DataFrame:
    """
    Add sector-relative percentile rank features.
    Computed across all symbols sharing the same (date_key, sector) pair.

    When sector is NULL (e.g. historical EOD data from dps.psx.com.pk which
    does not expose sector info), all symbols are grouped under "unknown" so
    ranks are computed market-wide rather than being left as NaN.
    """
    feat_all = feat_all.copy()
    feat_all["sector"] = feat_all["sector"].fillna("unknown")

    for col, new_col in [
        ("ret_5d",  "sector_ret5d_rank"),
        ("rsi_14",  "sector_rsi14_rank"),
        ("vol_20d", "sector_vol_rank"),
    ]:
        feat_all[new_col] = (
            feat_all
            .groupby(["date_key", "sector"])[col]
            .rank(pct=True, na_option="keep")
        )

    return feat_all


# ---------------------------------------------------------------------------
# Existing Parquet state
# ---------------------------------------------------------------------------

def _load_existing(path: Path) -> pd.DataFrame:
    if path.exists():
        try:
            return pd.read_parquet(path)
        except Exception as exc:
            logger.warning("Could not read existing Parquet (%s) — starting fresh", exc)
    return pd.DataFrame()


def _already_computed(
    existing: pd.DataFrame,
    symbol: str,
    date_key: int,
) -> bool:
    if existing.empty:
        return False
    mask = (existing["symbol"] == symbol) & (existing["date_key"] == date_key)
    return bool(mask.any())


# ---------------------------------------------------------------------------
# Main engine class
# ---------------------------------------------------------------------------

class FeatureEngine:
    """
    Offline ML feature computation pipeline.

    Example:
        engine = FeatureEngine()
        await engine.run()            # incremental
        await engine.run(force=True)  # full recompute
        await engine.run(symbols=["ENGRO", "LUCK"])
    """

    def __init__(self, parquet_path: Path = PARQUET_PATH) -> None:
        self.parquet_path = parquet_path
        self.parquet_path.parent.mkdir(parents=True, exist_ok=True)

    async def run(
        self,
        symbols: Optional[list[str]] = None,
        force: bool = False,
    ) -> pd.DataFrame:
        """
        Compute and persist features.

        Returns the full feature DataFrame (existing + newly computed rows).
        """
        logger.info("FeatureEngine: loading DB data …")

        # Load source data
        prices_df  = await _load_eod_prices(symbols)
        signal_df  = await _load_signal_log()
        outcome_df = await _load_signal_outcomes()

        if prices_df.empty:
            logger.warning("FeatureEngine: no price data — nothing to compute")
            return pd.DataFrame()

        # Load existing Parquet (for incremental skip)
        existing = pd.DataFrame() if force else _load_existing(self.parquet_path)

        new_frames: list[pd.DataFrame] = []

        for symbol, grp in prices_df.groupby("symbol", sort=False):
            grp = grp.sort_values("date_key").reset_index(drop=True)

            if len(grp) < _MIN_ROWS:
                logger.debug("  %s: only %d rows — skipping (need %d)", symbol, len(grp), _MIN_ROWS)
                continue

            # Identify rows that still need to be computed
            if not force and not existing.empty:
                computed_keys = set(
                    existing.loc[existing["symbol"] == symbol, "date_key"].tolist()
                )
                new_grp = grp[~grp["date_key"].isin(computed_keys)]
            else:
                new_grp = grp

            if new_grp.empty:
                logger.debug("  %s: all rows already computed", symbol)
                continue

            # We must compute features on the full series (rolling windows need history)
            # then filter to only the new rows for output.
            feat = _compute_symbol_features(symbol, grp)

            # Signal history
            sym_sig = signal_df[signal_df["symbol"] == symbol].copy()
            feat = _add_signal_history(feat, symbol, sym_sig)

            # Targets
            feat = _add_targets(feat, symbol, outcome_df)

            # Add symbol column
            feat.insert(0, "symbol", symbol)

            # Filter to only new date_keys
            if not force and not existing.empty:
                feat = feat[feat["date_key"].isin(new_grp["date_key"])]

            new_frames.append(feat)
            logger.info("  %s: %d new rows computed", symbol, len(feat))

        if not new_frames:
            logger.info("FeatureEngine: no new rows to add")
            if not existing.empty:
                return existing
            return pd.DataFrame()

        new_df = pd.concat(new_frames, ignore_index=True)

        # Cross-sectional features require all symbols on the same date
        # Compute on new rows using sector info from all symbols' new_df
        new_df = _add_cross_sectional(new_df)

        # Merge with existing
        if force or existing.empty:
            full_df = new_df
        else:
            full_df = pd.concat([existing, new_df], ignore_index=True)
            # Deduplicate — prefer new rows (they may correct stale targets)
            full_df = full_df.drop_duplicates(
                subset=["symbol", "date_key"], keep="last"
            ).reset_index(drop=True)

        # Persist
        full_df.to_parquet(self.parquet_path, index=False, engine="pyarrow")
        logger.info(
            "FeatureEngine: wrote %d total rows (%d new) to %s",
            len(full_df), len(new_df), self.parquet_path,
        )

        return full_df

    def load(self) -> pd.DataFrame:
        """Synchronously load the feature store from disk."""
        return _load_existing(self.parquet_path)

    @property
    def schema(self) -> list[str]:
        """Column names in the feature store."""
        return [
            "symbol", "date_key", "sector", "close", "scraped_at",
            # price-based
            "ret_1d", "ret_5d", "ret_20d", "ret_100d",
            "vol_20d", "vol_60d", "drawdown_60d", "dist_52w_high",
            # indicator-derived
            "rsi_7", "rsi_14", "rsi_21",
            "sma_ratio_5", "sma_ratio_20",
            "bb_pct_b", "bb_bandwidth",
            # cross-sectional
            "sector_ret5d_rank", "sector_rsi14_rank", "sector_vol_rank",
            # signal-history
            "days_since_buy", "days_since_sell",
            # targets
            "target_short", "target_medium", "target_long",
        ]
