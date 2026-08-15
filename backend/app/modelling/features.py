"""Point-in-time feature engineering for the logistic-regression match-winner
model.

Absolute leakage discipline, identical in spirit to elo_backtest.py /
poisson_backtest.py: for match N, every feature is a snapshot of team state
built from strictly earlier matches, taken *before* that match's own result
is folded into the rolling history. A prediction for match N can never see
match N or any later match.

Compact by design (13 match-level features, one difference per football
concept) rather than "everything obtainable" — see the module docstring in
app/modelling/ablation.py for which of these actually earned their place via
held-out evaluation rather than just sounding useful. Two rolling windows
for recent win/margin form (5 and 10 games, as specified), one window for
the advanced-stat differentials (6 games) — using two windows everywhere
would double the feature count without a clear football reason to.

Known, deliberate omissions (see the Stage 1B/1C audit): true disposal
efficiency (% of disposals to a teammate) and turnover/intercept
possessions aren't published by the AFL Tables source this project uses, so
they're not faked here. "Scoring efficiency" is included as a genuinely
computable proxy — goal conversion rate (goals / (goals + behinds)), from
Squiggle's official score breakdown, not a fabricated stand-in.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

FORM_WINDOW_SHORT = 5
FORM_WINDOW_LONG = 10
STATS_WINDOW = 6

MIN_GAMES_FORM_SHORT = 3
MIN_GAMES_FORM_LONG = 5
MIN_GAMES_STATS = 3

# The 13 match-level (home - away, or league-wide) features stats-only
# models are trained on. Order matters: it's the column order fed to
# scikit-learn, and the order standardized coefficients/permutation
# importance results are reported in.
STATS_FEATURE_NAMES = [
    "form_diff_5",
    "form_diff_10",
    "margin_diff_5",
    "margin_diff_10",
    "points_for_diff_5",
    "points_against_diff_5",
    "clearance_differential_diff",
    "inside50_differential_diff",
    "contested_possession_differential_diff",
    "tackle_differential_diff",
    "conversion_rate_diff",
    "marks_inside_50_diff",
    "league_home_win_rate",
]
ELO_FEATURE_NAME = "elo_home_win_probability"
STATS_PLUS_ELO_FEATURE_NAMES = STATS_FEATURE_NAMES + [ELO_FEATURE_NAME]

# Named feature groups for the ablation experiments (Stage brief section 16)
# — each is a subset of STATS_FEATURE_NAMES with a shared football meaning.
FEATURE_GROUPS: dict[str, list[str]] = {
    "recent_form": ["form_diff_5", "form_diff_10", "margin_diff_5", "margin_diff_10"],
    "recent_scoring": ["points_for_diff_5", "points_against_diff_5", "conversion_rate_diff"],
    "clearances": ["clearance_differential_diff"],
    "inside_50s": ["inside50_differential_diff", "marks_inside_50_diff"],
    "contested_possession": ["contested_possession_differential_diff"],
    "pressure": ["tackle_differential_diff"],
    "home_ground": ["league_home_win_rate"],
}


@dataclass(frozen=True)
class MatchFeatureInput:
    """One completed match: score info (always present) plus advanced-stat
    info (None where TeamMatchStat coverage is missing for that match —
    real backfill coverage is 100%, but the model must not assume that)."""

    match_id: int
    season_year: int
    scheduled_start: datetime
    home_team_id: int
    away_team_id: int
    home_score: int
    away_score: int
    home_goals: int | None = None
    home_behinds: int | None = None
    away_goals: int | None = None
    away_behinds: int | None = None
    home_clearances: int | None = None
    away_clearances: int | None = None
    home_inside_50s: int | None = None
    away_inside_50s: int | None = None
    home_contested_possessions: int | None = None
    away_contested_possessions: int | None = None
    home_tackles: int | None = None
    away_tackles: int | None = None
    home_marks_inside_50: int | None = None
    away_marks_inside_50: int | None = None


@dataclass(frozen=True)
class MatchFeatureRow:
    match_id: int
    season_year: int
    scheduled_start: datetime
    home_team_id: int
    away_team_id: int
    actual_home_outcome: float  # 1.0 win, 0.5 draw, 0.0 loss
    features: dict[str, float | None]
    has_full_history: bool  # False if any feature above was None (insufficient rolling history)


@dataclass(frozen=True)
class _TeamSnapshot:
    games_played_form: int
    games_played_stats: int
    wins_last_5: float | None
    wins_last_10: float | None
    avg_margin_last_5: float | None
    avg_margin_last_10: float | None
    points_for_last_5: float | None
    points_against_last_5: float | None
    clearance_differential: float | None
    inside_50_differential: float | None
    contested_possession_differential: float | None
    tackle_differential: float | None
    conversion_rate: float | None
    marks_inside_50: float | None


class _TeamRollingHistory:
    __slots__ = (
        "results", "margins", "points_for", "points_against",
        "clearances_for", "clearances_against", "inside50_for", "inside50_against",
        "contested_for", "contested_against", "tackles_for", "tackles_against",
        "conversion", "marks_i50_for",
    )

    def __init__(self) -> None:
        self.results: deque[float] = deque(maxlen=FORM_WINDOW_LONG)
        self.margins: deque[int] = deque(maxlen=FORM_WINDOW_LONG)
        self.points_for: deque[int] = deque(maxlen=FORM_WINDOW_SHORT)
        self.points_against: deque[int] = deque(maxlen=FORM_WINDOW_SHORT)
        self.clearances_for: deque[int] = deque(maxlen=STATS_WINDOW)
        self.clearances_against: deque[int] = deque(maxlen=STATS_WINDOW)
        self.inside50_for: deque[int] = deque(maxlen=STATS_WINDOW)
        self.inside50_against: deque[int] = deque(maxlen=STATS_WINDOW)
        self.contested_for: deque[int] = deque(maxlen=STATS_WINDOW)
        self.contested_against: deque[int] = deque(maxlen=STATS_WINDOW)
        self.tackles_for: deque[int] = deque(maxlen=STATS_WINDOW)
        self.tackles_against: deque[int] = deque(maxlen=STATS_WINDOW)
        self.conversion: deque[float] = deque(maxlen=STATS_WINDOW)
        self.marks_i50_for: deque[int] = deque(maxlen=STATS_WINDOW)


def _avg(values) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


def _snapshot(hist: _TeamRollingHistory) -> _TeamSnapshot:
    results = list(hist.results)
    margins = list(hist.margins)
    n_form, n_stats = len(results), len(hist.clearances_for)

    avg_cl_for, avg_cl_against = _avg(hist.clearances_for), _avg(hist.clearances_against)
    avg_i50_for, avg_i50_against = _avg(hist.inside50_for), _avg(hist.inside50_against)
    avg_cp_for, avg_cp_against = _avg(hist.contested_for), _avg(hist.contested_against)
    avg_tk_for, avg_tk_against = _avg(hist.tackles_for), _avg(hist.tackles_against)

    return _TeamSnapshot(
        games_played_form=n_form,
        games_played_stats=n_stats,
        wins_last_5=_avg(results[-FORM_WINDOW_SHORT:]) if n_form >= MIN_GAMES_FORM_SHORT else None,
        wins_last_10=_avg(results) if n_form >= MIN_GAMES_FORM_LONG else None,
        avg_margin_last_5=_avg(margins[-FORM_WINDOW_SHORT:]) if n_form >= MIN_GAMES_FORM_SHORT else None,
        avg_margin_last_10=_avg(margins) if n_form >= MIN_GAMES_FORM_LONG else None,
        points_for_last_5=_avg(hist.points_for) if len(hist.points_for) >= MIN_GAMES_FORM_SHORT else None,
        points_against_last_5=_avg(hist.points_against) if len(hist.points_against) >= MIN_GAMES_FORM_SHORT else None,
        clearance_differential=(avg_cl_for - avg_cl_against) if n_stats >= MIN_GAMES_STATS else None,
        inside_50_differential=(avg_i50_for - avg_i50_against) if n_stats >= MIN_GAMES_STATS else None,
        contested_possession_differential=(avg_cp_for - avg_cp_against) if n_stats >= MIN_GAMES_STATS else None,
        tackle_differential=(avg_tk_for - avg_tk_against) if n_stats >= MIN_GAMES_STATS else None,
        conversion_rate=_avg(hist.conversion) if len(hist.conversion) >= MIN_GAMES_STATS else None,
        marks_inside_50=_avg(hist.marks_i50_for) if len(hist.marks_i50_for) >= MIN_GAMES_STATS else None,
    )


def _diff(a: float | None, b: float | None) -> float | None:
    return (a - b) if a is not None and b is not None else None


def _build_features(home: _TeamSnapshot, away: _TeamSnapshot, league_home_rate: float) -> dict[str, float | None]:
    return {
        "form_diff_5": _diff(home.wins_last_5, away.wins_last_5),
        "form_diff_10": _diff(home.wins_last_10, away.wins_last_10),
        "margin_diff_5": _diff(home.avg_margin_last_5, away.avg_margin_last_5),
        "margin_diff_10": _diff(home.avg_margin_last_10, away.avg_margin_last_10),
        "points_for_diff_5": _diff(home.points_for_last_5, away.points_for_last_5),
        "points_against_diff_5": _diff(home.points_against_last_5, away.points_against_last_5),
        "clearance_differential_diff": _diff(home.clearance_differential, away.clearance_differential),
        "inside50_differential_diff": _diff(home.inside_50_differential, away.inside_50_differential),
        "contested_possession_differential_diff": _diff(
            home.contested_possession_differential, away.contested_possession_differential
        ),
        "tackle_differential_diff": _diff(home.tackle_differential, away.tackle_differential),
        "conversion_rate_diff": _diff(home.conversion_rate, away.conversion_rate),
        "marks_inside_50_diff": _diff(home.marks_inside_50, away.marks_inside_50),
        "league_home_win_rate": league_home_rate,
    }


def _update_history(hist: _TeamRollingHistory, m: MatchFeatureInput, is_home: bool) -> None:
    if is_home:
        own_score, opp_score = m.home_score, m.away_score
        own_cl, opp_cl = m.home_clearances, m.away_clearances
        own_i50, opp_i50 = m.home_inside_50s, m.away_inside_50s
        own_cp, opp_cp = m.home_contested_possessions, m.away_contested_possessions
        own_tk, opp_tk = m.home_tackles, m.away_tackles
        own_goals, own_behinds, own_mi50 = m.home_goals, m.home_behinds, m.home_marks_inside_50
    else:
        own_score, opp_score = m.away_score, m.home_score
        own_cl, opp_cl = m.away_clearances, m.home_clearances
        own_i50, opp_i50 = m.away_inside_50s, m.home_inside_50s
        own_cp, opp_cp = m.away_contested_possessions, m.home_contested_possessions
        own_tk, opp_tk = m.away_tackles, m.home_tackles
        own_goals, own_behinds, own_mi50 = m.away_goals, m.away_behinds, m.away_marks_inside_50

    outcome = 1.0 if own_score > opp_score else (0.0 if own_score < opp_score else 0.5)
    hist.results.append(outcome)
    hist.margins.append(own_score - opp_score)
    hist.points_for.append(own_score)
    hist.points_against.append(opp_score)

    if own_cl is not None and opp_cl is not None:
        hist.clearances_for.append(own_cl)
        hist.clearances_against.append(opp_cl)
    if own_i50 is not None and opp_i50 is not None:
        hist.inside50_for.append(own_i50)
        hist.inside50_against.append(opp_i50)
    if own_cp is not None and opp_cp is not None:
        hist.contested_for.append(own_cp)
        hist.contested_against.append(opp_cp)
    if own_tk is not None and opp_tk is not None:
        hist.tackles_for.append(own_tk)
        hist.tackles_against.append(opp_tk)
    if own_goals is not None and own_behinds is not None and (own_goals + own_behinds) > 0:
        hist.conversion.append(own_goals / (own_goals + own_behinds))
    if own_mi50 is not None:
        hist.marks_i50_for.append(own_mi50)


def build_match_features(
    matches: list[MatchFeatureInput], elo_prob_by_match: dict[int, float] | None = None
) -> list[MatchFeatureRow]:
    """Replays `matches` in chronological order. For each one, the feature
    snapshot is taken from state as it stood *before* that match — derived
    only from strictly earlier matches — and only afterwards is that state
    updated with the match's own result. `elo_prob_by_match`, if given, must
    itself already be a leakage-safe walk-forward result (see
    app/modelling/elo_backtest.py) — this function doesn't compute Elo
    itself, only attaches it as one more feature when present.
    """
    team_histories: dict[int, _TeamRollingHistory] = {}
    league_home_wins, league_games = 1.0, 2.0  # Beta(1,1) prior, same convention as baselines.py
    rows: list[MatchFeatureRow] = []

    for m in sorted(matches, key=lambda x: (x.scheduled_start, x.match_id)):
        home_hist = team_histories.setdefault(m.home_team_id, _TeamRollingHistory())
        away_hist = team_histories.setdefault(m.away_team_id, _TeamRollingHistory())

        home_snap = _snapshot(home_hist)
        away_snap = _snapshot(away_hist)
        league_home_rate = league_home_wins / league_games

        features = _build_features(home_snap, away_snap, league_home_rate)
        if elo_prob_by_match is not None:
            features[ELO_FEATURE_NAME] = elo_prob_by_match.get(m.match_id)

        has_full = all(v is not None for v in features.values())

        outcome = 1.0 if m.home_score > m.away_score else (0.0 if m.home_score < m.away_score else 0.5)
        rows.append(
            MatchFeatureRow(
                match_id=m.match_id,
                season_year=m.season_year,
                scheduled_start=m.scheduled_start,
                home_team_id=m.home_team_id,
                away_team_id=m.away_team_id,
                actual_home_outcome=outcome,
                features=features,
                has_full_history=has_full,
            )
        )

        _update_history(home_hist, m, is_home=True)
        _update_history(away_hist, m, is_home=False)
        league_home_wins += outcome
        league_games += 1.0

    return rows
