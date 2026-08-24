"""Match-day readiness (Finals Multi Quality + Match-Day Readiness stage,
item 12, extended by the Finals Market Readiness + Auto-Population stage's
items 3-4): a compact three-state signal — NOT_READY / PROVISIONAL / READY —
plus a full per-signal breakdown and a single "what is missing" sentence,
answering "should I trust the current Multi Builder output for this match
right now, and if not, what exactly is the app still waiting on."

Built entirely from already-persisted state: no external call, no new
freshness/staleness threshold, no new detection. player_identities_resolved
is always equal to player_props_exist by construction — prop_player_
resolution.py (reused unchanged by the odds-refresh path) never lets an
unresolved or ambiguous player name reach a persisted PlayerPropMarket row
in the first place, so a row existing at all IS the resolution proof.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExpectedLineup, OddsQuote, PlayerDisposalProjection, PlayerGoalProjection, PlayerPropMarket
from app.models.expected_lineup import CONFIRMED_SELECTION_STATUSES
from app.player_modelling.best_opportunities import load_best_opportunities
from app.player_modelling.live_change_detection import detect_matches_needing_regeneration
from app.player_modelling.prop_odds_freshness import DEFAULT_THRESHOLDS, freshness_state
from app.player_modelling.quality_tiers import TIER_DO_NOT_HEADLINE
from app.player_modelling.upcoming_features import load_next_upcoming_round

READY = "READY"
PROVISIONAL = "PROVISIONAL"
NOT_READY = "NOT_READY"


@dataclass(frozen=True)
class MatchReadiness:
    match_id: int
    state: str
    reasons: list[str] = field(default_factory=list)
    missing_explanation: str = ""

    # item 3's explicit per-signal breakdown
    team_odds_fresh: bool = False
    player_props_exist: bool = False
    player_props_fresh: bool = False
    player_identities_resolved: bool = False
    provisional_roster_available: bool = False
    projections_generated: bool = False
    projections_current: bool = False
    official_teams_confirmed: bool = False
    usable_multi_legs: int = 0

    # kept for backward compatibility with the Finals Multi Quality stage's
    # own fields (has_fresh_odds/has_projections/teams_confirmed) - equal
    # to the more specific booleans above.
    has_fresh_odds: bool = False
    has_projections: bool = False
    teams_confirmed: bool = False


def _missing_explanation(
    *, player_props_exist: bool, player_props_fresh: bool, projections_generated: bool,
    team_odds_fresh: bool, official_teams_confirmed: bool, usable_multi_legs: int,
) -> str:
    """One sentence, most-fundamental-blocker-first (item 4) - matches the
    example phrasings verbatim where they apply."""
    if not player_props_exist:
        return "Waiting for player prop markets."
    if not player_props_fresh and not projections_generated:
        return "Player prop odds are stale — refresh required."
    if not projections_generated:
        return "Player markets available, projections not generated."
    if not team_odds_fresh:
        return "Odds stale — refresh required."
    if not official_teams_confirmed:
        return "Provisional only — official teams not yet confirmed."
    if usable_multi_legs == 0:
        return "Projections current, but no leg currently clears the quality/probability gates."
    return ""


def compute_match_readiness(db: Session, match_id: int, *, raw_opportunities: list[dict] | None = None) -> MatchReadiness:
    """`raw_opportunities`: pass the SAME load_best_opportunities(...,
    include_stale=True) list a caller already fetched for other matches
    this request (e.g. the round-summary route, one match at a time in a
    loop) to avoid re-running that full, uncached-past-model-context scan
    once per match. Self-fetches only when nothing is supplied, so existing
    single-match callers/tests are unaffected."""
    raw = raw_opportunities if raw_opportunities is not None else load_best_opportunities(
        db, market_scope="all", include_uncertain=True, include_stale=True, include_insufficient_history=True, limit=None,
    )
    match_legs = [leg for leg in raw if leg["match_id"] == match_id]
    usable_multi_legs = sum(1 for leg in match_legs if leg["quality_tier"]["tier"] != TIER_DO_NOT_HEADLINE)

    team_odds_fresh = any(
        freshness_state(q.recorded_at, thresholds=DEFAULT_THRESHOLDS) != "stale"
        for q in db.scalars(select(OddsQuote).where(OddsQuote.match_id == match_id)).all()
    )

    prop_quotes = db.scalars(select(PlayerPropMarket).where(PlayerPropMarket.match_id == match_id)).all()
    player_props_exist = bool(prop_quotes)
    player_props_fresh = any(freshness_state(q.recorded_at, thresholds=DEFAULT_THRESHOLDS) != "stale" for q in prop_quotes)
    # See module docstring: only ever true together with player_props_exist.
    player_identities_resolved = player_props_exist

    provisional_roster_available = db.scalar(select(ExpectedLineup.id).where(ExpectedLineup.match_id == match_id).limit(1)) is not None
    official_teams_confirmed = db.scalar(
        select(ExpectedLineup.id).where(
            ExpectedLineup.match_id == match_id, ExpectedLineup.selection_status.in_(CONFIRMED_SELECTION_STATUSES)
        ).limit(1)
    ) is not None

    has_disposal_proj = db.scalar(select(PlayerDisposalProjection.id).where(PlayerDisposalProjection.match_id == match_id).limit(1)) is not None
    has_goal_proj = db.scalar(select(PlayerGoalProjection.id).where(PlayerGoalProjection.match_id == match_id).limit(1)) is not None
    projections_generated = has_disposal_proj or has_goal_proj

    projections_current = True
    if projections_generated:
        upcoming = load_next_upcoming_round(db)
        this_match = next((m for m in upcoming if m.match_id == match_id), None)
        if this_match is not None:
            projections_current = match_id not in detect_matches_needing_regeneration(db, [this_match])

    reasons: list[str] = []
    if not team_odds_fresh:
        reasons.append("Team odds are stale or unavailable for this match.")
    if not player_props_exist:
        reasons.append("No player prop markets exist yet for this match.")
    elif not player_props_fresh:
        reasons.append("Player prop odds are stale.")
    if not projections_generated:
        reasons.append("No player projections exist yet for this match.")
    elif not projections_current:
        reasons.append("Projections are out of date and need regeneration.")
    if not official_teams_confirmed:
        reasons.append("Official teams are not yet confirmed.")

    have_usable_projection_inputs = player_props_exist and player_props_fresh and team_odds_fresh
    if not have_usable_projection_inputs or not projections_generated or not projections_current:
        state = NOT_READY
    elif not official_teams_confirmed:
        state = PROVISIONAL
    else:
        state = READY

    missing = _missing_explanation(
        player_props_exist=player_props_exist, player_props_fresh=player_props_fresh,
        projections_generated=projections_generated, team_odds_fresh=team_odds_fresh,
        official_teams_confirmed=official_teams_confirmed, usable_multi_legs=usable_multi_legs,
    )

    return MatchReadiness(
        match_id=match_id, state=state, reasons=reasons, missing_explanation=missing,
        team_odds_fresh=team_odds_fresh, player_props_exist=player_props_exist, player_props_fresh=player_props_fresh,
        player_identities_resolved=player_identities_resolved, provisional_roster_available=provisional_roster_available,
        projections_generated=projections_generated, projections_current=projections_current,
        official_teams_confirmed=official_teams_confirmed, usable_multi_legs=usable_multi_legs,
        has_fresh_odds=team_odds_fresh and player_props_fresh, has_projections=projections_generated,
        teams_confirmed=official_teams_confirmed,
    )
