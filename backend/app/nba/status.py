"""What NBA data exists right now — row counts and latest timestamps read
straight from the NBA tables. Read-only; triggers no provider request.

This is the honest answer to "how far along is NBA": until ingestion and a
model are built every count is zero, and the API says so rather than
showing placeholder content.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.prospective import ensure_utc
from app.models.nba import (
    NbaGame,
    NbaPlayer,
    NbaGameLineupObservation,
    NbaGameScheduleObservation,
    NbaPlayerAvailabilityReport,
    NbaPlayerGameLog,
    NbaPropPrediction,
    NbaPropProjection,
    NbaPropQuote,
    NbaTeam,
    NbaTeamObservation,
)
from app.nba import SPORT_CODE
from app.nba.markets import NbaPropMarket


@dataclass(frozen=True)
class DatasetStatus:
    key: str
    label: str
    rows: int
    latest_at: datetime | None  # the most recent record's own timestamp; None when empty or not applicable


@dataclass(frozen=True)
class NbaStatusReport:
    sport: str
    markets: list[str]
    datasets: list[DatasetStatus]
    predictions_frozen: int
    predictions_with_closing_line: int
    predictions_settled: int


# (key, label, model, the column that says how recent the data is)
_DATASETS = [
    ("teams", "Teams", NbaTeam, None),
    ("players", "Players", NbaPlayer, None),
    ("games", "Games", NbaGame, NbaGame.scheduled_start),
    ("player_game_logs", "Player game logs", NbaPlayerGameLog, NbaPlayerGameLog.recorded_at),
    ("availability_reports", "Injury / availability observations", NbaPlayerAvailabilityReport, NbaPlayerAvailabilityReport.observed_at),
    ("team_observations", "Team roster / depth-chart observations", NbaTeamObservation, NbaTeamObservation.observed_at),
    ("game_lineup_observations", "Game lineup observations", NbaGameLineupObservation, NbaGameLineupObservation.observed_at),
    ("game_schedule_observations", "Game schedule observations", NbaGameScheduleObservation, NbaGameScheduleObservation.observed_at),
    ("prop_quotes", "Bookmaker prop quotes", NbaPropQuote, NbaPropQuote.observed_at),
    ("projections", "Model projections", NbaPropProjection, NbaPropProjection.generated_at),
    ("predictions", "Frozen predictions", NbaPropPrediction, NbaPropPrediction.predicted_at),
]


def _count(db: Session, model, *where) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*where)) or 0


def load_nba_status(db: Session) -> NbaStatusReport:
    datasets = []
    for key, label, model, recency_column in _DATASETS:
        latest = db.scalar(select(func.max(recency_column))) if recency_column is not None else None
        datasets.append(DatasetStatus(key=key, label=label, rows=_count(db, model), latest_at=ensure_utc(latest) if latest else None))
    return NbaStatusReport(
        sport=SPORT_CODE,
        markets=[m.value for m in NbaPropMarket],
        datasets=datasets,
        predictions_frozen=_count(db, NbaPropPrediction),
        predictions_with_closing_line=_count(db, NbaPropPrediction, NbaPropPrediction.closing_captured_at.is_not(None)),
        predictions_settled=_count(db, NbaPropPrediction, NbaPropPrediction.settled_at.is_not(None)),
    )
