"""The contract a future NBA prop model must satisfy. Types only — nothing
here computes a projection, and no model exists yet.

The intended model shape is

    stat  =  minutes played  x  production per minute

with minutes and the per-minute rate modelled SEPARATELY (teammate
availability and role changes mostly move minutes; opponent and pace mostly
move the rate). ProjectionOutput therefore carries both factors alongside
the combined distribution, so each can be evaluated on its own later —
"was the miss a minutes miss or a rate miss" is the first diagnostic
question for an NBA prop model, and it is unanswerable if only the product
is recorded.

Both factors are optional so that a simple baseline which does not
decompose (e.g. a rolling average) can run through the identical
freeze/close/settle pipeline and be compared honestly against a model that
does.
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.nba.markets import SELECTION_OVER, SELECTION_UNDER, NbaPropMarket


@runtime_checkable
class StatDistribution(Protocol):
    """A full probability distribution over one stat's outcome — a model
    must never return a point estimate alone, since a mean cannot price a
    line. `prob_over` and `prob_under` are separate methods because on a
    whole-number line they do not sum to 1: the remainder is the push."""

    def mean(self) -> float: ...

    def prob_over(self, line: float) -> float:
        """P(outcome > line)."""
        ...

    def prob_under(self, line: float) -> float:
        """P(outcome < line)."""
        ...


@dataclass(frozen=True)
class ProjectionOutput:
    """What a model returns for one player/game/market, ready to be recorded
    as an NbaPropProjection row."""

    market: NbaPropMarket
    distribution: StatDistribution
    distribution_kind: str
    distribution_params: dict
    games_of_history: int
    expected_minutes: float | None = None
    rate_per_minute: float | None = None
    inputs: dict = field(default_factory=dict)


def selection_probability(distribution: StatDistribution, line: float, selection: str) -> float:
    if selection == SELECTION_OVER:
        return distribution.prob_over(line)
    if selection == SELECTION_UNDER:
        return distribution.prob_under(line)
    raise ValueError(f"selection must be 'over' or 'under', got {selection!r}")
