"""Baselines A-E from the goal-prediction stage brief: strong, simple
scoring-rate predictors a real model must beat. Baseline E (team-adjusted
historical rate) is the one genuinely new idea vs the disposal baselines -
a player's own scoring rate scaled by how much their team is expected to
score relative to a league-average team, which is simple, leakage-safe
(both halves are already point-in-time features), and a reasonable proxy
for "this player scores more when their team scores more."
"""

from app.player_modelling.goal_features import GoalFeatureRow

LEAGUE_AVG_GOALS_FALLBACK = 0.53
LEAGUE_AVG_TEAM_SCORE_FALLBACK = 80.0  # roughly the league-wide average team score across the dataset


def baseline_last5(row: GoalFeatureRow) -> float | None:
    """Baseline A: player's last-5-game goals average."""
    return row.features.get("goals_last5_avg")


def baseline_last10(row: GoalFeatureRow) -> float | None:
    """Baseline B: player's last-10-game goals average."""
    return row.features.get("goals_last10_avg")


def baseline_ewma(row: GoalFeatureRow) -> float | None:
    """Baseline C: exponentially-weighted goals average."""
    return row.features.get("goals_ewma")


def baseline_season_avg(row: GoalFeatureRow) -> float | None:
    """Baseline D: season-to-date average, falling back to career average
    then the league average - never a bare None."""
    season_avg = row.features.get("goals_season_avg")
    if season_avg is not None:
        return season_avg
    career_avg = row.features.get("goals_career_avg")
    if career_avg is not None:
        return career_avg
    return LEAGUE_AVG_GOALS_FALLBACK


def baseline_team_adjusted_rate(row: GoalFeatureRow) -> float | None:
    """Baseline E: player's career scoring rate, scaled by how much their
    team is expected to score this match relative to a league-average
    team. Both halves are already point-in-time features (career_avg from
    this player's own prior games; team_expected_score from the leakage-
    safe Poisson walk-forward), so the combination introduces no new
    leakage risk - a simple multiplicative adjustment, not a fitted model."""
    career_avg = row.features.get("goals_career_avg") or baseline_season_avg(row)
    team_expected = row.features.get("team_expected_score")
    if career_avg is None:
        return None
    if team_expected is None:
        return career_avg
    return career_avg * (team_expected / LEAGUE_AVG_TEAM_SCORE_FALLBACK)


BASELINES = {
    "baseline_last5": baseline_last5,
    "baseline_last10": baseline_last10,
    "baseline_ewma": baseline_ewma,
    "baseline_season_avg": baseline_season_avg,
    "baseline_team_adjusted_rate": baseline_team_adjusted_rate,
}
