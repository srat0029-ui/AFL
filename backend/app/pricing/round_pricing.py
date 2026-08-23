"""Round-wide pricing (item 3): prices every team + player market for the
current upcoming AFL round in one call.

Reuses team_pricing.py/player_pricing.py per match — the only new logic
here is orchestration plus a short TTL cache around the whole response
(see app/player_modelling/request_cache.py) so a repeat request within the
cache window is a cache hit, not a recompute. This is a READ over an
already-fitted team model and already-persisted player projections (see
player_pricing.py's module docstring on why those are read, not
recomputed) — never a retrain — so caching exists purely to avoid
repeating the same cheap-but-not-free arithmetic many times a minute
under real traffic, not to hide an expensive operation.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.edges.calculator import build_model_context
from app.models import Match, PlayerDisposalProjection, PlayerGoalProjection
from app.player_modelling.request_cache import cached_with_ttl
from app.player_modelling.upcoming_features import load_next_upcoming_round
from app.pricing.player_pricing import DisposalPrice, GoalPrice, price_disposals, price_goals
from app.pricing.team_pricing import TeamMarketPrice, latest_completed_match_timestamp, price_team_market

# Deliberately short: this only guards against repeat requests recomputing
# identical arithmetic seconds apart, never intended to hide staleness -
# the underlying data it reads only actually changes when a live-cycle
# refresh runs (see live_cycle.py), which is on the order of minutes, not
# seconds.
ROUND_PRICING_TTL_SECONDS = 30.0


@dataclass
class RoundPricing:
    round_number: int | None
    season_year: int | None
    n_matches: int
    teams: list[TeamMarketPrice] = field(default_factory=list)
    disposals: list[DisposalPrice] = field(default_factory=list)
    goals: list[GoalPrice] = field(default_factory=list)


def _compute_current_round(db: Session) -> RoundPricing:
    upcoming = load_next_upcoming_round(db)
    if not upcoming:
        return RoundPricing(round_number=None, season_year=None, n_matches=0)

    context = build_model_context(db)
    match_ids = [m.match_id for m in upcoming]
    generated_at = datetime.now(timezone.utc)
    data_cutoff = latest_completed_match_timestamp(db) or generated_at

    teams = [price_team_market(db.get(Match, m.match_id), context, generated_at, data_cutoff) for m in upcoming]
    disposals = [
        price_disposals(db, row)
        for row in db.scalars(select(PlayerDisposalProjection).where(PlayerDisposalProjection.match_id.in_(match_ids))).all()
    ]
    goals = [
        price_goals(db, row)
        for row in db.scalars(select(PlayerGoalProjection).where(PlayerGoalProjection.match_id.in_(match_ids))).all()
    ]

    return RoundPricing(
        round_number=upcoming[0].round_number, season_year=upcoming[0].season_year, n_matches=len(upcoming),
        teams=teams, disposals=disposals, goals=goals,
    )


def price_current_round(db: Session, *, use_cache: bool = True) -> RoundPricing:
    if not use_cache:
        return _compute_current_round(db)
    return cached_with_ttl(db, ("pricing_current_round",), lambda: _compute_current_round(db), ttl_seconds=ROUND_PRICING_TTL_SECONDS)
