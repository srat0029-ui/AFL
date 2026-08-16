"""Research/backtesting API for the goal model - Section 24. Same
research-only architecture as player_models.py's disposal endpoints:
every response is explicitly `is_research_only: true`.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import GoalBacktestSummaryRead, GoalCalibrationReportRead, GoalPlayerHistoryRead, GoalTeamDiagnosticRead
from app.database import get_db
from app.player_modelling.goal_report_query import (
    GoalModelsUnavailableError,
    load_goal_backtest_summary,
    load_goal_calibration_report,
    load_goal_player_predictions,
)
from app.player_modelling.live_report_query import load_upcoming_goal_team_diagnostic

router = APIRouter(prefix="/api/player-models/goals", tags=["goal-models"])


@router.get("/backtest", response_model=GoalBacktestSummaryRead)
def goal_backtest_summary(db: Session = Depends(get_db)) -> GoalBacktestSummaryRead:
    try:
        summary = load_goal_backtest_summary(db)
    except GoalModelsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return GoalBacktestSummaryRead(
        promoted_model=summary.promoted,
        baselines=summary.baselines,
        candidates=summary.candidates,
        season_breakdown=summary.season_breakdown,
        zero_goal=summary.zero_goal,
    )


@router.get("/calibration", response_model=GoalCalibrationReportRead)
def goal_calibration(model_name: str | None = None, db: Session = Depends(get_db)) -> GoalCalibrationReportRead:
    try:
        report = load_goal_calibration_report(db, model_name)
    except GoalModelsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return GoalCalibrationReportRead(**report)


@router.get("/players/{player_id}", response_model=GoalPlayerHistoryRead)
def goal_player_history(player_id: int, model_name: str | None = None, db: Session = Depends(get_db)) -> GoalPlayerHistoryRead:
    try:
        result = load_goal_player_predictions(db, player_id, model_name)
    except GoalModelsUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return GoalPlayerHistoryRead(**result)


@router.get("/upcoming-team-diagnostic", response_model=list[GoalTeamDiagnosticRead])
def goal_upcoming_team_diagnostic(db: Session = Depends(get_db)) -> list[GoalTeamDiagnosticRead]:
    """Internal/Model Research diagnostic (Section 19) — compares the sum
    of individual player-projected goals per team against that team's
    Poisson-model expected goals for the next upcoming round. Exposed only
    here, not on the consumer-facing pages, and never used to reconcile or
    adjust the underlying hurdle-model projections themselves."""
    rows = load_upcoming_goal_team_diagnostic(db)
    return [GoalTeamDiagnosticRead(**r) for r in rows]
