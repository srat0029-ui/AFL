"""Derives a model's probability for one specific odds selection.

Elo only covers h2h. Poisson covers h2h, line, and total — using whatever
line_value the odds quote actually carries (a bookmaker-specific number),
not a value the model pre-computed for some other line. This is why
predictions aren't pre-computed and stored: a cached "model probability"
for total=165.5 would silently not apply if a bookmaker posts 162.5.

Line-market sign convention (not enforced by Stage 1.4's schema, so it's
documented here where it matters): line_value is from the SELECTED team's
own perspective, standard sportsbook style — negative for the favourite
(must win by more than |line_value|), positive for the underdog (can lose
by up to line_value and still cover). E.g. "Carlton -12.5" (Carlton
selected, line_value=-12.5): Carlton must win by more than 12.5. "Richmond
+12.5" (Richmond selected, line_value=+12.5): Richmond covers if it loses
by less than 12.5, draws, or wins.
"""

import numpy as np

from app.modelling.poisson_model import prob_margin_over, prob_total_over


def h2h_probability(elo_home_win_probability: float, selection: str, home_team_name: str, away_team_name: str) -> float:
    if selection == home_team_name:
        return elo_home_win_probability
    if selection == away_team_name:
        return 1.0 - elo_home_win_probability
    raise ValueError(f"selection {selection!r} is neither {home_team_name!r} nor {away_team_name!r}")


def line_probability(
    home_pmf: np.ndarray,
    away_pmf: np.ndarray,
    selection: str,
    line_value: float,
    home_team_name: str,
    away_team_name: str,
) -> float:
    if selection == home_team_name:
        return prob_margin_over(home_pmf, away_pmf, -line_value)
    if selection == away_team_name:
        return 1.0 - prob_margin_over(home_pmf, away_pmf, line_value)
    raise ValueError(f"selection {selection!r} is neither {home_team_name!r} nor {away_team_name!r}")


def total_probability(home_pmf: np.ndarray, away_pmf: np.ndarray, selection: str, line_value: float) -> float:
    p_over = prob_total_over(home_pmf, away_pmf, line_value)
    if selection.lower() == "over":
        return p_over
    if selection.lower() == "under":
        return 1.0 - p_over
    raise ValueError(f"selection must be 'over' or 'under' for a total market, got {selection!r}")
