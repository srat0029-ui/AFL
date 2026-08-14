"""Elo rating engine for AFL match-winner probability.

Standard Elo with two AFL-appropriate adjustments, both well-established in
public sports-Elo methodology (not invented here):

1. Margin-of-victory multiplier — a 50-point win moves ratings more than a
   1-point win, dampened by the pre-game rating gap so beating a much
   weaker team by a lot doesn't move ratings as much as an upset margin
   would. This is FiveThirtyEight's NBA Elo formula, adapted.
2. Season carryover — at the start of each new season, ratings partially
   regress toward the mean to account for off-season list changes (trades,
   drafts, retirements), rather than carrying full certainty across the
   off-season.

Home-ground advantage is a single global constant, not per-team or
per-venue — that refinement belongs to the richer feature-based models in
a later stage, not this baseline.

Both the margin-of-victory behaviour and season-carryover strength are
config knobs, not fixed constants — see elo_tuning.py, which selects them
by walk-forward validation rather than guesswork.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class EloConfig:
    k_factor: float = 32.0
    home_advantage: float = 35.0
    initial_rating: float = 1500.0
    use_margin_of_victory: bool = True
    season_carryover: float = 0.75  # fraction of (rating - initial_rating) retained across a season boundary


class EloEngine:
    def __init__(self, config: EloConfig | None = None):
        self.config = config or EloConfig()

    def expected_home_win_prob(self, home_rating: float, away_rating: float) -> float:
        diff = (home_rating + self.config.home_advantage) - away_rating
        return 1.0 / (1.0 + 10 ** (-diff / 400.0))

    def update(
        self, home_rating: float, away_rating: float, home_score: int, away_score: int
    ) -> tuple[float, float]:
        expected_home = self.expected_home_win_prob(home_rating, away_rating)

        if home_score > away_score:
            actual_home = 1.0
        elif home_score < away_score:
            actual_home = 0.0
        else:
            actual_home = 0.5

        multiplier = 1.0
        if self.config.use_margin_of_victory:
            multiplier = self._margin_of_victory_multiplier(
                margin=abs(home_score - away_score),
                elo_diff=home_rating - away_rating,
                actual_home=actual_home,
            )

        delta = self.config.k_factor * multiplier * (actual_home - expected_home)
        return home_rating + delta, away_rating - delta

    def _margin_of_victory_multiplier(self, margin: int, elo_diff: float, actual_home: float) -> float:
        if margin == 0:
            return 1.0
        winner_elo_diff = elo_diff if actual_home >= 0.5 else -elo_diff
        denominator = max(winner_elo_diff * 0.001 + 2.2, 0.5)  # clamp: guard against a runaway rating gap
        return math.log(margin + 1) * (2.2 / denominator)

    def regress_to_mean(self, rating: float) -> float:
        return self.config.initial_rating + self.config.season_carryover * (rating - self.config.initial_rating)
