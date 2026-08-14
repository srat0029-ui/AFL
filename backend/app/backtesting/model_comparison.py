"""Elo vs. Poisson: how often do the two models actually disagree, and who's
right more often when they do? Distinct from app/backtesting/evaluation.py
(one model vs. baselines) and app/backtesting/model_report.py (one model's
own quality) — this is specifically about the *relationship* between the
two models, which matters for a future ensemble/confidence decision but
isn't answered by either model's solo report.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtesting.segments import win_prob_metrics_from_pairs
from app.modelling.data_loading import load_completed_matches
from app.modelling.elo import EloConfig
from app.modelling.elo_backtest import run_walk_forward as elo_walk_forward
from app.modelling.metrics import mae
from app.modelling.poisson_backtest import run_walk_forward as poisson_walk_forward
from app.modelling.poisson_model import PoissonConfig
from app.models import ModelRun


class ModelsUnavailableError(Exception):
    """Raised when elo_cli.py / poisson_cli.py haven't been run yet."""

_DISAGREEMENT_BINS = [
    (0.0, 0.05, "agree within 5pp"),
    (0.05, 0.10, "disagree 5-10pp"),
    (0.10, 0.20, "disagree 10-20pp"),
    (0.20, 1.01, "disagree 20pp+"),
]


@dataclass(frozen=True)
class DisagreementBucket:
    label: str
    n: int
    elo_metrics: dict[str, float]
    poisson_metrics: dict[str, float]
    actual_home_win_rate: float | None


@dataclass(frozen=True)
class ModelComparisonReport:
    n_matches: int
    overall_elo_metrics: dict[str, float]
    overall_poisson_metrics: dict[str, float]
    mean_absolute_disagreement: float
    disagreement_buckets: list[DisagreementBucket]
    season_stability: list["SeasonStabilityRow"]


@dataclass(frozen=True)
class SeasonStabilityRow:
    season_year: str
    n_games: int
    elo_accuracy: float
    elo_brier: float
    elo_log_loss: float
    poisson_total_mae: float
    poisson_margin_mae: float
    home_win_rate: float


def build_model_comparison(elo_predictions: list, poisson_predictions: list) -> ModelComparisonReport:
    """Both prediction lists must come from a walk-forward run over the same
    match set — matched here by match_id, so a match present in only one
    (shouldn't happen in practice, since both models replay the same
    completed-matches query) is silently excluded rather than crashing."""
    poisson_by_match = {p.match_id: p for p in poisson_predictions}
    paired = [(e, poisson_by_match[e.match_id]) for e in elo_predictions if e.match_id in poisson_by_match]

    if not paired:
        return ModelComparisonReport(
            n_matches=0, overall_elo_metrics={}, overall_poisson_metrics={},
            mean_absolute_disagreement=float("nan"), disagreement_buckets=[], season_stability=[],
        )

    disagreements = [abs(e.home_win_probability - p.home_win_probability) for e, p in paired]
    mean_disagreement = sum(disagreements) / len(disagreements)

    buckets = []
    for lo, hi, label in _DISAGREEMENT_BINS:
        group = [(e, p) for (e, p), d in zip(paired, disagreements) if lo <= d < hi]
        if not group:
            buckets.append(DisagreementBucket(label=label, n=0, elo_metrics={}, poisson_metrics={}, actual_home_win_rate=None))
            continue
        elo_group = [e for e, _ in group]
        poisson_group = [p for _, p in group]
        buckets.append(
            DisagreementBucket(
                label=label,
                n=len(group),
                elo_metrics=win_prob_metrics_from_pairs(
                    [e.home_win_probability for e in elo_group], [e.actual_home_outcome for e in elo_group]
                ),
                poisson_metrics=win_prob_metrics_from_pairs(
                    [p.home_win_probability for p in poisson_group], [p.actual_home_outcome for p in poisson_group]
                ),
                actual_home_win_rate=sum(e.actual_home_outcome for e in elo_group) / len(elo_group),
            )
        )

    by_season: dict[str, list[tuple]] = {}
    for e, p in paired:
        by_season.setdefault(str(e.season_year), []).append((e, p))

    season_stability = []
    for season, rows in sorted(by_season.items()):
        elo_rows = [e for e, _ in rows]
        poisson_rows = [p for _, p in rows]
        elo_metrics = win_prob_metrics_from_pairs(
            [e.home_win_probability for e in elo_rows], [e.actual_home_outcome for e in elo_rows]
        )
        season_stability.append(
            SeasonStabilityRow(
                season_year=season,
                n_games=len(rows),
                elo_accuracy=elo_metrics["accuracy"],
                elo_brier=elo_metrics["brier_score"],
                elo_log_loss=elo_metrics["log_loss"],
                poisson_total_mae=mae(
                    [p.expected_total_points for p in poisson_rows], [p.actual_total_points for p in poisson_rows]
                ),
                poisson_margin_mae=mae(
                    [p.expected_margin for p in poisson_rows], [p.actual_margin for p in poisson_rows]
                ),
                home_win_rate=sum(e.actual_home_outcome for e in elo_rows) / len(elo_rows),
            )
        )

    all_elo = [e for e, _ in paired]
    all_poisson = [p for _, p in paired]
    return ModelComparisonReport(
        n_matches=len(paired),
        overall_elo_metrics=win_prob_metrics_from_pairs(
            [e.home_win_probability for e in all_elo], [e.actual_home_outcome for e in all_elo]
        ),
        overall_poisson_metrics=win_prob_metrics_from_pairs(
            [p.home_win_probability for p in all_poisson], [p.actual_home_outcome for p in all_poisson]
        ),
        mean_absolute_disagreement=mean_disagreement,
        disagreement_buckets=buckets,
        season_stability=season_stability,
    )


def load_model_comparison(db: Session) -> ModelComparisonReport:
    elo_run = db.scalar(select(ModelRun).where(ModelRun.model_name == "elo"))
    poisson_run = db.scalar(select(ModelRun).where(ModelRun.model_name == "poisson"))
    if elo_run is None or poisson_run is None:
        raise ModelsUnavailableError(
            "Run `python -m app.modelling.elo_cli` and `python -m app.modelling.poisson_cli` first."
        )

    matches = load_completed_matches(db)
    elo_predictions = elo_walk_forward(matches, EloConfig(**elo_run.config_json))
    poisson_predictions = poisson_walk_forward(matches, PoissonConfig(**poisson_run.config_json))
    return build_model_comparison(elo_predictions, poisson_predictions)
