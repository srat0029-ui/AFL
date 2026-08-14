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
    CalibrationReportRead,
    ModelComparisonRead,
    ScoringEvaluationRead,
    SeasonBreakdownRead,
    WinProbEvaluationRead,
)
from app.backtesting.evaluation import EVALUATION_START_YEAR
from app.backtesting.evaluation import ModelsUnavailableError as EvaluationModelsUnavailableError
from app.backtesting.evaluation import load_elo_evaluation, load_poisson_evaluation
from app.backtesting.model_comparison import ModelsUnavailableError as ComparisonModelsUnavailableError
from app.backtesting.model_comparison import load_model_comparison
from app.database import get_db

router = APIRouter(prefix="/api/backtests", tags=["backtests"])

_UNAVAILABLE = (EvaluationModelsUnavailableError, ComparisonModelsUnavailableError)


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
