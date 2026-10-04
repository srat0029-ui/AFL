"""Probability metrics for participation / rotation predictions.

Proper scoring rules (log loss, Brier) are primary: the classes are
imbalanced (about 82% of listings play), so accuracy at 0.5 says little.
"""

import numpy as np
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

CALIBRATION_BINS = 10
THRESHOLDS = (0.3, 0.5, 0.7, 0.9)


def prob_metrics(p, y) -> dict:
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, dtype=float)
    if np.isnan(p).any() or np.isnan(y).any():
        raise ValueError("metrics need complete probabilities and labels")
    return {
        "n": int(len(y)),
        "base_rate": float(y.mean()),
        "mean_p": float(p.mean()),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "brier": float(brier_score_loss(y, p)),
        "auc": float(roc_auc_score(y, p)) if 0 < y.mean() < 1 else None,
        "ece": expected_calibration_error(p, y),
    }


def reliability(p, y, bins: int = CALIBRATION_BINS) -> list[dict]:
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    out = []
    for b in range(bins):
        m = idx == b
        if m.any():
            out.append({"bin": f"{edges[b]:.1f}-{edges[b + 1]:.1f}", "n": int(m.sum()), "mean_p": float(p[m].mean()), "observed": float(y[m].mean())})
    return out


def expected_calibration_error(p, y, bins: int = CALIBRATION_BINS) -> float:
    n = len(p)
    return float(sum(r["n"] / n * abs(r["mean_p"] - r["observed"]) for r in reliability(p, y, bins)))


def confusion_at(p, y, thresholds=THRESHOLDS) -> dict:
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float).astype(bool)
    out = {}
    for t in thresholds:
        pred = p >= t
        tp, fp = int((pred & y).sum()), int((pred & ~y).sum())
        fn, tn = int((~pred & y).sum()), int((~pred & ~y).sum())
        out[str(t)] = {
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "specificity": tn / (tn + fp) if tn + fp else None,
            "negative_predictive_value": tn / (tn + fn) if tn + fn else None,
        }
    return out
