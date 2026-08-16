"""Baselines A-D from the disposal-prediction stage brief: strong, simple
rolling-average predictors a real model must beat before it's worth using.
Each one is a pure function over an already-built DisposalFeatureRow (see
disposal_features.py) — no fitting, no parameters, just reading a feature
column with a sensible fallback for players with too little history.
"""

from app.player_modelling.disposal_features import DisposalFeatureRow

LEAGUE_AVG_DISPOSALS_FALLBACK = 16.0


def baseline_last5(row: DisposalFeatureRow) -> float | None:
    """Baseline A: player's last-5-game disposal average."""
    return row.features.get("disposals_last5_avg")


def baseline_last10(row: DisposalFeatureRow) -> float | None:
    """Baseline B: player's last-10-game disposal average."""
    return row.features.get("disposals_last10_avg")


def baseline_ewma(row: DisposalFeatureRow) -> float | None:
    """Baseline C: exponentially-weighted disposal average (see
    disposal_features.EWMA_ALPHA)."""
    return row.features.get("disposals_ewma")


def baseline_season_avg(row: DisposalFeatureRow) -> float | None:
    """Baseline D: season-to-date average, falling back to this player's
    career-to-date average early in a season (when disposals_season_avg is
    still None or based on very few games), then to the league average for
    a player with no history at all - never a bare None, since a baseline
    that sometimes can't predict isn't a fair comparison for a model that
    always can."""
    season_avg = row.features.get("disposals_season_avg")
    if season_avg is not None:
        return season_avg
    career_avg = row.features.get("disposals_career_avg")
    if career_avg is not None:
        return career_avg
    return LEAGUE_AVG_DISPOSALS_FALLBACK


BASELINES = {
    "baseline_last5": baseline_last5,
    "baseline_last10": baseline_last10,
    "baseline_ewma": baseline_ewma,
    "baseline_season_avg": baseline_season_avg,
}
