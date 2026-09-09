"""Match Pricing Context — one shared, read-only preload per match, used
by every path that otherwise repeats the same handful of queries once per
player/threshold: `app.pricing.snapshot_service.snapshot_round_pricing`
and `app.market_monitor.detector`'s per-match anomaly detection (which
both `market_monitor_prospective_snapshots`'s `build_trader_inbox` and
`market_monitor_alert_snapshots`'s `freeze_anomaly_alerts` call via
`detect_match_anomalies` — see that module's docstring). Same "preload
once, reuse in memory" discipline already proven by
`app.player_modelling.prop_observation.ObservationMatchContext`; this is
its counterpart for the pricing/market-intelligence read path.

A production forensic review found `player_market_intelligence` /
`team_market_intelligence` (and the consensus/devig lookups they call
into) each independently re-query Bookmaker, OddsQuote, and
PlayerPropMarket per player/threshold — for a 46-player match at 5+4
thresholds each, that is hundreds of otherwise-identical round trips
against the exact same match's already-loaded data. The market-monitor
detector separately re-queries the full Bookmaker table and re-derives
match-wide lineup/context state once PER PLAYER, when both are identical
for every player in the same match. This context preloads all of that
ONCE and every consumer below reads it via plain dict/list filtering in
memory instead.

Building this context makes zero pricing/anomaly/confidence decisions
itself — every field is a straight, unfiltered read of already-persisted
rows for one match_id, exactly what each caller already queried before
this existed. Passing `context=None` anywhere it's accepted (the default)
preserves the exact prior per-call query behaviour unchanged — every
caller that hasn't been updated to build/pass a context keeps working
exactly as before.

`existing_team_snapshot_keys`/`existing_player_snapshot_keys` reuse the
NEW canonical per-family PricingSnapshot identity from revision
75715f635e7a (see app/pricing/snapshot_service.py's
`_team_snapshot_identity_clause`/`_player_snapshot_identity_clause`,
which this module's key tuples mirror field-for-field) — never the old,
broken single key.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Bookmaker,
    ExpectedLineup,
    MatchContextItem,
    OddsQuote,
    PlayerDisposalProjection,
    PlayerGoalProjection,
    PlayerPropMarket,
    PricingSnapshot,
)
from app.player_modelling.match_context_service import current_context_for_match


@dataclass(frozen=True)
class MatchPricingContext:
    match_id: int
    bookmakers_by_id: dict[int, Bookmaker]
    bookmakers_by_name: dict[str, Bookmaker]
    odds_quotes: list[OddsQuote]  # every OddsQuote row for this match (team markets)
    prop_markets: list[PlayerPropMarket]  # every PlayerPropMarket row for this match (player markets)
    lineups_by_player: dict[int, ExpectedLineup]
    context_items: list[MatchContextItem]  # current_context_for_match's result, computed once
    disposal_projections: list[PlayerDisposalProjection]
    goal_projections: list[PlayerGoalProjection]
    existing_team_snapshot_keys: set[tuple]  # (match_id, market_type, selection, line_value, model_version)
    existing_player_snapshot_keys: set[tuple]  # (match_id, player_id, market_type, selection, threshold, model_version)


def build_match_pricing_context(db: Session, match_id: int) -> MatchPricingContext:
    bookmakers = db.scalars(select(Bookmaker)).all()
    odds_quotes = db.scalars(select(OddsQuote).where(OddsQuote.match_id == match_id)).all()
    prop_markets = db.scalars(select(PlayerPropMarket).where(PlayerPropMarket.match_id == match_id)).all()
    lineups = db.scalars(select(ExpectedLineup).where(ExpectedLineup.match_id == match_id)).all()
    disposal_projections = db.scalars(select(PlayerDisposalProjection).where(PlayerDisposalProjection.match_id == match_id)).all()
    goal_projections = db.scalars(select(PlayerGoalProjection).where(PlayerGoalProjection.match_id == match_id)).all()

    existing_team_keys: set[tuple] = set()
    existing_player_keys: set[tuple] = set()
    snapshot_rows = db.execute(
        select(
            PricingSnapshot.player_id, PricingSnapshot.market_type, PricingSnapshot.selection,
            PricingSnapshot.threshold, PricingSnapshot.line_value, PricingSnapshot.model_version,
        ).where(PricingSnapshot.match_id == match_id)
    ).all()
    for player_id, market_type, selection, threshold, line_value, model_version in snapshot_rows:
        if player_id is None:
            existing_team_keys.add((match_id, market_type, selection, line_value, model_version))
        else:
            existing_player_keys.add((match_id, player_id, market_type, selection, threshold, model_version))

    return MatchPricingContext(
        match_id=match_id,
        bookmakers_by_id={b.id: b for b in bookmakers},
        bookmakers_by_name={b.name: b for b in bookmakers},
        odds_quotes=list(odds_quotes),
        prop_markets=list(prop_markets),
        lineups_by_player={lu.player_id: lu for lu in lineups},
        context_items=current_context_for_match(db, match_id),
        disposal_projections=list(disposal_projections),
        goal_projections=list(goal_projections),
        existing_team_snapshot_keys=existing_team_keys,
        existing_player_snapshot_keys=existing_player_keys,
    )
