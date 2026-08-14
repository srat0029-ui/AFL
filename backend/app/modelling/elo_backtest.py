"""Walk-forward Elo replay: the no-leakage guarantee this whole model rests on.

Matches are processed in strict chronological order. For each one, the
prediction is generated from ratings as they stood *before* that match —
derived only from strictly earlier matches — and only afterwards are those
ratings updated with the actual result. A prediction for match N can never
be influenced by match N or any later match. This is what makes the
resulting Brier score / log loss / calibration numbers meaningful rather
than retrospective self-flattery.
"""

from dataclasses import dataclass
from datetime import datetime

from app.modelling.elo import EloConfig, EloEngine
from app.modelling.types import MatchResult

__all__ = ["MatchResult", "EloPrediction", "run_walk_forward", "current_ratings", "rating_for_upcoming_match"]


@dataclass(frozen=True)
class EloPrediction:
    match_id: int
    season_year: int
    scheduled_start: datetime
    home_team_id: int
    away_team_id: int
    home_rating_before: float
    away_rating_before: float
    home_rating_after: float
    away_rating_after: float
    home_win_probability: float
    actual_home_outcome: float  # 1.0 win, 0.5 draw, 0.0 loss


def run_walk_forward(matches: list[MatchResult], config: EloConfig) -> list[EloPrediction]:
    """Replays `matches` in chronological order, returning one EloPrediction
    per match, also in chronological order.
    """
    engine = EloEngine(config)
    ratings: dict[int, float] = {}
    last_season_seen: dict[int, int] = {}
    predictions: list[EloPrediction] = []

    for match in sorted(matches, key=lambda m: (m.scheduled_start, m.match_id)):
        home_rating = _rating_entering_match(ratings, last_season_seen, engine, match.home_team_id, match.season_year)
        away_rating = _rating_entering_match(ratings, last_season_seen, engine, match.away_team_id, match.season_year)

        home_win_prob = engine.expected_home_win_prob(home_rating, away_rating)
        new_home, new_away = engine.update(home_rating, away_rating, match.home_score, match.away_score)

        if match.home_score > match.away_score:
            actual = 1.0
        elif match.home_score < match.away_score:
            actual = 0.0
        else:
            actual = 0.5

        predictions.append(
            EloPrediction(
                match_id=match.match_id,
                season_year=match.season_year,
                scheduled_start=match.scheduled_start,
                home_team_id=match.home_team_id,
                away_team_id=match.away_team_id,
                home_rating_before=home_rating,
                away_rating_before=away_rating,
                home_rating_after=new_home,
                away_rating_after=new_away,
                home_win_probability=home_win_prob,
                actual_home_outcome=actual,
            )
        )

        ratings[match.home_team_id] = new_home
        ratings[match.away_team_id] = new_away

    return predictions


def _rating_entering_match(
    ratings: dict[int, float],
    last_season_seen: dict[int, int],
    engine: EloEngine,
    team_id: int,
    season_year: int,
) -> float:
    if team_id not in ratings:
        ratings[team_id] = engine.config.initial_rating
        last_season_seen[team_id] = season_year
        return ratings[team_id]

    if last_season_seen[team_id] != season_year:
        ratings[team_id] = engine.regress_to_mean(ratings[team_id])
        last_season_seen[team_id] = season_year

    return ratings[team_id]


def current_ratings(predictions: list[EloPrediction]) -> dict[int, tuple[float, int]]:
    """Returns {team_id: (latest_rating_after, season_year_of_that_rating)},
    derived from a completed walk-forward run — used to seed predictions for
    not-yet-played fixtures (see app/modelling/cli.py).
    """
    latest: dict[int, tuple[float, int]] = {}
    for prediction in predictions:
        latest[prediction.home_team_id] = (prediction.home_rating_after, prediction.season_year)
        latest[prediction.away_team_id] = (prediction.away_rating_after, prediction.season_year)
    return latest


def rating_for_upcoming_match(
    ratings: dict[int, tuple[float, int]], engine: EloEngine, team_id: int, season_year: int
) -> float:
    """A team's rating to use when predicting a not-yet-played match in
    `season_year`, applying season-carryover regression if its most recent
    known rating is from an earlier season. Shared by elo_cli.py's preview
    and the live edge calculator (app/edges/calculator.py) so both use
    exactly the same rule.
    """
    rating, last_season = ratings.get(team_id, (engine.config.initial_rating, season_year))
    if last_season != season_year:
        rating = engine.regress_to_mean(rating)
    return rating
