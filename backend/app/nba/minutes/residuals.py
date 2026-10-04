"""Error-distribution analysis and the V1 uncertainty table.

Residual = actual - predicted (positive: he played MORE than predicted).

V1 does not assume normal errors. Its uncertainty is an empirical table:
quantiles of OUT-OF-SAMPLE residuals (walk-forward predictions, never
in-sample fits) within bands of the predicted value, so the spread is
allowed to differ between a 12-minute bench player and a 34-minute starter.
A prediction's quantiles are prediction + residual quantile, clipped to
[0, 60].
"""

import numpy as np
import pandas as pd
from scipy import stats

PRED_BANDS = [0.0, 10.0, 18.0, 24.0, 30.0, 34.0, 61.0]
BAND_LABELS = ["<10", "10-18", "18-24", "24-30", "30-34", "34+"]
QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)


def pred_band(pred) -> pd.Series:
    return pd.cut(pd.Series(np.asarray(pred, dtype=float)), PRED_BANDS, labels=BAND_LABELS, right=False)


def describe_residuals(resid) -> dict:
    r = np.asarray(resid, dtype=float)
    q = np.quantile(r, [0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99])
    jb = stats.jarque_bera(r)
    return {
        "n": int(len(r)),
        "mean": float(r.mean()),
        "std": float(r.std(ddof=1)),
        "skew": float(stats.skew(r)),
        "excess_kurtosis": float(stats.kurtosis(r)),
        "quantiles": {f"p{int(round(p * 100)):02d}": float(v) for p, v in zip((0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99), q)},
        "jarque_bera_stat": float(jb.statistic),
        "jarque_bera_p": float(jb.pvalue),
    }


def by_group(resid, groups) -> dict:
    frame = pd.DataFrame({"r": np.asarray(resid, dtype=float), "g": np.asarray(groups)})
    out = {}
    for key, g in frame.groupby("g", observed=True):
        r = g["r"].to_numpy()
        out[str(key)] = {
            "n": int(len(r)),
            "mean": float(r.mean()),
            "std": float(r.std(ddof=1)) if len(r) > 1 else None,
            "mae": float(np.abs(r).mean()),
            **{f"p{int(q * 100):02d}": float(np.quantile(r, q)) for q in QUANTILES},
        }
    return out


def fit_uncertainty_table(pred, actual) -> dict:
    """Residual quantiles per predicted-minutes band, from out-of-sample
    predictions."""
    resid = np.asarray(actual, dtype=float) - np.asarray(pred, dtype=float)
    bands = pred_band(pred)
    table = {}
    for label in BAND_LABELS:
        r = resid[(bands == label).to_numpy()]
        if len(r) < 200:
            raise ValueError(f"band {label} has only {len(r)} residuals - too few for an empirical table")
        table[label] = {f"p{int(q * 100):02d}": float(np.quantile(r, q)) for q in QUANTILES} | {"n": int(len(r)), "std": float(r.std(ddof=1))}
    return {"bands": PRED_BANDS, "labels": BAND_LABELS, "table": table, "kind": "empirical_residual_quantiles_by_predicted_band"}


def apply_uncertainty(pred, table: dict) -> pd.DataFrame:
    pred = np.asarray(pred, dtype=float)
    bands = pred_band(pred)
    out = {}
    for q in QUANTILES:
        key = f"p{int(q * 100):02d}"
        offsets = bands.map({label: table["table"][label][key] for label in table["labels"]}).astype(float).to_numpy()
        out[key] = np.clip(pred + offsets, 0.0, 60.0)
    return pd.DataFrame(out)


def interval_coverage(pred, actual, table: dict) -> dict:
    """How often the actual minutes fell inside the table's 50% (p25-p75) and
    80% (p10-p90) intervals - compared with what a normal distribution with
    each band's residual standard deviation would have claimed."""
    pred = np.asarray(pred, dtype=float)
    actual = np.asarray(actual, dtype=float)
    q = apply_uncertainty(pred, table)
    bands = pred_band(pred)
    sd = bands.map({label: table["table"][label]["std"] for label in table["labels"]}).astype(float).to_numpy()
    z80, z50 = stats.norm.ppf(0.90), stats.norm.ppf(0.75)
    return {
        "empirical_80": float(((actual >= q["p10"]) & (actual <= q["p90"])).mean()),
        "empirical_50": float(((actual >= q["p25"]) & (actual <= q["p75"])).mean()),
        "normal_80": float((np.abs(actual - pred) <= z80 * sd).mean()),
        "normal_50": float((np.abs(actual - pred) <= z50 * sd).mean()),
        "n": int(len(actual)),
    }
