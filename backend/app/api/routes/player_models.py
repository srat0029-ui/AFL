"""Research/backtesting API for player-level prop models - Section 23 of
the disposal-prediction stage brief. Every response is explicitly marked
`is_research_only: true` - this is historical model research, not live
betting advice (the brief is explicit that this stage must not be exposed
as such). Reads persisted PlayerModelRun/PlayerDisposalPrediction rows
(see app/player_modelling/disposal_persistence.py) rather than re-running
the backtest per request.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import (
    DisposalBacktestSummaryRead,
    DisposalCalibrationReportRead,
    DisposalPlayerHistoryRead,
    PlayerModelRunListRead,
)
from app.database import get_db
from app.player_modelling.disposal_report_query import (
    ModelsUnavailableError,
    load_all_runs,
    load_backtest_summary,
    load_calibration_report,
    load_player_predictions,
    run_summary,
)

router = APIRouter(prefix="/api/player-models", tags=["player-models"])


@router.get("", response_model=PlayerModelRunListRead)
def list_player_models(db: Session = Depends(get_db)) -> PlayerModelRunListRead:
    runs = load_all_runs(db)
    return PlayerModelRunListRead(runs=[run_summary(db, r) for r in runs])


@router.get("/disposals/backtest", response_model=DisposalBacktestSummaryRead)
def disposal_backtest_summary(db: Session = Depends(get_db)) -> DisposalBacktestSummaryRead:
    try:
        summary = load_backtest_summary(db)
    except ModelsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return DisposalBacktestSummaryRead(
        promoted_model=summary.promoted,
        baselines=summary.baselines,
        candidates=summary.candidates,
        season_breakdown=summary.season_breakdown,
        within_2=summary.within_2,
        within_5=summary.within_5,
        within_10=summary.within_10,
        median_ae=summary.median_ae,
    )


@router.get("/disposals/calibration", response_model=DisposalCalibrationReportRead)
def disposal_calibration(model_name: str | None = None, db: Session = Depends(get_db)) -> DisposalCalibrationReportRead:
    try:
        report = load_calibration_report(db, model_name)
    except ModelsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return DisposalCalibrationReportRead(**report)


@router.get("/disposals/players/{player_id}", response_model=DisposalPlayerHistoryRead)
def disposal_player_history(player_id: int, model_name: str | None = None, db: Session = Depends(get_db)) -> DisposalPlayerHistoryRead:
    try:
        result = load_player_predictions(db, player_id, model_name)
    except ModelsUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DisposalPlayerHistoryRead(**result)
