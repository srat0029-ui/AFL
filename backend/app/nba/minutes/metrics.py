"""Point-error metrics for minutes predictions.

Sign convention: error = predicted - actual, so a positive bias means the
model predicts too many minutes.
"""

import numpy as np
import pandas as pd

WITHIN_THRESHOLDS = (2.0, 4.0, 6.0)


def point_metrics(predicted, actual) -> dict:
    predicted = np.asarray(predicted, dtype=float)
    actual = np.asarray(actual, dtype=float)
    if predicted.shape != actual.shape:
        raise ValueError("predicted and actual must have the same shape")
    if np.isnan(predicted).any() or np.isnan(actual).any():
        raise ValueError("metrics require complete predictions and outcomes; handle coverage before calling")
    n = len(actual)
    if n == 0:
        return {"n": 0}
    err = predicted - actual
    abs_err = np.abs(err)
    out = {
        "n": int(n),
        "mae": float(abs_err.mean()),
        "rmse": float(np.sqrt((err**2).mean())),
        "bias": float(err.mean()),
        "median_ae": float(np.median(abs_err)),
    }
    for t in WITHIN_THRESHOLDS:
        out[f"within_{int(t)}"] = float((abs_err <= t).mean())
    return out


def breakdown(frame: pd.DataFrame, pred_col: str, actual_col: str, group_col: str) -> dict:
    return {str(k): point_metrics(g[pred_col], g[actual_col]) for k, g in frame.groupby(group_col, observed=True)}
