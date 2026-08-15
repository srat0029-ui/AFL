"""Output shapes for a future player-projection model. Nothing in this
module computes a projection — see the package docstring in
app/player_modelling/__init__.py for why this stage builds only the
architecture, not the models themselves.

The central design requirement this encodes: a player model must eventually
produce a full probability distribution over outcomes, not just a point
estimate. A disposal-count model that only outputs "29.8 expected
disposals" cannot answer "P(30+ disposals)" — that requires the underlying
distribution (e.g. a fitted Poisson/negative-binomial/discretised-normal),
the same way app/modelling/poisson_model.py already does for team scores.
ProjectionDistribution is written the same way: it wraps whatever
distribution a future model produces and exposes both point-estimate and
threshold-probability queries, so no model implementation is tempted to
skip straight to a point estimate.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.player_modelling.market import LineType, MarketLine, PlayerMarket


@runtime_checkable
class ProjectionDistribution(Protocol):
    """What any future player-outcome distribution must support —
    deliberately minimal so a Poisson count model, a negative-binomial
    model, and a discretised-normal model (whichever turns out to fit a
    given market best) can all satisfy it without a shared base class. See
    app/modelling/poisson_model.py's score_distribution()/win_draw_loss()
    for the equivalent, already-implemented pattern at team level.
    """

    def mean(self) -> float:
        """The point estimate — e.g. "29.8 expected disposals"."""
        ...

    def prob_at_least(self, threshold: float) -> float:
        """P(outcome >= threshold) — e.g. prob_at_least(30) for "30+ disposals"."""
        ...

    def prob_over(self, line: float) -> float:
        """P(outcome > line) — for a half-point over/under line (e.g. 27.5).
        Kept as a separate method rather than reused via prob_at_least,
        since that equivalence (prob_over(27.5) == prob_at_least(28) for an
        integer-valued outcome) doesn't hold for every future market — a
        continuous-valued fantasy-points model, for instance, has no such
        shortcut."""
        ...


@dataclass(frozen=True)
class PlayerProjection:
    """One model's projection for one player, in one match, for one market —
    the unit a future API endpoint or UI component would consume. Carries
    the full distribution (not just a point estimate), plus enough
    provenance to trust or distrust it."""

    player_id: int
    match_id: int
    market: PlayerMarket
    distribution: ProjectionDistribution
    model_name: str
    # Point-in-time discipline: how many of the player's prior games
    # (strictly before this match) fed this projection, surfaced
    # explicitly rather than assumed — so a projection built on very
    # little history (e.g. a debutant) is visibly low-confidence rather
    # than silently treated the same as a veteran's 100-game history. See
    # app/player_modelling/features.py's PlayerFeatureRow.has_sufficient_history.
    games_of_history: int

    def probability_for_line(self, line: MarketLine) -> float:
        if line.market != self.market:
            raise ValueError(f"line is for market {line.market!r}, this projection is for {self.market!r}")
        if line.line_type is LineType.MULTI_PLUS:
            return self.distribution.prob_at_least(line.threshold)
        return self.distribution.prob_over(line.threshold)
