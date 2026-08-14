"""Simple baseline models Elo/Poisson must actually beat to be considered
useful — see app/modelling/elo_backtest.py for why Elo alone, evaluated in
isolation, doesn't answer "is this adding real predictive value."

Each baseline returns the same (match_id, home_win_probability,
actual_home_outcome) shape as EloPrediction/PoissonPrediction so they can be
scored with the exact same app/backtesting/segments.py metric functions —
comparing baselines to real models isn't a special case, it's the same
scoring path applied to a dumber probability generator.

All three are walk-forward / leakage-safe: a baseline's prediction for match
N only ever depends on matches strictly before N in the sorted input.
"""

from dataclasses import dataclass
from datetime import datetime

from app.modelling.types import MatchResult

__all__ = [
    "BaselinePrediction",
    "always_home_baseline",
    "historical_home_win_rate_baseline",
    "simple_form_baseline",
]


@dataclass(frozen=True)
class BaselinePrediction:
    match_id: int
    season_year: int
    scheduled_start: datetime
    home_team_id: int
    away_team_id: int
    home_win_probability: float
    actual_home_outcome: float  # 1.0 win, 0.5 draw, 0.0 loss


def _actual_outcome(home_score: int, away_score: int) -> float:
    if home_score > away_score:
        return 1.0
    if home_score < away_score:
        return 0.0
    return 0.5


def _sorted(matches: list[MatchResult]) -> list[MatchResult]:
    return sorted(matches, key=lambda m: (m.scheduled_start, m.match_id))


def _predictions(matches: list[MatchResult], prob_fn) -> list[BaselinePrediction]:
    out = []
    for match in _sorted(matches):
        out.append(
            BaselinePrediction(
                match_id=match.match_id,
                season_year=match.season_year,
                scheduled_start=match.scheduled_start,
                home_team_id=match.home_team_id,
                away_team_id=match.away_team_id,
                home_win_probability=prob_fn(match),
                actual_home_outcome=_actual_outcome(match.home_score, match.away_score),
            )
        )
    return out


def always_home_baseline(matches: list[MatchResult]) -> list[BaselinePrediction]:
    """Baseline A: always predict the home team, at full confidence (p=1.0).
    Deliberately unhedged — this is meant to be easy to beat, and log loss
    punishes every away win it gets wrong accordingly (log_loss() clips
    probabilities away from the exact 0/1 boundary, so the score stays
    finite rather than exploding to infinity)."""
    return _predictions(matches, lambda _match: 1.0)


def historical_home_win_rate_baseline(matches: list[MatchResult]) -> list[BaselinePrediction]:
    """Baseline B: the league-wide home-win rate observed so far, updated
    after each match — an expanding average, not a single number computed
    from the whole dataset (that would leak future results into early
    predictions). Starts at a Beta(1,1) prior (1 win / 2 games, i.e. 50%)
    rather than an undefined 0/0 for the very first match."""
    wins = 1.0
    games = 2.0
    out = []
    for match in _sorted(matches):
        prob = wins / games
        outcome = _actual_outcome(match.home_score, match.away_score)
        out.append(
            BaselinePrediction(
                match_id=match.match_id,
                season_year=match.season_year,
                scheduled_start=match.scheduled_start,
                home_team_id=match.home_team_id,
                away_team_id=match.away_team_id,
                home_win_probability=prob,
                actual_home_outcome=outcome,
            )
        )
        wins += outcome  # a draw (0.5) contributes half a "win", consistent with actual_home_outcome's convention
        games += 1.0
    return out


_FORM_WINDOW_GAMES = 10
_FORM_SENSITIVITY = 0.5  # how strongly a form-rate gap moves probability away from 50%
_FORM_PROB_CLAMP = 0.1  # keep predictions away from the 0%/100% edges


def simple_form_baseline(matches: list[MatchResult]) -> list[BaselinePrediction]:
    """Baseline C: each team's win rate over its last `_FORM_WINDOW_GAMES`
    completed matches (across season boundaries — deliberately simple, no
    ladder/finals-position logic), mapped linearly to a probability around
    50% by the gap between the two teams' recent form. A team with no prior
    games defaults to 50% form (neutral), same as the league-wide average.
    """
    from collections import deque

    recent: dict[int, deque[float]] = {}

    def form(team_id: int) -> float:
        history = recent.get(team_id)
        if not history:
            return 0.5
        return sum(history) / len(history)

    out = []
    for match in _sorted(matches):
        home_form = form(match.home_team_id)
        away_form = form(match.away_team_id)
        raw_prob = 0.5 + _FORM_SENSITIVITY * (home_form - away_form)
        prob = min(max(raw_prob, _FORM_PROB_CLAMP), 1 - _FORM_PROB_CLAMP)
        outcome = _actual_outcome(match.home_score, match.away_score)

        out.append(
            BaselinePrediction(
                match_id=match.match_id,
                season_year=match.season_year,
                scheduled_start=match.scheduled_start,
                home_team_id=match.home_team_id,
                away_team_id=match.away_team_id,
                home_win_probability=prob,
                actual_home_outcome=outcome,
            )
        )

        recent.setdefault(match.home_team_id, deque(maxlen=_FORM_WINDOW_GAMES)).append(outcome)
        recent.setdefault(match.away_team_id, deque(maxlen=_FORM_WINDOW_GAMES)).append(1.0 - outcome)
    return out
