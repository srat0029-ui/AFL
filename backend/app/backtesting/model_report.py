"""Full statistical validation report for both models — Brier score, log
loss, accuracy, calibration, broken down by season/team/model-conviction.

No historical market odds are used or needed here: this validates whether
the models' probability estimates matched what actually happened, not
whether they'd have made money against any specific market price (that
needs real odds — see app/backtesting/logged_odds.py, which has almost none
yet). This module reuses the exact same walk-forward replay already proven
leakage-free in Stage 1.2/1.3 — it's purely an aggregation/reporting layer
on top of it, not new prediction logic.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtesting.segments import (
    BacktestSegment,
    build_segments,
    conviction_bucket,
    scoring_metrics,
    team_perspective,
    win_prob_metrics,
    win_prob_metrics_from_pairs,
)
from app.modelling.data_loading import load_completed_matches
from app.modelling.elo import EloConfig
from app.modelling.elo_backtest import run_walk_forward as elo_walk_forward
from app.modelling.metrics import calibration_table
from app.modelling.poisson_backtest import run_walk_forward as poisson_walk_forward
from app.modelling.poisson_model import PoissonConfig
from app.models import ModelRun, Team


class ModelsUnavailableError(Exception):
    """Raised when elo_cli.py / poisson_cli.py haven't been run yet."""


@dataclass(frozen=True)
class WinProbReport:
    model_name: str
    overall: BacktestSegment
    by_season: list[BacktestSegment]
    by_team: list[BacktestSegment]
    by_conviction: list[BacktestSegment]
    calibration: list[dict]


@dataclass(frozen=True)
class ScoringReport:
    overall: BacktestSegment
    by_season: list[BacktestSegment]


def _team_names(db: Session) -> dict[int, str]:
    return {t.id: t.name for t in db.scalars(select(Team)).all()}


def _by_team_win_prob_segments(predictions: list, team_names: dict[int, str]) -> list[BacktestSegment]:
    by_team: dict[int, list[tuple[float, float]]] = {}
    for p in predictions:
        for team_id in (p.home_team_id, p.away_team_id):
            perspective = team_perspective(p, team_id)
            if perspective is not None:
                by_team.setdefault(team_id, []).append(perspective)

    segments = []
    for team_id, pairs in by_team.items():
        probs = [prob for prob, _ in pairs]
        outcomes = [outcome for _, outcome in pairs]
        label = team_names.get(team_id, f"Team {team_id}")
        segments.append(BacktestSegment(label=label, n=len(pairs), metrics=win_prob_metrics_from_pairs(probs, outcomes)))
    return sorted(segments, key=lambda s: s.label)


def build_win_prob_report(model_name: str, predictions: list, team_names: dict[int, str]) -> WinProbReport:
    overall = BacktestSegment(label="Overall", n=len(predictions), metrics=win_prob_metrics(predictions))
    by_season = build_segments(predictions, key_fn=lambda p: str(p.season_year), metrics_fn=win_prob_metrics)
    by_team = _by_team_win_prob_segments(predictions, team_names)
    by_conviction = build_segments(
        predictions, key_fn=lambda p: conviction_bucket(p.home_win_probability), metrics_fn=win_prob_metrics
    )
    calibration = calibration_table(
        [p.home_win_probability for p in predictions], [p.actual_home_outcome for p in predictions]
    )
    return WinProbReport(
        model_name=model_name, overall=overall, by_season=by_season, by_team=by_team,
        by_conviction=by_conviction, calibration=calibration,
    )


def build_scoring_report(predictions: list) -> ScoringReport:
    overall = BacktestSegment(label="Overall", n=len(predictions), metrics=scoring_metrics(predictions))
    by_season = build_segments(predictions, key_fn=lambda p: str(p.season_year), metrics_fn=scoring_metrics)
    return ScoringReport(overall=overall, by_season=by_season)


def load_elo_backtest(db: Session) -> WinProbReport:
    elo_run = db.scalar(select(ModelRun).where(ModelRun.model_name == "elo"))
    if elo_run is None:
        raise ModelsUnavailableError("Run `python -m app.modelling.elo_cli` first.")

    matches = load_completed_matches(db)
    config = EloConfig(**elo_run.config_json)
    predictions = elo_walk_forward(matches, config)
    return build_win_prob_report("elo", predictions, _team_names(db))


def load_poisson_backtest(db: Session) -> tuple[WinProbReport, ScoringReport]:
    poisson_run = db.scalar(select(ModelRun).where(ModelRun.model_name == "poisson"))
    if poisson_run is None:
        raise ModelsUnavailableError("Run `python -m app.modelling.poisson_cli` first.")

    matches = load_completed_matches(db)
    config = PoissonConfig(**poisson_run.config_json)
    predictions = poisson_walk_forward(matches, config)
    win_report = build_win_prob_report("poisson", predictions, _team_names(db))
    scoring_report = build_scoring_report(predictions)
    return win_report, scoring_report
