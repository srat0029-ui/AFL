"""Ingests team-selection information into ExpectedLineup rows — Section 4
of the team-selection stage brief. Deliberately source-agnostic: it accepts
already-shaped SelectionEntry objects from WHATEVER produced them, whether
that's the efficient manual bulk-entry workflow (the only source available
today — see the stage report's Section 1 source audit: no reliable,
stable, low-risk automated AFL team-selection source currently exists) or
a future real automated provider. Adding a real provider later means
writing something that emits SelectionEntry objects and calls
ingest_team_selections, not touching this module's logic.

Player identity resolution never invents a player (Section 4's explicit
requirement): matching is scoped to the given team_id's CURRENT roster
first, falling back to any player with historical PlayerMatchStat rows for
that team (covers a recent trade where current_team_id hasn't been synced
yet). An unmatched or ambiguous (multiple same-name players for that team)
name is reported, never guessed.

Manual-override preservation (Section 3): a row a human directly edited via
the single-player PUT endpoint is marked is_manual_override=True there;
this module's writes always clear that flag to False (a bulk/automated
ingestion write is not itself "an override" — see
app/api/routes/player_projections.py for the asymmetry), and SKIP any
existing overridden row entirely unless allow_override_manual=True is
passed explicitly.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ExpectedLineup, Player, PlayerAlias, PlayerMatchStat, SelectionStatus, derive_coarse_status

ANNOUNCEMENT_NOT_ANNOUNCED = "teams_not_announced"
ANNOUNCEMENT_SQUAD_ANNOUNCED = "squad_announced"
ANNOUNCEMENT_FINAL_CONFIRMED = "final_team_confirmed"


@dataclass(frozen=True)
class SelectionEntry:
    team_id: int
    selection_status: str  # SelectionStatus value
    player_id: int | None = None  # pass this when identity is already known (e.g. chosen from a UI dropdown) to skip name matching
    player_name: str | None = None  # raw text from the source, used for matching when player_id is None
    note: str | None = None
    source_reference: str | None = None


@dataclass(frozen=True)
class PlayerResolution:
    player: Player | None
    is_ambiguous: bool = False


def resolve_player_identity(db: Session, team_id: int, player_name: str) -> PlayerResolution:
    name_norm = player_name.strip()

    # Section 9 of the live-operations stage brief: an explicit, reviewed
    # PlayerAlias claim is tried first, same reasoning as
    # prop_player_resolution.py's RESOLUTION_ALIAS tier — never fuzzy,
    # always a specific human-entered string-to-player mapping.
    aliased = db.scalars(
        select(PlayerAlias).where(func.lower(PlayerAlias.alias_name) == name_norm.lower())
    ).all()
    aliased_players = {a.player.id: a.player for a in aliased if a.player.current_team_id == team_id}
    if len(aliased_players) == 1:
        return PlayerResolution(player=next(iter(aliased_players.values())))
    if len(aliased_players) > 1:
        return PlayerResolution(player=None, is_ambiguous=True)

    current_matches = db.scalars(
        select(Player).where(Player.current_team_id == team_id, func.lower(Player.display_name) == func.lower(name_norm))
    ).all()
    if len(current_matches) == 1:
        return PlayerResolution(player=current_matches[0])
    if len(current_matches) > 1:
        return PlayerResolution(player=None, is_ambiguous=True)

    historical_ids = db.scalars(select(PlayerMatchStat.player_id).where(PlayerMatchStat.team_id == team_id).distinct()).all()
    if not historical_ids:
        return PlayerResolution(player=None)
    historical_matches = db.scalars(
        select(Player).where(Player.id.in_(historical_ids), func.lower(Player.display_name) == func.lower(name_norm))
    ).all()
    if len(historical_matches) == 1:
        return PlayerResolution(player=historical_matches[0])
    if len(historical_matches) > 1:
        return PlayerResolution(player=None, is_ambiguous=True)
    return PlayerResolution(player=None)


@dataclass(frozen=True)
class IngestionReport:
    created: list[int] = field(default_factory=list)
    updated: list[int] = field(default_factory=list)
    # (player_id, old_selection_status, new_selection_status)
    status_changed: list[tuple[int, str, str]] = field(default_factory=list)
    skipped_manual_override: list[int] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.created or self.updated)


def ingest_team_selections(
    db: Session,
    match_id: int,
    entries: list[SelectionEntry],
    source: str,
    source_timestamp: datetime | None = None,
    allow_override_manual: bool = False,
) -> IngestionReport:
    report = IngestionReport()
    now = datetime.now(timezone.utc)

    for entry in entries:
        if entry.player_id is not None:
            player = db.get(Player, entry.player_id)
            if player is None:
                report.unresolved.append(entry.player_name or f"player_id={entry.player_id}")
                continue
        elif entry.player_name:
            resolution = resolve_player_identity(db, entry.team_id, entry.player_name)
            if resolution.is_ambiguous:
                report.ambiguous.append(entry.player_name)
                continue
            if resolution.player is None:
                report.unresolved.append(entry.player_name)
                continue
            player = resolution.player
        else:
            continue  # neither player_id nor player_name given - nothing to resolve

        existing = db.scalar(select(ExpectedLineup).where(ExpectedLineup.match_id == match_id, ExpectedLineup.player_id == player.id))
        if existing is not None and existing.is_manual_override and not allow_override_manual:
            report.skipped_manual_override.append(player.id)
            continue

        old_selection_status = existing.selection_status if existing is not None else None
        if existing is None:
            existing = ExpectedLineup(match_id=match_id, player_id=player.id)
            db.add(existing)
            report.created.append(player.id)
        else:
            report.updated.append(player.id)

        existing.team_id = entry.team_id
        existing.selection_status = entry.selection_status
        existing.status = derive_coarse_status(entry.selection_status)
        existing.is_confirmed = entry.selection_status == SelectionStatus.CONFIRMED_SELECTED.value
        existing.recorded_at = now
        existing.source = source
        existing.source_timestamp = source_timestamp
        existing.source_reference = entry.source_reference
        existing.is_manual_override = False
        if entry.note is not None:
            existing.note = entry.note

        if old_selection_status is not None and old_selection_status != entry.selection_status:
            report.status_changed.append((player.id, old_selection_status, entry.selection_status))

    db.commit()
    return report


def derive_announcement_state(selection_statuses: list[str]) -> str:
    """Section 7: the UI-facing "how far along is selection for this
    match" signal, derived purely from what's actually stored — a
    placeholder roster existing must never read as more certain than
    "teams not announced"."""
    if any(s == SelectionStatus.CONFIRMED_SELECTED.value for s in selection_statuses):
        return ANNOUNCEMENT_FINAL_CONFIRMED
    if any(s == SelectionStatus.NAMED_IN_SQUAD.value for s in selection_statuses):
        return ANNOUNCEMENT_SQUAD_ANNOUNCED
    return ANNOUNCEMENT_NOT_ANNOUNCED


# --- Efficient manual fallback workflow (Section 13) ------------------------
#
# Since Section 1's audit found no reliable automated source, this is the
# PRIMARY way lineups get populated today: suggest a starting point (each
# team's most recent completed-match roster) for a human to quickly review
# and bulk-confirm/remove/add from, rather than typing 44+ names by hand.
# The suggestion is NEVER auto-applied and is always tagged PLACEHOLDER —
# only a human's explicit bulk-apply call writes real ExpectedLineup rows.


@dataclass(frozen=True)
class RosterSuggestion:
    player_id: int
    display_name: str
    last_match_id: int
    last_played_at: datetime


RECENT_MATCHES_WINDOW = 6
"""How many of the team's most recent completed matches count as "the
current squad" for suggestion purposes. A single most-recent match misses
anyone rested/omitted that week alone (e.g. a fit, first-choice player left
out for one round) even though they're clearly still selectable - an AFL
club rotates through roughly a 44-player list, not just the 22 who started
last week. Six matches (~6 weeks) is generous enough to catch that rotation
without reaching back so far it starts pulling in players who have since
been delisted or traded away."""


def suggest_roster_from_recent_match(db: Session, team_id: int) -> list[RosterSuggestion]:
    from app.models import Match, MatchStatus

    recent_match_ids = db.execute(
        select(Match.id)
        .join(PlayerMatchStat, PlayerMatchStat.match_id == Match.id)
        .where(PlayerMatchStat.team_id == team_id, Match.status == MatchStatus.COMPLETED)
        .group_by(Match.id)
        .order_by(Match.scheduled_start.desc())
        .limit(RECENT_MATCHES_WINDOW)
    ).scalars().all()
    if not recent_match_ids:
        return []

    rows = db.execute(
        select(PlayerMatchStat, Player, Match)
        .join(Player, Player.id == PlayerMatchStat.player_id)
        .join(Match, Match.id == PlayerMatchStat.match_id)
        .where(PlayerMatchStat.team_id == team_id, PlayerMatchStat.match_id.in_(recent_match_ids))
        .order_by(Match.scheduled_start.desc())
    ).all()

    best_by_player: dict[int, RosterSuggestion] = {}
    for _stat, p, m in rows:
        if p.id in best_by_player:
            continue  # rows are ordered most-recent-match-first; keep only that
        best_by_player[p.id] = RosterSuggestion(player_id=p.id, display_name=p.display_name, last_match_id=m.id, last_played_at=m.scheduled_start)
    return sorted(best_by_player.values(), key=lambda s: s.display_name)
