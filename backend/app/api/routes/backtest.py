from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import BacktestOverview, LoggedOddsReportRead, ScoringReportRead, WinProbReportRead
from app.backtesting.logged_odds import ModelsUnavailableError as LoggedOddsModelsUnavailableError
from app.backtesting.logged_odds import build_logged_odds_report
from app.backtesting.model_report import ModelsUnavailableError as ReportModelsUnavailableError
from app.backtesting.model_report import load_elo_backtest, load_poisson_backtest
from app.database import get_db

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.get("", response_model=BacktestOverview)
def get_backtest_overview(db: Session = Depends(get_db)) -> BacktestOverview:
    try:
        elo_report = load_elo_backtest(db)
        poisson_win_report, poisson_scoring_report = load_poisson_backtest(db)
        logged_odds_report = build_logged_odds_report(db)
    except (ReportModelsUnavailableError, LoggedOddsModelsUnavailableError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return BacktestOverview(
        elo=WinProbReportRead.model_validate(elo_report),
        poisson_win=WinProbReportRead.model_validate(poisson_win_report),
        poisson_scoring=ScoringReportRead.model_validate(poisson_scoring_report),
        logged_odds=LoggedOddsReportRead.model_validate(logged_odds_report),
    )
