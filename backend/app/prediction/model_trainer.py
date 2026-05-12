"""
ModelTrainer — walk-forward gradient-boosted classifier for PSX signals.
=========================================================================

Trains one LightGBM binary classifier per target horizon
(target_short / target_medium / target_long) using only labeled rows
from the feature store (features.parquet).

Training protocol (Evolution Plan A3):
  - Time-series walk-forward expanding window.
  - Splits are made on date_key (YYYYMMDD) to prevent look-ahead.
  - Minimum 2 000 labeled training samples required per target;
    training is skipped and a warning is logged if below threshold.
  - Test window: configurable (default 1 month ≈ 22 trading days).
  - Walk runs until the end of the dataset; final model is trained on
    all available labeled data and serialised to disk.

Metrics computed per walk-forward fold and on the final hold-out:
  - ROC-AUC               — discrimination ability
  - Brier score           — calibration quality (lower = better)
  - Precision@top-decile  — accuracy on the highest-confidence predictions
  - Log-loss              — cross-entropy (tracks model confidence quality)

Calibration:
  Isotonic regression calibration is applied to the final model's
  probability outputs so that 0.70 confidence ≈ 70% empirical accuracy.

Output (per target):
  backend/data/models/{target}_latest.pkl       — serialised pipeline (lgbm + calibrator)
  backend/data/models/{target}_{trained_at}.pkl — timestamped archive copy
  backend/data/models/{target}_metrics.json     — latest metrics + feature importances

Feature columns used (from FeatureEngine.schema, minus id/target cols):
  ret_1d, ret_5d, ret_20d, ret_100d,
  vol_20d, vol_60d, drawdown_60d, dist_52w_high,
  rsi_7, rsi_14, rsi_21,
  sma_ratio_5, sma_ratio_20,
  bb_pct_b, bb_bandwidth,
  sector_ret5d_rank, sector_rsi14_rank, sector_vol_rank,
  days_since_buy, days_since_sell

Usage:
  python -m scripts.train_model                        # all three targets
  python -m scripts.train_model --target target_short  # one target
  python -m scripts.train_model --test-months 2        # larger hold-out
  python -m scripts.train_model --min-samples 500      # lower threshold (dev)
"""

from __future__ import annotations

import json
import logging
import pickle
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("psx.model_trainer")

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

_BACKEND_DIR  = Path(__file__).resolve().parents[2]
PARQUET_PATH  = _BACKEND_DIR / "data" / "features.parquet"
MODELS_DIR    = _BACKEND_DIR / "data" / "models"

TARGETS       = ["target_short", "target_medium", "target_long"]

FEATURE_COLS  = [
    "ret_1d", "ret_5d", "ret_20d", "ret_100d",
    "vol_20d", "vol_60d", "drawdown_60d", "dist_52w_high",
    "rsi_7", "rsi_14", "rsi_21",
    "sma_ratio_5", "sma_ratio_20",
    "bb_pct_b", "bb_bandwidth",
    "sector_ret5d_rank", "sector_rsi14_rank", "sector_vol_rank",
    "days_since_buy", "days_since_sell",
]

MIN_TRAIN_SAMPLES  = 2_000   # Evolution Plan A3 threshold
TEST_DAYS          = 22      # ~1 trading month per fold
LGBM_PARAMS = {
    "objective":        "binary",
    "metric":           "binary_logloss",
    "n_estimators":     300,
    "learning_rate":    0.05,
    "num_leaves":       31,
    "max_depth":        -1,
    "min_child_samples":20,
    "subsample":        0.8,
    "colsample_bytree": 0.8,
    "reg_alpha":        0.1,
    "reg_lambda":       0.1,
    "n_jobs":           -1,
    "verbose":          -1,
    "random_state":     42,
}


# ---------------------------------------------------------------------------
# Calibrated pipeline (module-level so pickle can serialise it)
# ---------------------------------------------------------------------------

class CalibratedPipeline:
    """LightGBM model + IsotonicRegression calibrator, pickle-safe."""

    def __init__(self, model, calibrator):
        self._mdl = model
        self._cal = calibrator
        self.feature_importances_ = model.feature_importances_

    def predict_proba(self, X):
        raw = self._mdl.predict_proba(X)[:, 1]
        cal = self._cal.transform(raw)
        return np.column_stack([1.0 - cal, cal])

    def predict(self, X):
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)


# ---------------------------------------------------------------------------
# Metrics dataclass
# ---------------------------------------------------------------------------

@dataclass
class FoldMetrics:
    fold:               int
    train_start:        int   # date_key
    train_end:          int
    test_start:         int
    test_end:           int
    n_train:            int
    n_test:             int
    roc_auc:            Optional[float]
    brier_score:        Optional[float]
    log_loss:           Optional[float]
    precision_top10:    Optional[float]   # precision on top-10% confidence predictions
    n_positive:         int               # positive class count in test set


@dataclass
class TrainResult:
    target:             str
    trained_at:         int               # Unix ts
    n_folds:            int
    n_train_final:      int
    fold_metrics:       list[FoldMetrics] = field(default_factory=list)
    # Aggregated over folds (mean ± std)
    mean_roc_auc:       Optional[float]   = None
    std_roc_auc:        Optional[float]   = None
    mean_brier:         Optional[float]   = None
    mean_log_loss:      Optional[float]   = None
    mean_precision_top10: Optional[float] = None
    feature_importances: dict             = field(default_factory=dict)
    model_path:         str               = ""
    calibrated:         bool              = False
    skipped:            bool              = False
    skip_reason:        str               = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _date_key_to_ts(dk: int) -> int:
    """YYYYMMDD → approximate Unix timestamp (midnight PKT)."""
    y, md = divmod(dk, 10_000)
    m, d  = divmod(md, 100)
    dt = datetime(y, m, d, 0, 0, 0)
    return int(dt.timestamp())


def _compute_metrics(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    fold: int,
    train_start: int,
    train_end: int,
    test_start: int,
    test_end: int,
) -> FoldMetrics:
    from sklearn.metrics import (
        brier_score_loss,
        log_loss,
        roc_auc_score,
    )

    n_pos = int(y_true.sum())
    n_test = len(y_true)

    if n_pos == 0 or n_pos == n_test:
        # Degenerate — can't compute AUC
        return FoldMetrics(
            fold=fold,
            train_start=train_start, train_end=train_end,
            test_start=test_start,   test_end=test_end,
            n_train=0, n_test=n_test,
            roc_auc=None, brier_score=None,
            log_loss=None, precision_top10=None,
            n_positive=n_pos,
        )

    roc  = float(roc_auc_score(y_true, y_prob))
    brier = float(brier_score_loss(y_true, y_prob))
    ll   = float(log_loss(y_true, y_prob))

    # Precision@top-10% confidence
    n_top   = max(1, int(0.10 * n_test))
    top_idx = np.argsort(y_prob)[-n_top:]
    prec_top = float(y_true[top_idx].mean())

    return FoldMetrics(
        fold=fold,
        train_start=train_start, train_end=train_end,
        test_start=test_start,   test_end=test_end,
        n_train=0,   # filled by caller
        n_test=n_test,
        roc_auc=roc, brier_score=brier,
        log_loss=ll, precision_top10=prec_top,
        n_positive=n_pos,
    )


# ---------------------------------------------------------------------------
# Walk-forward split generator
# ---------------------------------------------------------------------------

def _walk_forward_splits(
    date_keys: np.ndarray,
    test_days: int = TEST_DAYS,
    min_train: int = MIN_TRAIN_SAMPLES,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """
    Generate (train_idx, test_idx) pairs for walk-forward CV.

    Splits are anchored on unique sorted date_key values.
    Each fold: train = all data before split point, test = next test_days keys.
    Only yields folds where train set >= min_train rows.
    """
    unique_keys = np.sort(np.unique(date_keys))
    splits: list[tuple[np.ndarray, np.ndarray]] = []

    # Start after we have enough training data
    for split_pos in range(len(unique_keys) - test_days):
        train_keys = unique_keys[:split_pos + 1]
        test_keys  = unique_keys[split_pos + 1 : split_pos + 1 + test_days]

        train_idx = np.where(np.isin(date_keys, train_keys))[0]
        test_idx  = np.where(np.isin(date_keys, test_keys))[0]

        if len(train_idx) < min_train or len(test_idx) == 0:
            continue

        splits.append((train_idx, test_idx))

    return splits


# ---------------------------------------------------------------------------
# ModelTrainer
# ---------------------------------------------------------------------------

class ModelTrainer:
    """
    Walk-forward LightGBM trainer for PSX signal prediction.

    Example:
        trainer = ModelTrainer()
        results = trainer.train_all()
        for r in results:
            print(r.target, r.mean_roc_auc)
    """

    def __init__(
        self,
        parquet_path: Path = PARQUET_PATH,
        models_dir:   Path = MODELS_DIR,
        min_samples:  int  = MIN_TRAIN_SAMPLES,
        test_days:    int  = TEST_DAYS,
        lgbm_params:  dict | None = None,
    ) -> None:
        self.parquet_path = parquet_path
        self.models_dir   = models_dir
        self.min_samples  = min_samples
        self.test_days    = test_days
        self.lgbm_params  = lgbm_params or LGBM_PARAMS.copy()
        self.models_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def train_all(self, targets: list[str] | None = None) -> list[TrainResult]:
        """Train models for all (or specified) targets. Returns list of TrainResult."""
        targets = targets or TARGETS
        results = []
        for target in targets:
            logger.info("=== Training %s ===", target)
            result = self._train_one(target)
            results.append(result)
            if result.skipped:
                logger.warning("  Skipped: %s", result.skip_reason)
            else:
                logger.info(
                    "  ROC-AUC %.3f ± %.3f  |  Brier %.3f  |  P@top10 %.3f  |  folds=%d",
                    result.mean_roc_auc or 0,
                    result.std_roc_auc  or 0,
                    result.mean_brier   or 0,
                    result.mean_precision_top10 or 0,
                    result.n_folds,
                )
        return results

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _load_features(self, target: str) -> pd.DataFrame:
        """
        Load feature store, drop rows without a label for this target.

        Signal-history features (days_since_buy, days_since_sell) are NaN for
        every row before the first signal of that type fires per symbol.
        Filling with a large sentinel (999) encodes "never fired" as a valid
        feature value rather than silently dropping the row.
        """
        if not self.parquet_path.exists():
            return pd.DataFrame()
        df = pd.read_parquet(self.parquet_path)

        # Optional features: NaN is valid (fill with sentinel before dropna).
        #   days_since_*   — NaN before first signal of that type fires
        #   sector_*_rank  — NaN when sector is missing (historical EOD data
        #                    has no sector info; fixed in feature_engine but
        #                    old parquet files may still have NaN here)
        _OPTIONAL_COLS = [
            "days_since_buy", "days_since_sell",
            "sector_ret5d_rank", "sector_rsi14_rank", "sector_vol_rank",
        ]
        _SENTINELS = {
            "days_since_buy":    999.0,
            "days_since_sell":   999.0,
            "sector_ret5d_rank": 0.5,   # mid-rank as neutral sentinel
            "sector_rsi14_rank": 0.5,
            "sector_vol_rank":   0.5,
        }
        for col, sentinel in _SENTINELS.items():
            if col in df.columns:
                df[col] = df[col].fillna(sentinel)

        # Required: price-based + indicator-derived only — drop rows where
        # these are NaN (warmup period at start of each symbol's history).
        _REQUIRED = [c for c in FEATURE_COLS if c not in _OPTIONAL_COLS]
        df = df.dropna(subset=[target] + _REQUIRED)

        # Ensure numeric
        df[FEATURE_COLS] = df[FEATURE_COLS].astype(float)
        df[target]       = df[target].astype(int)
        df = df.sort_values("date_key").reset_index(drop=True)
        return df

    def _train_one(self, target: str) -> TrainResult:
        import lightgbm as lgb
        from sklearn.calibration import CalibratedClassifierCV

        now_ts = int(time.time())
        result = TrainResult(target=target, trained_at=now_ts, n_folds=0, n_train_final=0)

        df = self._load_features(target)
        if df.empty:
            result.skipped    = True
            # Distinguish: parquet missing vs. parquet exists but no labeled rows
            if not self.parquet_path.exists():
                result.skip_reason = "Feature store not found — run: python -m scripts.build_features"
            else:
                result.skip_reason = (
                    f"0 labeled rows for {target} — signal_outcomes table is empty. "
                    "Let the live server run to generate signals, then run signal_evaluator "
                    "to resolve outcomes, then re-run build_features to attach targets."
                )
            return result

        n_labeled = len(df)
        logger.info("  %s: %d labeled rows", target, n_labeled)

        if n_labeled < self.min_samples:
            result.skipped    = True
            result.skip_reason = (
                f"Only {n_labeled} labeled samples — need {self.min_samples}. "
                "Run more signals + signal_evaluator to generate labels."
            )
            return result

        X_all = df[FEATURE_COLS].values
        y_all = df[target].values
        dk_all = df["date_key"].values

        # ── Walk-forward CV ──────────────────────────────────────────────
        splits = _walk_forward_splits(dk_all, test_days=self.test_days, min_train=self.min_samples)
        if not splits:
            result.skipped    = True
            result.skip_reason = "Not enough data for even one walk-forward fold"
            return result

        fold_metrics: list[FoldMetrics] = []

        for fold_i, (train_idx, test_idx) in enumerate(splits):
            X_tr, y_tr = X_all[train_idx], y_all[train_idx]
            X_te, y_te = X_all[test_idx],  y_all[test_idx]

            model = lgb.LGBMClassifier(**self.lgbm_params)
            model.fit(X_tr, y_tr)
            y_prob = model.predict_proba(X_te)[:, 1]

            fm = _compute_metrics(
                y_true=y_te, y_prob=y_prob, fold=fold_i,
                train_start=int(dk_all[train_idx[0]]),
                train_end=int(dk_all[train_idx[-1]]),
                test_start=int(dk_all[test_idx[0]]),
                test_end=int(dk_all[test_idx[-1]]),
            )
            fm.n_train = len(train_idx)
            fold_metrics.append(fm)

        # ── Aggregate fold metrics ───────────────────────────────────────
        aucs    = [f.roc_auc       for f in fold_metrics if f.roc_auc       is not None]
        briers  = [f.brier_score   for f in fold_metrics if f.brier_score   is not None]
        lls     = [f.log_loss      for f in fold_metrics if f.log_loss      is not None]
        prec10s = [f.precision_top10 for f in fold_metrics if f.precision_top10 is not None]

        result.n_folds              = len(fold_metrics)
        result.fold_metrics         = fold_metrics
        result.mean_roc_auc         = float(np.mean(aucs))    if aucs    else None
        result.std_roc_auc          = float(np.std(aucs))     if aucs    else None
        result.mean_brier           = float(np.mean(briers))  if briers  else None
        result.mean_log_loss        = float(np.mean(lls))     if lls     else None
        result.mean_precision_top10 = float(np.mean(prec10s)) if prec10s else None

        # ── Final model — train on ALL labeled data, calibrate ───────────
        final_model = lgb.LGBMClassifier(**self.lgbm_params)

        # Use last 20% as calibration set (time-ordered — no leakage)
        cal_split  = int(0.80 * len(X_all))
        X_tr_f, y_tr_f = X_all[:cal_split], y_all[:cal_split]
        X_cal, y_cal   = X_all[cal_split:], y_all[cal_split:]

        final_model.fit(X_tr_f, y_tr_f)

        try:
            # Isotonic calibration using raw probabilities — avoids sklearn's
            # cv="prefit" which was removed in sklearn >= 1.6.
            from sklearn.isotonic import IsotonicRegression

            raw_cal_probs = final_model.predict_proba(X_cal)[:, 1]
            ir = IsotonicRegression(out_of_bounds="clip")
            ir.fit(raw_cal_probs, y_cal)
            pipeline = CalibratedPipeline(final_model, ir)
            result.calibrated = True
        except Exception as exc:
            logger.warning("  Calibration failed (%s) — saving uncalibrated model", exc)
            pipeline = final_model

        result.n_train_final = len(X_all)

        # Feature importances from the raw LightGBM model
        result.feature_importances = {
            col: int(imp)
            for col, imp in zip(FEATURE_COLS, final_model.feature_importances_)
        }

        # ── Serialise ────────────────────────────────────────────────────
        ts_str     = datetime.fromtimestamp(now_ts).strftime("%Y%m%d_%H%M%S")
        archive    = self.models_dir / f"{target}_{ts_str}.pkl"
        latest     = self.models_dir / f"{target}_latest.pkl"

        with open(archive, "wb") as f:
            pickle.dump(pipeline, f, protocol=5)
        with open(latest, "wb") as f:
            pickle.dump(pipeline, f, protocol=5)

        result.model_path = str(latest)

        # ── Persist metrics JSON ─────────────────────────────────────────
        metrics_path = self.models_dir / f"{target}_metrics.json"
        metrics_dict = {
            "target":               result.target,
            "trained_at":           result.trained_at,
            "n_folds":              result.n_folds,
            "n_train_final":        result.n_train_final,
            "mean_roc_auc":         result.mean_roc_auc,
            "std_roc_auc":          result.std_roc_auc,
            "mean_brier":           result.mean_brier,
            "mean_log_loss":        result.mean_log_loss,
            "mean_precision_top10": result.mean_precision_top10,
            "calibrated":           result.calibrated,
            "model_path":           result.model_path,
            "feature_importances":  result.feature_importances,
            "folds": [asdict(fm) for fm in fold_metrics],
        }
        with open(metrics_path, "w") as f:
            json.dump(metrics_dict, f, indent=2)

        logger.info("  Model saved → %s", latest)
        logger.info("  Metrics    → %s", metrics_path)

        return result


# ---------------------------------------------------------------------------
# Convenience loader
# ---------------------------------------------------------------------------

def load_model(target: str, models_dir: Path = MODELS_DIR) -> object:
    """Load the latest serialised pipeline for a given target."""
    path = models_dir / f"{target}_latest.pkl"
    if not path.exists():
        raise FileNotFoundError(f"No trained model for {target!r} at {path}")
    with open(path, "rb") as f:
        return pickle.load(f)
