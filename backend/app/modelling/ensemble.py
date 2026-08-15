"""A deliberately simple, leakage-safe Elo + boosting ensemble: a fixed
weighted average of the two models' probabilities. The weight is selected
only on tune-window data (the same inner-validation discipline used
throughout this stage), never on the final evaluation outcomes — see the
Stage brief's explicit instruction not to build a stacking system yet, just
test whether the cheapest possible combination helps at all before
justifying anything fancier.
"""

from dataclasses import dataclass

from app.modelling.metrics import brier_score

# 0.0 = pure Elo, 1.0 = pure boosting. Coarse on purpose — a weighted
# average of two already-decent models is not sensitive to fine-grained
# weight tuning, and a coarse grid is less prone to overfitting the small
# inner-validation sample.
DEFAULT_WEIGHT_GRID = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


@dataclass(frozen=True)
class EnsembleWeightResult:
    boosting_weight: float
    inner_val_brier: float


def blend(elo_probs: list[float], boosting_probs: list[float], boosting_weight: float) -> list[float]:
    return [boosting_weight * b + (1 - boosting_weight) * e for e, b in zip(elo_probs, boosting_probs)]


def select_ensemble_weight(
    elo_probs: list[float], boosting_probs: list[float], outcomes: list[float], weight_grid: list[float] | None = None
) -> tuple[float, list[EnsembleWeightResult]]:
    """elo_probs/boosting_probs/outcomes must be an inner model's genuinely
    out-of-sample predictions (never the final evaluation set) — the same
    role inner_val plays for regularisation-strength and calibration-method
    selection elsewhere in this stage."""
    weight_grid = weight_grid or DEFAULT_WEIGHT_GRID
    leaderboard = [
        EnsembleWeightResult(boosting_weight=w, inner_val_brier=brier_score(blend(elo_probs, boosting_probs, w), outcomes))
        for w in weight_grid
    ]
    leaderboard.sort(key=lambda r: r.inner_val_brier)
    return leaderboard[0].boosting_weight, leaderboard
