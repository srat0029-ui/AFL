"""Rigorous model-evaluation API (Stage 1B) — distinct from the existing
GET /api/backtest (singular), which stays exactly as it was for the
existing Backtesting page's by-team/by-conviction detail. These endpoints
serve the new evaluation-period/baseline-comparison/calibration/disagreement
views described in app/backtesting/evaluation.py and
app/backtesting/model_comparison.py.

`id` in the path is the model name ("elo" | "poisson") — stable and
human-readable, not an opaque incrementing integer, since there's exactly
one current run per model (see app/models/model_run.py's uniqueness
constraint) and callers naturally think in terms of "the elo backtest," not
a numeric id they'd have to look up first.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import (
    BacktestDetailRead,
    BacktestSummaryRead,
    BoostingComparisonOverviewRead,
    CalibrationReportRead,
    LogisticComparisonOverviewRead,
    ModelComparisonRead,
    PoissonRevisionComparisonRead,
    ScoringEvaluationRead,
    SeasonBreakdownRead,
    WinProbEvaluationRead,
)
from app.backtesting.boosting_report import ModelsUnavailableError as BoostingModelsUnavailableError
from app.backtesting.boosting_report import build_boosting_comparison
from app.backtesting.evaluation import EVALUATION_START_YEAR
from app.backtesting.evaluation import ModelsUnavailableError as EvaluationModelsUnavailableError
from app.backtesting.evaluation import load_elo_evaluation, load_poisson_evaluation
from app.backtesting.logistic_report import ModelsUnavailableError as LogisticModelsUnavailableError
from app.backtesting.logistic_report import build_logistic_comparison
from app.backtesting.model_comparison import ModelsUnavailableError as ComparisonModelsUnavailableError
from app.backtesting.model_comparison import load_model_comparison
from app.backtesting.poisson_revision_report import build_poisson_revision_comparison
from app.database import get_db
from app.models import ModelRun
from sqlalchemy import select

router = APIRouter(prefix="/api/backtests", tags=["backtests"])

# Process-level cache for the three comparison endpoints that recompute a
# full walk-forward backtest (poisson-revision: a hyperparameter grid
# search plus two complete 2016-2025 replays) or a comparably expensive
# model comparison live on every request — observed to take 3-33s each,
# which dominated the Backtesting page's load time since the frontend
# fires all backtest endpoints in parallel on mount. These results only
# change when the underlying training data or a promoted model config
# changes (i.e. after a CLI retrain), so caching for the life of the
# server process is safe; restart the server (or, later, add an explicit
# invalidation hook if a retrain-while-running workflow is ever needed) to
# pick up a change.
_logistic_comparison_cache: LogisticComparisonOverviewRead | None = None
_boosting_comparison_cache: BoostingComparisonOverviewRead | None = None
_poisson_revision_cache: PoissonRevisionComparisonRead | None = None

_UNAVAILABLE = (
    EvaluationModelsUnavailableError,
    ComparisonModelsUnavailableError,
    LogisticModelsUnavailableError,
    BoostingModelsUnavailableError,
)


def _load(db: Session, model_id: str):
    """Returns (ModelRun, win_prob_report, scoring_report | None) for a
    given model id, or raises HTTPException(404) for an unknown id."""
    if model_id == "elo":
        run, win_prob = load_elo_evaluation(db)
        return run, win_prob, None
    if model_id == "poisson":
        run, win_prob, scoring = load_poisson_evaluation(db)
        return run, win_prob, scoring
    raise HTTPException(status_code=404, detail=f"Unknown backtest id {model_id!r} — use 'elo' or 'poisson'.")


def _headline_metrics(win_prob, scoring) -> dict[str, float]:
    headline = dict(win_prob.evaluation_metrics)
    if scoring is not None:
        headline.update(
            {
                "total_points_mae": scoring.evaluation_metrics.get("total_points_mae", float("nan")),
                "margin_mae": scoring.evaluation_metrics.get("margin_mae", float("nan")),
            }
        )
    return headline


@router.get("", response_model=list[BacktestSummaryRead])
def list_backtests(db: Session = Depends(get_db)) -> list[BacktestSummaryRead]:
    summaries = []
    for model_id in ("elo", "poisson"):
        try:
            run, win_prob, scoring = _load(db, model_id)
        except _UNAVAILABLE:
            continue
        summaries.append(
            BacktestSummaryRead(
                id=model_id,
                model_name=run.model_name,
                run_at=run.run_at,
                tune_end_year=run.tune_end_year,
                evaluation_start_year=EVALUATION_START_YEAR,
                n_evaluation=win_prob.period.n_evaluation,
                headline_metrics=_headline_metrics(win_prob, scoring),
            )
        )
    return summaries


@router.get("/comparison", response_model=ModelComparisonRead)
def get_comparison(db: Session = Depends(get_db)) -> ModelComparisonRead:
    try:
        report = load_model_comparison(db)
    except _UNAVAILABLE as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ModelComparisonRead.model_validate(report)


@router.get("/logistic-comparison", response_model=LogisticComparisonOverviewRead)
def get_logistic_comparison(db: Session = Depends(get_db)) -> LogisticComparisonOverviewRead:
    global _logistic_comparison_cache
    if _logistic_comparison_cache is not None:
        return _logistic_comparison_cache

    stats_only_run = db.scalar(select(ModelRun).where(ModelRun.model_name == "logistic_stats_only"))
    stats_plus_elo_run = db.scalar(select(ModelRun).where(ModelRun.model_name == "logistic_stats_plus_elo"))
    if stats_only_run is None or stats_plus_elo_run is None:
        raise HTTPException(status_code=503, detail="Run `python -m app.modelling.logistic_cli` first.")

    try:
        overview = build_logistic_comparison(
            db, C_stats_only=stats_only_run.config_json["C"], C_stats_plus_elo=stats_plus_elo_run.config_json["C"]
        )
    except _UNAVAILABLE as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    _logistic_comparison_cache = LogisticComparisonOverviewRead.model_validate(overview)
    return _logistic_comparison_cache


@router.get("/boosting-comparison", response_model=BoostingComparisonOverviewRead)
def get_boosting_comparison(db: Session = Depends(get_db)) -> BoostingComparisonOverviewRead:
    global _boosting_comparison_cache
    if _boosting_comparison_cache is not None:
        return _boosting_comparison_cache

    try:
        overview = build_boosting_comparison(db)
    except _UNAVAILABLE as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    _boosting_comparison_cache = BoostingComparisonOverviewRead.model_validate(overview)
    return _boosting_comparison_cache


@router.get("/poisson-revision", response_model=PoissonRevisionComparisonRead)
def get_poisson_revision(db: Session = Depends(get_db)) -> PoissonRevisionComparisonRead:
    global _poisson_revision_cache
    if _poisson_revision_cache is not None:
        return _poisson_revision_cache

    try:
        comparison = build_poisson_revision_comparison(db)
    except _UNAVAILABLE as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    _poisson_revision_cache = PoissonRevisionComparisonRead.model_validate(comparison)
    return _poisson_revision_cache


@router.get("/{model_id}", response_model=BacktestDetailRead)
def get_backtest_detail(model_id: str, db: Session = Depends(get_db)) -> BacktestDetailRead:
    try:
        run, win_prob, scoring = _load(db, model_id)
    except _UNAVAILABLE as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return BacktestDetailRead(
        id=model_id,
        model_name=run.model_name,
        config=run.config_json,
        tune_end_year=run.tune_end_year,
        run_at=run.run_at,
        win_prob=WinProbEvaluationRead.model_validate(win_prob),
        scoring=ScoringEvaluationRead.model_validate(scoring) if scoring is not None else None,
    )


@router.get("/{model_id}/calibration", response_model=CalibrationReportRead)
def get_backtest_calibration(model_id: str, db: Session = Depends(get_db)) -> CalibrationReportRead:
    try:
        _run, win_prob, _scoring = _load(db, model_id)
    except _UNAVAILABLE as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return CalibrationReportRead(model_name=win_prob.model_name, buckets=win_prob.calibration, ece=win_prob.calibration_ece)


@router.get("/{model_id}/seasons", response_model=SeasonBreakdownRead)
def get_backtest_seasons(model_id: str, db: Session = Depends(get_db)) -> SeasonBreakdownRead:
    try:
        _run, win_prob, scoring = _load(db, model_id)
    except _UNAVAILABLE as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return SeasonBreakdownRead(
        model_name=win_prob.model_name,
        win_prob_by_season=win_prob.by_season,
        scoring_by_season=scoring.by_season if scoring is not None else None,
    )
