"""Persists a completed Poisson walk-forward run to the database.

Wholesale recompute-and-replace, same as elo_persistence.py — always
reflects the current best config, not a history of past configs.
"""

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.modelling.poisson_backtest import PoissonPrediction
from app.models import PoissonMatchPrediction


def persist_poisson_predictions(db: Session, predictions: list[PoissonPrediction]) -> int:
    db.execute(delete(PoissonMatchPrediction))

    rows = [
        PoissonMatchPrediction(
            match_id=p.match_id,
            sequence=sequence,
            home_expected_goals=p.home_expected_goals,
            home_expected_behinds=p.home_expected_behinds,
            away_expected_goals=p.away_expected_goals,
            away_expected_behinds=p.away_expected_behinds,
            home_win_probability=p.home_win_probability,
            draw_probability=p.draw_probability,
            away_win_probability=p.away_win_probability,
            expected_total_points=p.expected_total_points,
            expected_margin=p.expected_margin,
            actual_total_points=p.actual_total_points,
            actual_margin=p.actual_margin,
            actual_home_outcome=p.actual_home_outcome,
        )
        for sequence, p in enumerate(predictions)
    ]

    db.add_all(rows)
    db.commit()
    return len(rows)
