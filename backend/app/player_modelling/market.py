"""Market/line type definitions for player prop markets — schema only, no
probability computation (see the package docstring in
app/player_modelling/__init__.py). Different markets need genuinely
different models (a disposal-count model and a goal-count model have
different natural distributions — see projection.py's module docstring) so
this module keeps "what market is this" and "what number is being asked
about" as explicit, typed values rather than a loose string, so a future
model-selection layer can dispatch on PlayerMarket without re-parsing
strings.
"""

from dataclasses import dataclass
from enum import Enum


class PlayerMarket(str, Enum):
    DISPOSALS = "player_disposals"
    GOALS = "player_goals"
    TACKLES = "player_tackles"
    MARKS = "player_marks"
    # Extend here as new markets are modelled — deliberately not
    # exhaustive; adding a market is a one-line addition here plus a new
    # PlayerFeatureBuilder/model, not a schema migration.


class LineType(str, Enum):
    OVER_UNDER = "over_under"  # e.g. over/under 27.5 disposals
    MULTI_PLUS = "multi_plus"  # e.g. 20+, 25+, 30+, 35+ disposals ("N or more")


@dataclass(frozen=True)
class MarketLine:
    """One specific tradeable line within a market — e.g. PlayerMarket.DISPOSALS
    + LineType.OVER_UNDER + threshold=27.5, or PlayerMarket.GOALS +
    LineType.MULTI_PLUS + threshold=2 ("2+ goals"). threshold is always the
    number itself (27.5, 20, 2, ...), not a pre-formatted label — display
    formatting is a UI concern, not a modelling one.
    """

    market: PlayerMarket
    line_type: LineType
    threshold: float

    def label(self) -> str:
        if self.line_type is LineType.MULTI_PLUS:
            return f"{self.threshold:g}+"
        return f"over/under {self.threshold:g}"


# The specific lines this project expects to eventually price — a reference
# list for the future modelling stage, not something enforced at the type
# level (a MarketLine with an arbitrary threshold is still a valid value;
# these are just the commonly-quoted ones worth pre-listing now).
COMMON_DISPOSAL_LINES: list[MarketLine] = [
    MarketLine(PlayerMarket.DISPOSALS, LineType.MULTI_PLUS, t) for t in (20, 25, 30, 35)
] + [MarketLine(PlayerMarket.DISPOSALS, LineType.OVER_UNDER, 27.5)]

COMMON_GOAL_LINES: list[MarketLine] = [
    MarketLine(PlayerMarket.GOALS, LineType.MULTI_PLUS, t) for t in (1, 2, 3, 4)
]
