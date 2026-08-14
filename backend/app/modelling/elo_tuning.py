"""Hyperparameter selection for the Elo model via a time-based split.

Selection scores each candidate config only on a "tune window" of matches
(e.g. 2016-2022) — never on the later "holdout window" (e.g. 2023-2025)
that final metrics get reported against. This matters for a reason
distinct from the walk-forward no-leakage guarantee in elo_backtest.py:
even though every individual walk-forward prediction is already
out-of-sample relative to its own match, picking the config that happens to
minimise error *on the exact games used to report accuracy* is its own,
subtler form of overfitting (to the hyperparameters, not the ratings). A
held-out window the tuning process never sees is the honest fix.
"""

from dataclasses import replace
from itertools import product

from app.modelling.elo import EloConfig
from app.modelling.elo_backtest import MatchResult, run_walk_forward
from app.modelling.metrics import brier_score

DEFAULT_GRID: dict[str, list] = {
    "k_factor": [15.0, 24.0, 32.0, 45.0],
    "home_advantage": [0.0, 25.0, 35.0, 50.0, 75.0],
    "use_margin_of_victory": [True, False],
    "season_carryover": [0.55, 0.75, 1.0],
}


def select_best_config(
    tune_matches: list[MatchResult], grid: dict[str, list] | None = None
) -> tuple[EloConfig, list[dict]]:
    """Runs every combination in `grid` over `tune_matches` only, scoring by
    Brier score. Returns (best_config, leaderboard) where leaderboard is
    sorted best-first.
    """
    grid = grid or DEFAULT_GRID
    keys = list(grid.keys())
    base = EloConfig()

    leaderboard = []
    for values in product(*grid.values()):
        config = replace(base, **dict(zip(keys, values)))
        predictions = run_walk_forward(tune_matches, config)
        score = brier_score(
            [p.home_win_probability for p in predictions],
            [p.actual_home_outcome for p in predictions],
        )
        leaderboard.append({"config": config, "tune_brier": score})

    leaderboard.sort(key=lambda row: row["tune_brier"])
    best_config = leaderboard[0]["config"]
    return best_config, leaderboard
