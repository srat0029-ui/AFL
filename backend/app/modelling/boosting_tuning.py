"""Hyperparameter selection for gradient boosting, via the same inner
chronological split as app/modelling/logistic_tuning.py — fit on the
earlier part of the tune window, score candidates on the later part, never
touch the final evaluation-period data. See that module's docstring for
why a static model needs this rather than Elo/Poisson's online scoring.

Deliberately modest: a handful of max_depth/learning_rate/n_estimators
combinations, everything else pinned to conservative values (some
subsampling, moderate min-child-weight, L2 regularisation) — the Stage
brief is explicit about not running a brute-force search on a dataset this
small. Tuned once per library on a single representative feature set
(features.py's richest set, stats + Elo) rather than once per (library,
feature set) pair — the tuned architecture is then reused unchanged when
comparing feature sets in app/backtesting/boosting_report.py, so that
comparison isolates the effect of the *features*, not a moving target of
re-tuned hyperparameters per set.
"""

from app.modelling.boosting import BoostingConfig, fit_boosting_model, predict
from app.modelling.features import MatchFeatureRow
from app.modelling.metrics import brier_score

DEFAULT_GRID: dict[str, list] = {
    "max_depth": [2, 3, 4],
    "learning_rate": [0.03, 0.1],
    "n_estimators": [75, 150],
}

# Conservative, fixed regardless of the grid search above — small-sample
# AFL data doesn't support tuning every knob at once.
_FIXED_DEFAULTS = {
    "min_child_weight": 5.0,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_alpha": 0.0,
    "reg_lambda": 1.0,
}


def select_best_boosting_config(
    library: str,
    tune_rows: list[MatchFeatureRow],
    feature_names: tuple[str, ...],
    inner_validation_start_year: int,
    grid: dict[str, list] | None = None,
) -> tuple[BoostingConfig, list[dict]]:
    grid = grid or DEFAULT_GRID
    inner_train = [r for r in tune_rows if r.season_year < inner_validation_start_year and r.has_full_history]
    inner_val = [r for r in tune_rows if r.season_year >= inner_validation_start_year and r.has_full_history]

    leaderboard = []
    for max_depth in grid["max_depth"]:
        for learning_rate in grid["learning_rate"]:
            for n_estimators in grid["n_estimators"]:
                config = BoostingConfig(
                    library=library,
                    feature_names=feature_names,
                    max_depth=max_depth,
                    learning_rate=learning_rate,
                    n_estimators=n_estimators,
                    **_FIXED_DEFAULTS,
                )
                model = fit_boosting_model(inner_train, config)
                preds = predict(model, inner_val, feature_names)
                score = brier_score([p.home_win_probability for p in preds], [p.actual_home_outcome for p in preds])
                leaderboard.append(
                    {
                        "max_depth": max_depth, "learning_rate": learning_rate, "n_estimators": n_estimators,
                        "inner_val_brier": score, "n_train": len(inner_train), "n_val": len(inner_val),
                    }
                )

    leaderboard.sort(key=lambda row: row["inner_val_brier"])
    best = leaderboard[0]
    best_config = BoostingConfig(
        library=library,
        feature_names=feature_names,
        max_depth=best["max_depth"],
        learning_rate=best["learning_rate"],
        n_estimators=best["n_estimators"],
        **_FIXED_DEFAULTS,
    )
    return best_config, leaderboard
