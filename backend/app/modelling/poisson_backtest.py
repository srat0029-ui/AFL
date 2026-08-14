"""Walk-forward Poisson replay: the same no-leakage discipline as
elo_backtest.py, applied to rolling per-team scoring averages instead of an
Elo rating.

Each team's attack/defense strength entering a match is computed from a
rolling window of that team's STRICTLY PRIOR completed matches only, relative
to a *blended* (home+away pooled) league average — a full expanding average,
not windowed, since league scoring trends move slowly and a longer, more
stable baseline is preferable to a noisy few-round window for that purpose;
team *form*, in contrast, is exactly what the shorter rolling window is
meant to capture.

Home-ground advantage is derived from data, not a guessed multiplier: a
separate expanding average of home-only and away-only scoring is tracked,
and the ratio of each to the blended average gives a home_factor/away_factor
that (by construction) average back to the blended baseline — so predicting
two league-average teams reproduces the league-average total exactly,
instead of systematically inflating it. See poisson_model.py's module
docstring for why a naive "multiply the home team's expected score by a
guessed constant" approach was tried first and rejected — a tuning test
caught it double-counting home advantage on top of pooled team ratings.

Small-sample teams (few games in their window, e.g. early in the dataset or
just promoted) have their strength shrunk toward the league average (1.0)
rather than trusted at full strength on n=1 noise — the same treatment is
applied to the home/away split itself early in a walk-forward run, before
enough matches exist to estimate it reliably.
"""

from collections import deque
from dataclasses import dataclass
from datetime import datetime

from app.modelling.poisson_model import PoissonConfig, score_distribution, win_draw_loss
from app.modelling.types import MatchResult

_FALLBACK_LEAGUE_AVG = 15.0  # used only before any match has been observed


@dataclass(frozen=True)
class PoissonPrediction:
    match_id: int
    season_year: int
    scheduled_start: datetime
    home_team_id: int
    away_team_id: int
    home_expected_goals: float
    home_expected_behinds: float
    away_expected_goals: float
    away_expected_behinds: float
    home_win_probability: float
    draw_probability: float
    away_win_probability: float
    expected_total_points: float
    expected_margin: float
    actual_home_outcome: float  # 1.0 win, 0.5 draw, 0.0 loss
    actual_total_points: int
    actual_margin: int


@dataclass(frozen=True)
class _TeamStrength:
    attack_goals: float
    defense_goals: float
    attack_behinds: float
    defense_behinds: float


class _TeamHistory:
    __slots__ = ("goals_for", "goals_against", "behinds_for", "behinds_against")

    def __init__(self, maxlen: int):
        self.goals_for: deque[int] = deque(maxlen=maxlen)
        self.goals_against: deque[int] = deque(maxlen=maxlen)
        self.behinds_for: deque[int] = deque(maxlen=maxlen)
        self.behinds_against: deque[int] = deque(maxlen=maxlen)

    def __len__(self) -> int:
        return len(self.goals_for)

    def record(self, goals_for: int, goals_against: int, behinds_for: int, behinds_against: int) -> None:
        self.goals_for.append(goals_for)
        self.goals_against.append(goals_against)
        self.behinds_for.append(behinds_for)
        self.behinds_against.append(behinds_against)


class _LeagueSplit:
    """Tracks expanding home-only / away-only scoring averages, separately
    from the blended average used for team ratings, so a home/away factor
    can be derived directly from data instead of guessed."""

    def __init__(self) -> None:
        self.home_goals_sum = 0.0
        self.away_goals_sum = 0.0
        self.home_behinds_sum = 0.0
        self.away_behinds_sum = 0.0
        self.matches_count = 0

    def blended_avg_goals(self) -> float:
        if self.matches_count == 0:
            return _FALLBACK_LEAGUE_AVG
        return (self.home_goals_sum + self.away_goals_sum) / (2 * self.matches_count)

    def blended_avg_behinds(self) -> float:
        if self.matches_count == 0:
            return _FALLBACK_LEAGUE_AVG
        return (self.home_behinds_sum + self.away_behinds_sum) / (2 * self.matches_count)

    def factors(self, config: PoissonConfig) -> tuple[float, float, float, float]:
        """Returns (home_factor_goals, away_factor_goals, home_factor_behinds, away_factor_behinds),
        each shrunk toward 1.0 (neutral) until enough matches have been observed."""
        if self.matches_count == 0:
            return 1.0, 1.0, 1.0, 1.0

        blended_goals = self.blended_avg_goals()
        blended_behinds = self.blended_avg_behinds()
        raw_home_goals = (self.home_goals_sum / self.matches_count) / blended_goals
        raw_away_goals = (self.away_goals_sum / self.matches_count) / blended_goals
        raw_home_behinds = (self.home_behinds_sum / self.matches_count) / blended_behinds
        raw_away_behinds = (self.away_behinds_sum / self.matches_count) / blended_behinds

        weight = min(self.matches_count / config.min_league_games_for_home_split, 1.0)
        return (
            1.0 + weight * (raw_home_goals - 1.0),
            1.0 + weight * (raw_away_goals - 1.0),
            1.0 + weight * (raw_home_behinds - 1.0),
            1.0 + weight * (raw_away_behinds - 1.0),
        )

    def record(self, home_goals: int, away_goals: int, home_behinds: int, away_behinds: int) -> None:
        self.home_goals_sum += home_goals
        self.away_goals_sum += away_goals
        self.home_behinds_sum += home_behinds
        self.away_behinds_sum += away_behinds
        self.matches_count += 1


def _avg(values: deque[int]) -> float:
    return sum(values) / len(values)


def _team_strength(hist: _TeamHistory, league_avg_goals: float, league_avg_behinds: float, config: PoissonConfig) -> _TeamStrength:
    n = len(hist)
    if n == 0:
        return _TeamStrength(1.0, 1.0, 1.0, 1.0)

    raw_attack_goals = _avg(hist.goals_for) / league_avg_goals
    raw_defense_goals = _avg(hist.goals_against) / league_avg_goals
    raw_attack_behinds = _avg(hist.behinds_for) / league_avg_behinds
    raw_defense_behinds = _avg(hist.behinds_against) / league_avg_behinds

    # Shrink toward league-average (1.0) when the sample is small.
    weight = min(n / config.min_games_for_reliable_strength, 1.0)
    return _TeamStrength(
        attack_goals=1.0 + weight * (raw_attack_goals - 1.0),
        defense_goals=1.0 + weight * (raw_defense_goals - 1.0),
        attack_behinds=1.0 + weight * (raw_attack_behinds - 1.0),
        defense_behinds=1.0 + weight * (raw_defense_behinds - 1.0),
    )


def run_walk_forward(
    matches: list[MatchResult], config: PoissonConfig, return_state: bool = False
) -> list[PoissonPrediction] | tuple[list[PoissonPrediction], "PoissonModelState"]:
    """Set return_state=True to also get back a PoissonModelState snapshot
    reflecting all of `matches` — used to predict not-yet-played fixtures
    (see PoissonModelState.predict) without re-deriving team form from a
    separate, duplicate pass over history."""
    team_histories: dict[int, _TeamHistory] = {}
    league = _LeagueSplit()
    predictions: list[PoissonPrediction] = []

    for match in sorted(matches, key=lambda m: (m.scheduled_start, m.match_id)):
        if match.home_goals is None or match.away_goals is None:
            continue  # no goals/behinds breakdown available for this match — skip, don't guess

        home_hist = team_histories.setdefault(match.home_team_id, _TeamHistory(config.rolling_window_games))
        away_hist = team_histories.setdefault(match.away_team_id, _TeamHistory(config.rolling_window_games))

        blended_goals = league.blended_avg_goals()
        blended_behinds = league.blended_avg_behinds()
        home_factor_goals, away_factor_goals, home_factor_behinds, away_factor_behinds = league.factors(config)

        home_strength = _team_strength(home_hist, blended_goals, blended_behinds, config)
        away_strength = _team_strength(away_hist, blended_goals, blended_behinds, config)

        lambda_home_goals = blended_goals * home_strength.attack_goals * away_strength.defense_goals * home_factor_goals
        lambda_away_goals = blended_goals * away_strength.attack_goals * home_strength.defense_goals * away_factor_goals
        lambda_home_behinds = (
            blended_behinds * home_strength.attack_behinds * away_strength.defense_behinds * home_factor_behinds
        )
        lambda_away_behinds = (
            blended_behinds * away_strength.attack_behinds * home_strength.defense_behinds * away_factor_behinds
        )

        home_pmf = score_distribution(lambda_home_goals, lambda_home_behinds, config.max_goals, config.max_behinds)
        away_pmf = score_distribution(lambda_away_goals, lambda_away_behinds, config.max_goals, config.max_behinds)
        home_win_p, draw_p, away_win_p = win_draw_loss(home_pmf, away_pmf)

        expected_home_total = 6 * lambda_home_goals + lambda_home_behinds
        expected_away_total = 6 * lambda_away_goals + lambda_away_behinds

        if match.home_score > match.away_score:
            actual_outcome = 1.0
        elif match.home_score < match.away_score:
            actual_outcome = 0.0
        else:
            actual_outcome = 0.5

        predictions.append(
            PoissonPrediction(
                match_id=match.match_id,
                season_year=match.season_year,
                scheduled_start=match.scheduled_start,
                home_team_id=match.home_team_id,
                away_team_id=match.away_team_id,
                home_expected_goals=lambda_home_goals,
                home_expected_behinds=lambda_home_behinds,
                away_expected_goals=lambda_away_goals,
                away_expected_behinds=lambda_away_behinds,
                home_win_probability=home_win_p,
                draw_probability=draw_p,
                away_win_probability=away_win_p,
                expected_total_points=expected_home_total + expected_away_total,
                expected_margin=expected_home_total - expected_away_total,
                actual_home_outcome=actual_outcome,
                actual_total_points=match.home_score + match.away_score,
                actual_margin=match.home_score - match.away_score,
            )
        )

        home_hist.record(
            goals_for=match.home_goals, goals_against=match.away_goals,
            behinds_for=match.home_behinds, behinds_against=match.away_behinds,
        )
        away_hist.record(
            goals_for=match.away_goals, goals_against=match.home_goals,
            behinds_for=match.away_behinds, behinds_against=match.home_behinds,
        )
        league.record(
            home_goals=match.home_goals, away_goals=match.away_goals,
            home_behinds=match.home_behinds, away_behinds=match.away_behinds,
        )

    if return_state:
        return predictions, PoissonModelState(team_histories=team_histories, league=league, config=config)
    return predictions


@dataclass(frozen=True)
class PoissonModelState:
    """A snapshot of rolling team form and league scoring after replaying a
    set of matches — enough to predict a hypothetical *next* match for any
    two teams without mutating anything or re-deriving history from scratch.

    Known simplification: unlike Elo's explicit season-carryover regression,
    there's no special handling here for an off-season gap (list changes via
    trades/draft/retirements). The rolling window naturally discounts old
    form as new games accumulate, but for the first few games of a new
    season it's still weighted by last season's window contents. Acceptable
    for now — Elo already carries the primary match-winner signal across
    that gap; revisit if totals/margin predictions look stale early in a
    season once there's real data to check against.
    """

    team_histories: dict[int, _TeamHistory]
    league: _LeagueSplit
    config: PoissonConfig

    def games_played(self, team_id: int) -> int:
        """How many games are in this team's rolling-form window right now —
        used as a sample-size signal for confidence tiering (app/edges/confidence.py)."""
        hist = self.team_histories.get(team_id)
        return len(hist) if hist is not None else 0

    def predict(self, home_team_id: int, away_team_id: int):
        blended_goals = self.league.blended_avg_goals()
        blended_behinds = self.league.blended_avg_behinds()
        home_factor_goals, away_factor_goals, home_factor_behinds, away_factor_behinds = self.league.factors(self.config)

        home_hist = self.team_histories.get(home_team_id, _TeamHistory(self.config.rolling_window_games))
        away_hist = self.team_histories.get(away_team_id, _TeamHistory(self.config.rolling_window_games))
        home_strength = _team_strength(home_hist, blended_goals, blended_behinds, self.config)
        away_strength = _team_strength(away_hist, blended_goals, blended_behinds, self.config)

        lambda_home_goals = blended_goals * home_strength.attack_goals * away_strength.defense_goals * home_factor_goals
        lambda_away_goals = blended_goals * away_strength.attack_goals * home_strength.defense_goals * away_factor_goals
        lambda_home_behinds = (
            blended_behinds * home_strength.attack_behinds * away_strength.defense_behinds * home_factor_behinds
        )
        lambda_away_behinds = (
            blended_behinds * away_strength.attack_behinds * home_strength.defense_behinds * away_factor_behinds
        )

        home_pmf = score_distribution(lambda_home_goals, lambda_home_behinds, self.config.max_goals, self.config.max_behinds)
        away_pmf = score_distribution(lambda_away_goals, lambda_away_behinds, self.config.max_goals, self.config.max_behinds)
        return home_pmf, away_pmf
