"""Finals Market Readiness + Auto-Population stage, items 1-2: turns
already-resolved, already-fresh player-prop-market evidence into a
provisional (unconfirmed) roster, so projections can exist BEFORE official
teams are named.

Deliberately narrow and defensible, never inventive:
  - The player identity is never guessed here — a PlayerPropMarket row only
    ever exists with a player_id that prop_player_resolution.py already
    resolved via an exact/normalized/alias/nickname/initial match scoped to
    the match's own two teams (see that module's TRUSTED_TIERS gate). This
    module trusts that resolution, it does not re-derive it.
  - "Valid current-team/current-season evidence" (item 2's own phrase): the
    player's CURRENT team must be one of this match's two teams (a second,
    cheap re-check — defence in depth, not a new resolution path), AND the
    player must have at least one real PlayerMatchStat row in the match's
    own season (proof they are an active current-season player, not a
    stale/retired record a name coincidentally still resolves to).
  - The market itself must be FRESH (same freshness_state everything else
    in this app already uses) — a fresh market says "a bookmaker believes
    this person will play right now"; a stale one says nothing current.
  - A player who already has ANY ExpectedLineup row (manual, bulk, or a
    previous placeholder) is never touched here - this only ever FILLS an
    empty gap, never downgrades or overwrites a more authoritative status.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExpectedLineup, Match, Player, PlayerMatchStat, PlayerPropMarket
from app.models.expected_lineup import ExpectedLineupStatus, SelectionStatus
from app.player_modelling.prop_odds_freshness import DEFAULT_THRESHOLDS, FreshnessThresholds, freshness_state

PROVISIONAL_ROSTER_SOURCE = "provisional_from_prop_market"


@dataclass(frozen=True)
class ProvisionalRosterReport:
    match_id: int
    players_added: int
    players_considered: int


def _has_current_season_evidence(db: Session, player_id: int, season_id: int) -> bool:
    return db.scalar(
        select(PlayerMatchStat.id)
        .join(Match, Match.id == PlayerMatchStat.match_id)
        .where(PlayerMatchStat.player_id == player_id, Match.season_id == season_id)
        .limit(1)
    ) is not None


def populate_provisional_roster(
    db: Session, match_id: int, *, now: datetime | None = None, freshness_thresholds: FreshnessThresholds = DEFAULT_THRESHOLDS,
) -> ProvisionalRosterReport:
    now = now or datetime.now(timezone.utc)
    match = db.get(Match, match_id)
    if match is None:
        return ProvisionalRosterReport(match_id=match_id, players_added=0, players_considered=0)

    already_rostered = set(db.scalars(select(ExpectedLineup.player_id).where(ExpectedLineup.match_id == match_id)).all())

    quotes = db.scalars(select(PlayerPropMarket).where(PlayerPropMarket.match_id == match_id)).all()
    latest_quote_by_player: dict[int, PlayerPropMarket] = {}
    for q in quotes:
        current = latest_quote_by_player.get(q.player_id)
        if current is None or q.recorded_at > current.recorded_at:
            latest_quote_by_player[q.player_id] = q

    candidate_ids = {pid for pid in latest_quote_by_player if pid not in already_rostered}
    players_considered = len(candidate_ids)
    added = 0

    for player_id, quote in latest_quote_by_player.items():
        if player_id not in candidate_ids:
            continue
        if freshness_state(quote.recorded_at, now=now, thresholds=freshness_thresholds) == "stale":
            continue
        player = db.get(Player, player_id)
        if player is None or player.current_team_id not in (match.home_team_id, match.away_team_id):
            continue
        if not _has_current_season_evidence(db, player_id, match.season_id):
            continue

        db.add(ExpectedLineup(
            match_id=match_id, player_id=player_id, team_id=player.current_team_id,
            status=ExpectedLineupStatus.UNCERTAIN.value, selection_status=SelectionStatus.PLACEHOLDER.value,
            is_confirmed=False, recorded_at=now, source=PROVISIONAL_ROSTER_SOURCE,
            source_reference=f"player_prop_market:{quote.id}",
        ))
        added += 1

    db.commit()
    return ProvisionalRosterReport(match_id=match_id, players_added=added, players_considered=players_considered)
