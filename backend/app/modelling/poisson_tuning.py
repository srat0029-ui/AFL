"""Hyperparameter selection for the Poisson model, via the same time-based
tune/holdout discipline as elo_tuning.py.

Scored on total-points MAE, not win-probability Brier score: total match
points is this model's flagship market (the user's own priority — "start
with match winner, line and total points because those should have the
strongest datasets" — and match winner is already Elo's job). A config that
predicts total points well will generally also predict margin reasonably,
since both derive from the same fitted λs; margin MAE is reported alongside
for the final config but isn't the selection criterion.
"""

from dataclasses import replace
from itertools import product

from app.modelling.metrics import mae
from app.modelling.poisson_backtest import run_walk_forward
from app.modelling.poisson_model import PoissonConfig
from app.modelling.types import MatchResult

DEFAULT_GRID: dict[str, list] = {
    "rolling_window_games": [22, 44, 66, 9999],  # 9999 ≈ expanding (whole history)
    "min_games_for_reliable_strength": [4, 6, 8],
    "min_league_games_for_home_split": [20, 40, 80],
    # None = unbounded expanding league-wide average (the ORIGINAL model's
    # exact behaviour — see poisson_backtest.py's _LeagueSplit docstring for
    # why this let 2020's shortened-quarter scoring shock distort 2021
    # predictions for months). The other values bound it to roughly a half,
    # full, or one-and-a-half AFL seasons, so a genuine scoring-environment
    # shift self-corrects within about that many matches instead of years.
    "league_window_games": [None, 100, 200, 300],
}


def select_best_config(
    tune_matches: list[MatchResult], grid: dict[str, list] | None = None
) -> tuple[PoissonConfig, list[dict]]:
    grid = grid or DEFAULT_GRID
    keys = list(grid.keys())
    base = PoissonConfig()

    leaderboard = []
    for values in product(*grid.values()):
        config = replace(base, **dict(zip(keys, values)))
        predictions = run_walk_forward(tune_matches, config)
        if not predictions:
            continue
        score = mae(
            [p.expected_total_points for p in predictions],
            [p.actual_total_points for p in predictions],
        )
        leaderboard.append({"config": config, "tune_total_points_mae": score})

    leaderboard.sort(key=lambda row: row["tune_total_points_mae"])
    best_config = leaderboard[0]["config"]
    return best_config, leaderboard
