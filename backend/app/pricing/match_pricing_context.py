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

`disposal_model_version`/`goal_model_version`/`calibration_by_key` exist
for the SAME reason: a follow-up trace (real local PostgreSQL 18, one
46-player match) found `app.pricing.player_pricing.price_disposals`/
`price_goals` each independently re-querying the globally-promoted
PlayerModelRun/GoalModelRun row (once directly via
current_disposal_model_version/current_goal_model_version, and AGAIN
inside historical_calibration_metrics's own internal lookup of the same
row) plus a calibration-metric row, once PER PLAYER - 472 total queries
for price_single_match alone at 46 players, none of which vary by player:
- `current_disposal_model_version`/`current_goal_model_version` take no
  match/player argument at all - the promoted model is a single global
  fact for the whole pricing pass.
- `historical_calibration_metrics(db, market_type, threshold)` is a PURE
  function of exactly those two arguments (see its source) - it snaps
  `threshold` to the nearest of a small fixed candidate list
  (_DISPOSAL_CALIBRATION_THRESHOLDS/_GOAL_CALIBRATION_THRESHOLDS) before
  ever touching the DB, and every real call site passes a value drawn
  from an equally small fixed set: goals ALWAYS pass 1.5; disposals pass
  whichever of the 5 DEFAULT_DISPOSAL_THRESHOLDS values is nearest to a
  player's own predicted_mean. So across an entire match there are AT
  MOST 5 distinct disposal results and exactly 1 distinct goal result
  possible, no matter how many players there are - `calibration_by_key`
  precomputes all of them (still forwarding to the same real
  historical_calibration_metrics function, not reimplementing its
  methodology or data window) and callers do a dict lookup instead of a
  query.
`players_by_id` exists because `row.player.display_name` (PlayerDisposalProjection/
PlayerGoalProjection's lazily-loaded `player` relationship) was ALSO
issuing one extra query per call, per row - not one of the four
originally-suspected functions, but found by the same trace and fixed the
same way.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Bookmaker,
    ExpectedLineup,
    MatchContextItem,
    OddsQuote,
    Player,
    PlayerDisposalProjection,
    PlayerGoalProjection,
    PlayerPropMarket,
    PricingSnapshot,
)
from app.player_modelling.market import PlayerMarket
from app.player_modelling.match_context_service import current_context_for_match
from app.player_modelling.live_report_query import CalibrationMetrics, current_disposal_model_version, current_goal_model_version, historical_calibration_metrics


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
    players_by_id: dict[int, Player]  # every player referenced by the projections/lineups above
    disposal_model_version: str | None
    goal_model_version: str | None
    calibration_by_key: dict[tuple[str, float], "CalibrationMetrics | None"]  # (market_type, threshold) -> result
    existing_team_snapshot_keys: set[tuple]  # (match_id, market_type, selection, line_value, model_version)
    existing_player_snapshot_keys: set[tuple]  # (match_id, player_id, market_type, selection, threshold, model_version)


def build_match_pricing_context(db: Session, match_id: int) -> MatchPricingContext:
    from app.pricing.player_pricing import DEFAULT_DISPOSAL_THRESHOLDS

    bookmakers = db.scalars(select(Bookmaker)).all()
    odds_quotes = db.scalars(select(OddsQuote).where(OddsQuote.match_id == match_id)).all()
    prop_markets = db.scalars(select(PlayerPropMarket).where(PlayerPropMarket.match_id == match_id)).all()
    lineups = db.scalars(select(ExpectedLineup).where(ExpectedLineup.match_id == match_id)).all()
    disposal_projections = db.scalars(select(PlayerDisposalProjection).where(PlayerDisposalProjection.match_id == match_id)).all()
    goal_projections = db.scalars(select(PlayerGoalProjection).where(PlayerGoalProjection.match_id == match_id)).all()

    player_ids = {row.player_id for row in disposal_projections} | {row.player_id for row in goal_projections} | {lu.player_id for lu in lineups}
    players_by_id = {p.id: p for p in db.scalars(select(Player).where(Player.id.in_(player_ids))).all()} if player_ids else {}

    # Precompute the small, fixed set of (market_type, threshold) pairs
    # every real call site can ever pass - see the module docstring.
    calibration_by_key: dict[tuple[str, float], CalibrationMetrics | None] = {}
    for t in DEFAULT_DISPOSAL_THRESHOLDS:
        calibration_by_key[(PlayerMarket.DISPOSALS.value, t)] = historical_calibration_metrics(db, PlayerMarket.DISPOSALS.value, t)
    calibration_by_key[(PlayerMarket.GOALS.value, 1.5)] = historical_calibration_metrics(db, PlayerMarket.GOALS.value, 1.5)

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
        players_by_id=players_by_id,
        disposal_model_version=current_disposal_model_version(db),
        goal_model_version=current_goal_model_version(db),
        calibration_by_key=calibration_by_key,
        existing_team_snapshot_keys=existing_team_keys,
        existing_player_snapshot_keys=existing_player_keys,
    )
