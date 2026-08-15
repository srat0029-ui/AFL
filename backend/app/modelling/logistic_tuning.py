"""Regularisation-strength selection for the logistic regression model, via
an *inner* chronological split entirely within the tune window — never
touching the final evaluation-period data.

This differs from elo_tuning.py/poisson_tuning.py, which score a config by
walk-forward replay over the whole tune window (legitimate there because
Elo/Poisson update online, game by game, so even a prediction generated
"inside" the tune window only ever used strictly-earlier state). Logistic
regression is fit once to a static set of coefficients — scoring it on the
exact rows it was fit on would be in-sample and overfitting-prone, not a
genuine out-of-sample check. The fix is an ordinary inner train/validate
split: fit on the earlier part of the tune window, score candidate C values
on the later part, then refit on the *whole* tune window with the winning C
for the model actually used in evaluation.
"""

from app.modelling.features import MatchFeatureRow
from app.modelling.logistic import LogisticConfig, fit_logistic_model, predict
from app.modelling.metrics import brier_score

DEFAULT_C_GRID = [0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0]


def select_best_C(
    tune_rows: list[MatchFeatureRow],
    feature_names: tuple[str, ...],
    inner_validation_start_year: int,
    C_grid: list[float] | None = None,
) -> tuple[float, list[dict]]:
    """Splits `tune_rows` at `inner_validation_start_year`: everything
    before is inner-train, everything from that year on (but still within
    the tune window the caller passed in) is inner-validation. Returns
    (best_C, leaderboard) sorted best-first by inner-validation Brier
    score. Rows without full rolling history are excluded from both sides
    — an imputed all-13-features row contributes noise, not signal, to a
    hyperparameter decision.
    """
    C_grid = C_grid or DEFAULT_C_GRID
    inner_train = [r for r in tune_rows if r.season_year < inner_validation_start_year and r.has_full_history]
    inner_val = [r for r in tune_rows if r.season_year >= inner_validation_start_year and r.has_full_history]

    leaderboard = []
    for C in C_grid:
        config = LogisticConfig(feature_names=feature_names, C=C)
        pipeline = fit_logistic_model(inner_train, config)
        preds = predict(pipeline, inner_val, config.feature_names)
        score = brier_score([p.home_win_probability for p in preds], [p.actual_home_outcome for p in preds])
        leaderboard.append({"C": C, "inner_val_brier": score, "n_train": len(inner_train), "n_val": len(inner_val)})

    leaderboard.sort(key=lambda row: row["inner_val_brier"])
    return leaderboard[0]["C"], leaderboard
