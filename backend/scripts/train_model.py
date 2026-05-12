#!/usr/bin/env python3
"""
PSX Model Trainer
==================
CLI runner for ModelTrainer — trains walk-forward LightGBM classifiers
on the feature store and writes models + metrics to backend/data/models/.

Usage (from the backend/ directory):
  python -m scripts.train_model                           # all three targets
  python -m scripts.train_model --target target_short     # one target
  python -m scripts.train_model --test-months 2           # larger hold-out window
  python -m scripts.train_model --min-samples 500         # lower threshold (dev/testing)
  python -m scripts.train_model --verbose                 # debug logging
  python -m scripts.train_model --results                 # print saved metrics and exit
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("train_model")


def print_saved_results(models_dir: Path, targets: list[str]) -> None:
    for target in targets:
        metrics_path = models_dir / f"{target}_metrics.json"
        if not metrics_path.exists():
            logger.info("%s: no metrics file found", target)
            continue
        m = json.loads(metrics_path.read_text())
        logger.info("─── %s ───", target)
        logger.info("  trained_at       : %s", m.get("trained_at"))
        logger.info("  n_train_final    : %d", m.get("n_train_final", 0))
        logger.info("  n_folds          : %d", m.get("n_folds", 0))
        logger.info("  mean_roc_auc     : %.4f ± %.4f",
                    m.get("mean_roc_auc") or 0,
                    m.get("std_roc_auc")  or 0)
        logger.info("  mean_brier       : %.4f", m.get("mean_brier") or 0)
        logger.info("  mean_log_loss    : %.4f", m.get("mean_log_loss") or 0)
        logger.info("  precision@top10  : %.4f", m.get("mean_precision_top10") or 0)
        logger.info("  calibrated       : %s",   m.get("calibrated"))
        # Top-5 features
        fi = m.get("feature_importances", {})
        top5 = sorted(fi.items(), key=lambda x: x[1], reverse=True)[:5]
        logger.info("  top features     : %s", ", ".join(f"{k}={v}" for k, v in top5))


def main() -> None:
    from app.prediction.model_trainer import MODELS_DIR, TARGETS, ModelTrainer

    p = argparse.ArgumentParser(description="Train PSX signal prediction models")
    p.add_argument(
        "--target", choices=TARGETS, default=None,
        help="Train a specific target only (default: all three)",
    )
    p.add_argument(
        "--test-months", type=int, default=1,
        help="Walk-forward test window in months (default: 1 ≈ 22 trading days)",
    )
    p.add_argument(
        "--min-samples", type=int, default=2_000,
        help="Minimum labeled training samples required (default: 2000)",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging",
    )
    p.add_argument(
        "--results", action="store_true",
        help="Print saved model metrics and exit (no training)",
    )
    args = p.parse_args()

    if args.verbose:
        logging.getLogger("psx.model_trainer").setLevel(logging.DEBUG)
        logging.getLogger("lightgbm").setLevel(logging.DEBUG)

    targets = [args.target] if args.target else TARGETS

    if args.results:
        print_saved_results(MODELS_DIR, targets)
        return

    test_days = args.test_months * 22   # approximate trading days per month

    trainer = ModelTrainer(
        min_samples=args.min_samples,
        test_days=test_days,
    )

    results = trainer.train_all(targets=targets)

    print("\n══ Summary ══")
    for r in results:
        if r.skipped:
            print(f"  {r.target:<20} SKIPPED — {r.skip_reason}")
        else:
            auc  = f"{r.mean_roc_auc:.3f} ± {r.std_roc_auc:.3f}" if r.mean_roc_auc else "N/A"
            p10  = f"{r.mean_precision_top10:.3f}" if r.mean_precision_top10 else "N/A"
            print(f"  {r.target:<20} AUC={auc}  P@top10={p10}  folds={r.n_folds}  cal={'yes' if r.calibrated else 'no'}")


if __name__ == "__main__":
    main()
