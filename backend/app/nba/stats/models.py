"""Baselines, the shrunk-rate mean and the count distributions.

Every parameter here (prior rates, half-life, shrinkage, dispersion) is learned
from earlier seasons by the caller. Nothing in this module sees an outcome from
the rows it predicts.
"""

import numpy as np
import pandas as pd
from scipy import stats as sps

from app.nba.stats.features import RECENT_MINUTES_HALFLIFE

BASELINES = ("season_to_date", "last_10", "ewma_4", "ewma_10", "rate_ewma")
HALFLIFE_GRID = (10, 25, 50)
SHRINK_GRID = (20.0, 50.0, 100.0, 200.0)
RATE_EWMA_HALFLIFE = 25
MINUTES_BAND_EDGES = [-0.1, 15.0, 25.0, 32.0, 100.0]
MINUTES_BAND_LABELS = ("<15", "15-25", "25-32", "32+")
MEAN_BUCKET_EDGES = np.array([2.0, 5.0, 10.0, 15.0, 20.0, 30.0])
MIN_MEAN = 0.05  # a Poisson or NB mean of 0 is degenerate
MIN_ALPHA = 1e-4
MIN_BUCKET_ROWS = 200
PMF_GRID_MAX = 121  # no modelled stat reaches this; the highest tail is negligible


def baseline_mean(frame: pd.DataFrame, stat: str, name: str) -> pd.Series:
    if name == "season_to_date":
        pred = frame[f"{stat}_season"].fillna(frame[f"{stat}_last_n"])
    elif name == "last_10":
        pred = frame[f"{stat}_last_n"]
    elif name == "ewma_4":
        pred = frame[f"{stat}_ewm4"]
    elif name == "ewma_10":
        pred = frame[f"{stat}_ewm10"]
    elif name == "rate_ewma":
        h = RATE_EWMA_HALFLIFE
        minutes = frame[f"min_ewm{h}"]
        rate = (frame[f"{stat}_ewm{h}"] / minutes.where(minutes > 0)).fillna(0.0)
        pred = rate * frame[f"min_ewm{RECENT_MINUTES_HALFLIFE}"]
    else:
        raise ValueError(f"unknown baseline {name!r}")
    return pred.astype(float)


def minutes_band(frame: pd.DataFrame) -> pd.Series:
    """The player's band from his recent minutes, known before the game."""
    return pd.cut(frame[f"min_ewm{RECENT_MINUTES_HALFLIFE}"].fillna(0.0), MINUTES_BAND_EDGES, labels=MINUTES_BAND_LABELS).astype(str)


def fit_prior_rates(train: pd.DataFrame, stat: str) -> dict[str, float]:
    """League stat per minute by recent-minutes band, from played training rows."""
    bands = minutes_band(train)
    sums = train.groupby(bands, observed=True)[[f"actual_{stat}", "minutes"]].sum()
    overall = float(train[f"actual_{stat}"].sum() / train["minutes"].sum())
    rates = (sums[f"actual_{stat}"] / sums["minutes"].where(sums["minutes"] > 0)).to_dict()
    return {band: float(rates.get(band, overall)) if not pd.isna(rates.get(band, overall)) else overall for band in MINUTES_BAND_LABELS}


def shrunk_rate(frame: pd.DataFrame, stat: str, prior: dict[str, float], halflife: int, shrink: float) -> pd.Series:
    """(decayed stat + k x prior) / (decayed minutes + k)."""
    weight = frame[f"weight{halflife}"]
    prior_rate = minutes_band(frame).map(prior).astype(float)
    return (frame[f"{stat}_ewm{halflife}"] * weight + shrink * prior_rate) / (frame[f"min_ewm{halflife}"] * weight + shrink)


# --- distributions -----------------------------------------------------------


def _bucket(mu: np.ndarray) -> np.ndarray:
    return np.searchsorted(MEAN_BUCKET_EDGES, mu, side="left")


def _moment_alpha(y: np.ndarray, mu: np.ndarray) -> float:
    return float(max(((y - mu) ** 2 - mu).sum() / (mu**2).sum(), MIN_ALPHA))


def fit_alpha_single(y, mu) -> float:
    return _moment_alpha(np.asarray(y, dtype=float), np.clip(np.asarray(mu, dtype=float), MIN_MEAN, None))


def fit_alpha_buckets(y, mu) -> dict[int, float]:
    """alpha by predicted-mean bucket. A bucket with too few rows borrows the
    pooled value rather than producing a noisy one."""
    y = np.asarray(y, dtype=float)
    mu = np.clip(np.asarray(mu, dtype=float), MIN_MEAN, None)
    pooled = _moment_alpha(y, mu)
    buckets = _bucket(mu)
    return {b: (_moment_alpha(y[buckets == b], mu[buckets == b]) if (buckets == b).sum() >= MIN_BUCKET_ROWS else pooled) for b in range(len(MEAN_BUCKET_EDGES) + 1)}


def alpha_for(mu, alphas: dict[int, float]) -> np.ndarray:
    return np.vectorize(alphas.__getitem__, otypes=[float])(_bucket(np.clip(np.asarray(mu, dtype=float), MIN_MEAN, None)))


def _nb_args(mu: np.ndarray, alpha: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = 1.0 / alpha
    return n, n / (n + mu)


def cdf(k, mu, alpha=None) -> np.ndarray:
    """P(X <= k). alpha None is Poisson."""
    mu = np.clip(np.asarray(mu, dtype=float), MIN_MEAN, None)
    if alpha is None:
        return sps.poisson.cdf(k, mu)
    n, p = _nb_args(mu, np.broadcast_to(np.asarray(alpha, dtype=float), mu.shape))
    return sps.nbinom.cdf(k, n, p)


def log_pmf(y, mu, alpha=None) -> np.ndarray:
    mu = np.clip(np.asarray(mu, dtype=float), MIN_MEAN, None)
    if alpha is None:
        return sps.poisson.logpmf(y, mu)
    n, p = _nb_args(mu, np.broadcast_to(np.asarray(alpha, dtype=float), mu.shape))
    return sps.nbinom.logpmf(y, n, p)


def prob_at_least(threshold, mu, alpha=None) -> np.ndarray:
    return 1.0 - cdf(np.asarray(threshold, dtype=float) - 1.0, mu, alpha)


def crps(y, mu, alpha=None, *, chunk: int = 20000) -> np.ndarray:
    """Discrete CRPS per row: sum over k of (F(k) - 1[y <= k])^2."""
    y = np.asarray(y, dtype=float)
    mu = np.clip(np.asarray(mu, dtype=float), MIN_MEAN, None)
    alpha_arr = None if alpha is None else np.broadcast_to(np.asarray(alpha, dtype=float), mu.shape)
    grid = np.arange(PMF_GRID_MAX, dtype=float)
    out = np.empty(len(y))
    for start in range(0, len(y), chunk):
        stop = start + chunk
        m = mu[start:stop, None]
        if alpha_arr is None:
            f = sps.poisson.cdf(grid[None, :], m)
        else:
            a = alpha_arr[start:stop, None]
            n, p = _nb_args(m, a)
            f = sps.nbinom.cdf(grid[None, :], n, p)
        out[start:stop] = ((f - (y[start:stop, None] <= grid)) ** 2).sum(axis=1)
    return out


def randomised_pit(y, mu, alpha=None, *, seed: int = 11) -> np.ndarray:
    """Uniform on [0, 1] if the distribution is right, including for counts."""
    y = np.asarray(y, dtype=float)
    upper = cdf(y, mu, alpha)
    lower = cdf(y - 1.0, mu, alpha)
    return lower + np.random.default_rng(seed).random(len(y)) * (upper - lower)
