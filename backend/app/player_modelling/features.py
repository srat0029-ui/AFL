"""Point-in-time feature building for player-level models — architecture
only, no implementation of the actual feature calculations yet (see the
package docstring in app/player_modelling/__init__.py).

Mirrors app/modelling/features.py's leakage-safe discipline exactly: a
PlayerFeatureBuilder must compute each row's features from strictly-prior
matches only (the same walk-forward, snapshot-before-update pattern already
used for Elo, Poisson, and the team-level logistic/boosting features), never
from the match being predicted or anything later. Different markets are
expected to need different feature sets — disposal models likely lean on
recent disposal/time-on-ground history, goal models on recent
scoring/inside-50 involvement — so this is a per-market interface, not one
shared feature table.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.player_modelling.market import PlayerMarket


@dataclass(frozen=True)
class PlayerFeatureInput:
    """Raw point-in-time inputs available to a feature builder for one
    player entering one match — deliberately just identifiers and match
    context, not pre-computed stats. A PlayerFeatureBuilder is responsible
    for looking up that player's own PRIOR PlayerMatchStat history itself
    (see app/models/player_match_stat.py), the same way
    app/modelling/features.py's build_match_features() walks history
    forward rather than being handed it already computed."""

    player_id: int
    match_id: int
    team_id: int
    opponent_team_id: int
    season_year: int
    round_number: int
    scheduled_start: datetime


@dataclass(frozen=True)
class PlayerFeatureRow:
    """One computed feature row — market-specific field names live in
    `features`, not as fixed dataclass attributes, since different markets
    need different features (see module docstring). has_sufficient_history
    mirrors app/modelling/features.py's MIN_GAMES_* gating: a feature row
    built on too little prior history should be flagged, not silently
    treated as equally reliable as one built on a long history."""

    player_id: int
    match_id: int
    market: PlayerMarket
    features: dict[str, float | None] = field(default_factory=dict)
    has_sufficient_history: bool = False


class PlayerFeatureBuilder(Protocol):
    """One implementation per market (a disposals builder, a goals builder,
    ...) — see module docstring for why these aren't assumed to share a
    feature set. A concrete implementation (a future stage) must walk
    PlayerMatchStat history chronologically and snapshot state BEFORE
    incorporating the match being featured, exactly like
    app/modelling/features.py's build_match_features()."""

    market: PlayerMarket

    def build(self, inputs: list[PlayerFeatureInput]) -> list[PlayerFeatureRow]:
        ...
