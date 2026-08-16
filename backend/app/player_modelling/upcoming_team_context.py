"""Team-context features (Elo win probability, Poisson expected scores/
margin) for UPCOMING matches — the live counterpart to
disposal_team_context.build_team_context, which only ever covers completed
matches. Reuses app/edges/calculator.py's live-computed Elo/Poisson model
state directly (the same code path the match-detail page's "model
probabilities" panel and the edges/backtest CLIs already use), rather than
re-deriving team strength — one leakage-safe implementation, not two.
"""

from sqlalchemy.orm import Session

from app.edges.calculator import ModelsUnavailableError, build_model_context, compute_match_predictions
from app.models import Match
from app.player_modelling.upcoming_features import UpcomingMatchTeams

__all__ = ["ModelsUnavailableError", "build_upcoming_team_context"]


def build_upcoming_team_context(db: Session, upcoming_matches: list[UpcomingMatchTeams]) -> dict[int, dict[int, dict]]:
    """Returns {match_id: {team_id: {"elo_win_prob", "expected_score",
    "opponent_expected_score", "expected_margin"}}}, same shape as
    disposal_team_context.build_team_context, for the given upcoming
    matches only. Raises ModelsUnavailableError if elo_cli/poisson_cli
    haven't been run yet (same contract as the edges calculator)."""
    if not upcoming_matches:
        return {}

    context = build_model_context(db)
    result: dict[int, dict[int, dict]] = {}
    for m in upcoming_matches:
        match_obj = db.get(Match, m.match_id)
        predictions = compute_match_predictions(match_obj, context)
        result[m.match_id] = {
            m.home_team_id: {
                "elo_win_prob": predictions.elo_home_win_probability,
                "expected_score": predictions.poisson_home_expected_score,
                "opponent_expected_score": predictions.poisson_away_expected_score,
                "expected_margin": predictions.poisson_expected_margin,
            },
            m.away_team_id: {
                "elo_win_prob": 1.0 - predictions.elo_home_win_probability,
                "expected_score": predictions.poisson_away_expected_score,
                "opponent_expected_score": predictions.poisson_home_expected_score,
                "expected_margin": -predictions.poisson_expected_margin,
            },
        }
    return result
