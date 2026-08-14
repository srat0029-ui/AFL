"""Persists a completed walk-forward run's rating history to the database.

Wholesale recompute-and-replace, not versioned: this table always reflects
the current best Elo configuration. Re-running the modelling CLI with a
different config overwrites it entirely rather than accumulating history
for configs nobody's using anymore.
"""

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.modelling.elo_backtest import EloPrediction
from app.models import EloRating


def persist_elo_ratings(db: Session, predictions: list[EloPrediction]) -> int:
    db.execute(delete(EloRating))

    rows = []
    for sequence, prediction in enumerate(predictions):
        rows.append(
            EloRating(
                match_id=prediction.match_id,
                team_id=prediction.home_team_id,
                sequence=sequence,
                rating_before=prediction.home_rating_before,
                rating_after=prediction.home_rating_after,
            )
        )
        rows.append(
            EloRating(
                match_id=prediction.match_id,
                team_id=prediction.away_team_id,
                sequence=sequence,
                rating_before=prediction.away_rating_before,
                rating_after=prediction.away_rating_after,
            )
        )

    db.add_all(rows)
    db.commit()
    return len(rows)
