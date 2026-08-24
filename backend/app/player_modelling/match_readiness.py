"""Match-day readiness (Finals Multi Quality + Match-Day Readiness stage,
item 12): a compact three-state signal — NOT_READY / PROVISIONAL / READY —
answering "should I trust the current Multi Builder output for this match
right now." Built entirely from already-computed signals: each leg's own
odds_freshness (the exact field the builder itself already reads), whether
any player projection exists, live_change_detection's own regeneration-
staleness check (the same one `refresh-live` uses to decide what needs
recomputing), and ExpectedLineup confirmation — no new detection, no new
freshness/staleness thresholds invented here.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExpectedLineup, PlayerDisposalProjection, PlayerGoalProjection
from app.models.expected_lineup import CONFIRMED_SELECTION_STATUSES
from app.player_modelling.best_opportunities import load_best_opportunities
from app.player_modelling.live_change_detection import detect_matches_needing_regeneration
from app.player_modelling.upcoming_features import load_next_upcoming_round

READY = "READY"
PROVISIONAL = "PROVISIONAL"
NOT_READY = "NOT_READY"


@dataclass(frozen=True)
class MatchReadiness:
    match_id: int
    state: str
    reasons: list[str] = field(default_factory=list)
    has_fresh_odds: bool = False
    has_projections: bool = False
    projections_current: bool = False
    teams_confirmed: bool = False


def compute_match_readiness(db: Session, match_id: int) -> MatchReadiness:
    """Fetches the SAME raw opportunity feed the builder itself is built on
    (load_best_opportunities, include_stale=True) so a stale market can be
    told apart from no market at all — the builder's own leg count already
    has stale legs hard-excluded via quality_tiers.py, which would make
    "no eligible legs" and "all legs stale" indistinguishable if this reused
    that already-filtered list instead."""
    raw = load_best_opportunities(
        db, market_scope="all", include_uncertain=True, include_stale=True, include_insufficient_history=True, limit=None,
    )
    match_legs = [leg for leg in raw if leg["match_id"] == match_id]
    has_fresh_odds = any(leg["odds_freshness"] != "stale" for leg in match_legs)

    has_disposal_proj = db.scalar(select(PlayerDisposalProjection.id).where(PlayerDisposalProjection.match_id == match_id).limit(1)) is not None
    has_goal_proj = db.scalar(select(PlayerGoalProjection.id).where(PlayerGoalProjection.match_id == match_id).limit(1)) is not None
    has_projections = has_disposal_proj or has_goal_proj

    teams_confirmed = db.scalar(
        select(ExpectedLineup.id).where(
            ExpectedLineup.match_id == match_id, ExpectedLineup.selection_status.in_(CONFIRMED_SELECTION_STATUSES)
        ).limit(1)
    ) is not None

    projections_current = True
    if has_projections:
        upcoming = load_next_upcoming_round(db)
        this_match = next((m for m in upcoming if m.match_id == match_id), None)
        if this_match is not None:
            projections_current = match_id not in detect_matches_needing_regeneration(db, [this_match])

    reasons: list[str] = []
    if not has_fresh_odds:
        reasons.append("Odds are stale or unavailable for this match.")
    if not has_projections:
        reasons.append("No player projections exist yet for this match.")
    elif not projections_current:
        reasons.append("Projections are out of date and need regeneration.")
    if not teams_confirmed:
        reasons.append("Teams are not yet confirmed.")

    if not has_fresh_odds or not has_projections or not projections_current:
        state = NOT_READY
    elif not teams_confirmed:
        state = PROVISIONAL
    else:
        state = READY

    return MatchReadiness(
        match_id=match_id, state=state, reasons=reasons, has_fresh_odds=has_fresh_odds,
        has_projections=has_projections, projections_current=projections_current, teams_confirmed=teams_confirmed,
    )
