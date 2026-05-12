"""
MLScorer — live ML confidence scoring for the PredictionEngine.
================================================================

Loads the three trained LightGBM models (target_short / medium / long)
and the feature store (features.parquet) at startup.  For each incoming
signal it looks up the most recent feature row for that symbol and returns
a calibrated ML probability per horizon.

Design choices
--------------
- Direction still comes from the heuristic vote system (momentum/RSI/BB/SR).
  ML gives a calibrated *confidence* that the heuristic direction is correct.
- Only the latest EOD feature row per symbol is used (yesterday's close).
  Intraday price buffers are not used — the model was trained on EOD features.
- Falls back gracefully: if models or features are missing, returns None and
  the PredictionEngine uses its heuristic confidence unchanged.
- Thread-safe lazy init via a module-level singleton.
- Reload support: call MLScorer.reload() after running train_model.

Output
------
score(symbol, signal) -> Optional[dict]:
  {
    "short":  float,   # P(target_short=correct) for this symbol
    "medium": float,   # P(target_medium=correct)
    "long":   float,   # P(target_long=correct)
    "best":   float,   # max(short, medium, long)  — used as overall confidence
    "horizon": str,    # "short" | "medium" | "long"  — best horizon
  }
"""

from __future__ import annotations

import logging
import pickle
import threading
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("psx.ml_scorer")

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_PARQUET     = _BACKEND_DIR / "data" / "features.parquet"
_MODELS_DIR  = _BACKEND_DIR / "data" / "models"

FEATURE_COLS = [
    "ret_1d", "ret_5d", "ret_20d", "ret_100d",
    "vol_20d", "vol_60d", "drawdown_60d", "dist_52w_high",
    "rsi_7", "rsi_14", "rsi_21",
    "sma_ratio_5", "sma_ratio_20",
    "bb_pct_b", "bb_bandwidth",
    "sector_ret5d_rank", "sector_rsi14_rank", "sector_vol_rank",
    "days_since_buy", "days_since_sell",
]

_SENTINEL_FILLS = {
    "days_since_buy":    999.0,
    "days_since_sell":   999.0,
    "sector_ret5d_rank": 0.5,
    "sector_rsi14_rank": 0.5,
    "sector_vol_rank":   0.5,
}

TARGETS = ["target_short", "target_medium", "target_long"]
_HORIZON_LABELS = {
    "target_short":  "short",
    "target_medium": "medium",
    "target_long":   "long",
}


class MLScorer:
    """
    Singleton ML scorer.  Use the module-level ``scorer`` instance.

    Thread-safe: a single RLock guards model + feature-store loads and reloads.
    """

    def __init__(self) -> None:
        self._lock    = threading.RLock()
        self._models: dict[str, object]      = {}   # target → pipeline
        self._latest: dict[str, np.ndarray]  = {}   # symbol → feature vector
        self._ready   = False

    # ------------------------------------------------------------------ #
    # Initialisation                                                       #
    # ------------------------------------------------------------------ #

    def load(self) -> None:
        """
        Load models and feature store.  Safe to call multiple times — only
        loads once unless reload() is called first.
        """
        with self._lock:
            if self._ready:
                return
            self._do_load()

    def reload(self) -> None:
        """Force a full reload of models and features (call after retraining)."""
        with self._lock:
            self._ready = False
            self._models.clear()
            self._latest.clear()
            self._do_load()

    def _do_load(self) -> None:
        n_models = self._load_models()
        n_syms   = self._load_features()
        if n_models > 0 and n_syms > 0:
            self._ready = True
            logger.info(
                "MLScorer ready: %d model(s), %d symbol(s) in feature store",
                n_models, n_syms,
            )
        else:
            logger.warning(
                "MLScorer not ready: models=%d symbols=%d — falling back to heuristic",
                n_models, n_syms,
            )

    def _load_models(self) -> int:
        loaded = 0
        for target in TARGETS:
            path = _MODELS_DIR / f"{target}_latest.pkl"
            if not path.exists():
                logger.debug("No model file for %s", target)
                continue
            try:
                with open(path, "rb") as f:
                    self._models[target] = pickle.load(f)
                loaded += 1
                logger.debug("Loaded model %s", target)
            except Exception as exc:
                logger.warning("Could not load %s: %s", path, exc)
        return loaded

    def _load_features(self) -> int:
        if not _PARQUET.exists():
            logger.debug("Feature store not found at %s", _PARQUET)
            return 0
        try:
            df = pd.read_parquet(_PARQUET)
        except Exception as exc:
            logger.warning("Could not read feature store: %s", exc)
            return 0

        # Fill optional feature sentinels
        for col, val in _SENTINEL_FILLS.items():
            if col in df.columns:
                df[col] = df[col].fillna(val)

        # Keep only rows where all required features are present
        required = [c for c in FEATURE_COLS if c not in _SENTINEL_FILLS]
        df = df.dropna(subset=required)
        if df.empty:
            return 0

        # For each symbol, keep the most recent row (highest date_key)
        latest = (
            df.sort_values("date_key")
            .groupby("symbol", sort=False)
            .tail(1)
            .set_index("symbol")
        )

        # Pre-compute feature vectors
        self._latest.clear()
        for symbol, row in latest.iterrows():
            try:
                vec = row[FEATURE_COLS].values.astype(float)
                if not np.any(np.isnan(vec)):
                    self._latest[symbol] = vec
            except Exception:
                pass

        return len(self._latest)

    # ------------------------------------------------------------------ #
    # Scoring                                                              #
    # ------------------------------------------------------------------ #

    def score(self, symbol: str) -> Optional[dict]:
        """
        Return ML probability scores for all three horizons.

        Returns None if models or features are not available for this symbol.

        The returned probabilities represent P(signal outcome = correct) for
        each horizon — i.e., the likelihood that a trade in the signal
        direction will be profitable at that time horizon.
        """
        if not self._ready:
            return None

        with self._lock:
            vec = self._latest.get(symbol.upper())
            if vec is None:
                return None

            X = vec.reshape(1, -1)
            scores: dict[str, float] = {}

            for target, label in _HORIZON_LABELS.items():
                model = self._models.get(target)
                if model is None:
                    continue
                try:
                    prob = float(model.predict_proba(X)[0, 1])
                    scores[label] = round(prob, 4)
                except Exception as exc:
                    logger.debug("Score failed for %s/%s: %s", symbol, target, exc)

            if not scores:
                return None

            best_label  = max(scores, key=scores.__getitem__)
            best_prob   = scores[best_label]

            return {
                **scores,
                "best":    best_prob,
                "horizon": best_label,
            }

    def confidence(self, symbol: str) -> Optional[float]:
        """
        Return a single confidence value (0–0.85) for use in the prediction dict.
        Uses the best horizon score, capped at 0.85 to preserve heuristic headroom.
        Returns None if ML is unavailable.
        """
        result = self.score(symbol)
        if result is None:
            return None
        # Clamp: 0.5 = chance level → map [0.5, 1.0] → [0.0, 0.85]
        raw = result["best"]
        mapped = max(0.0, (raw - 0.5) / 0.5 * 0.85)
        return round(min(mapped, 0.85), 4)

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def loaded_symbols(self) -> list[str]:
        return list(self._latest.keys())


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

scorer = MLScorer()
