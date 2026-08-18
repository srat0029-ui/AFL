"""Current-context ingestion, history, and freshness/supersession
(Current Context + Team News Intelligence stage, Sections 1-3, 11-12).

Manual entry is the primary write path today (Section 2's own audit
conclusion, carried forward from the team-selection stage's precedent in
team_selection_ingestion.py: no reliable structured public feed for AFL
team news/injuries currently exists — see this stage's source-research
notes). `add_context_item` is intentionally source-agnostic: it accepts
whatever `source`/`confidence` a caller supplies, so a future automated
provider can call the exact same function a human's manual-entry form
calls today, without this module changing.

Supersession (Section 12) is derived, not stored — see match_context.py's
module docstring for why. `_subject_key` decides what counts as "the same
evolving situation": a player-specific item groups by (match, player)
regardless of context_type (so Monday's "limited game-time concern" and
Thursday's "confirmed out" about the same player are correctly treated as
one evolving story, matching the brief's own TEST -> CONFIRMED_OUT worked
example); a team-wide item (no player) groups by (match, team); a
match-wide item (no team, no player — weather/venue notes) groups by
(match, context_type) so an unrelated venue note doesn't supersede a
rain-probability note.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CONTEXT_CONFIDENCE_LABELS, CONTEXT_TYPE_LABELS, MatchContextItem

FRESHNESS_FRESH = "fresh"
FRESHNESS_AGING = "aging"
FRESHNESS_STALE = "stale"

# A team-news item is only useful for a short window before it should be
# treated with more caution — these are deliberately short relative to a
# typical AFL week (selections are usually only truly final 1-2 days out),
# not a generic "how old is old" heuristic.
_FRESH_WITHIN = timedelta(hours=24)
_AGING_WITHIN = timedelta(hours=72)


def _effective_timestamp(item: MatchContextItem) -> datetime:
    return item.source_timestamp if item.source_timestamp is not None else item.recorded_at


def context_freshness(item: MatchContextItem, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    ts = _effective_timestamp(item)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = now - ts
    if age <= _FRESH_WITHIN:
        return FRESHNESS_FRESH
    if age <= _AGING_WITHIN:
        return FRESHNESS_AGING
    return FRESHNESS_STALE


def _subject_key(item: MatchContextItem) -> tuple:
    if item.player_id is not None:
        return ("player", item.match_id, item.player_id)
    if item.team_id is not None:
        return ("team", item.match_id, item.team_id)
    return ("match", item.match_id, item.context_type)


def add_context_item(
    db: Session,
    *,
    match_id: int,
    context_type: str,
    source: str,
    summary: str,
    confidence: str,
    team_id: int | None = None,
    player_id: int | None = None,
    source_timestamp: datetime | None = None,
    source_reference: str | None = None,
) -> MatchContextItem:
    item = MatchContextItem(
        match_id=match_id,
        team_id=team_id,
        player_id=player_id,
        context_type=context_type,
        confidence=confidence,
        source=source,
        source_reference=source_reference,
        source_timestamp=source_timestamp,
        recorded_at=datetime.now(timezone.utc),
        summary=summary,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def list_context_for_match(db: Session, match_id: int) -> list[MatchContextItem]:
    """Full history, newest source/recorded timestamp first — nothing is
    ever hidden, only the "current state" view (below) collapses it."""
    items = db.scalars(select(MatchContextItem).where(MatchContextItem.match_id == match_id)).all()
    return sorted(items, key=_effective_timestamp, reverse=True)


def current_context_for_match(db: Session, match_id: int) -> list[MatchContextItem]:
    """The latest authoritative item per subject (Section 12) — what a
    compact match panel should show, as opposed to the full history."""
    all_items = list_context_for_match(db, match_id)
    latest_by_subject: dict[tuple, MatchContextItem] = {}
    for item in all_items:  # already newest-first
        key = _subject_key(item)
        if key not in latest_by_subject:
            latest_by_subject[key] = item
    return list(latest_by_subject.values())


def context_for_player(db: Session, match_id: int, player_id: int) -> list[MatchContextItem]:
    """Full history for one player in one match (Section 10 player-context
    panel) — newest first."""
    items = db.scalars(
        select(MatchContextItem).where(MatchContextItem.match_id == match_id, MatchContextItem.player_id == player_id)
    ).all()
    return sorted(items, key=_effective_timestamp, reverse=True)


@dataclass(frozen=True)
class ContextGroup:
    """One subject's current state plus its full history — used by the
    match context panel and the manual-entry UI to show "here's what's
    superseded this" without a second query shape."""

    current: MatchContextItem
    history: list[MatchContextItem]  # includes `current`, newest first


def context_groups_for_match(db: Session, match_id: int) -> list[ContextGroup]:
    all_items = list_context_for_match(db, match_id)
    by_subject: dict[tuple, list[MatchContextItem]] = {}
    for item in all_items:
        by_subject.setdefault(_subject_key(item), []).append(item)
    return [ContextGroup(current=items[0], history=items) for items in by_subject.values()]


def context_item_as_dict(item: MatchContextItem, *, is_current: bool = True, now: datetime | None = None) -> dict:
    return {
        "id": item.id,
        "match_id": item.match_id,
        "team_id": item.team_id,
        "player_id": item.player_id,
        "player_name": item.player.display_name if item.player is not None else None,
        "context_type": item.context_type,
        "context_type_label": CONTEXT_TYPE_LABELS.get(item.context_type, item.context_type),
        "confidence": item.confidence,
        "confidence_label": CONTEXT_CONFIDENCE_LABELS.get(item.confidence, item.confidence),
        "source": item.source,
        "source_reference": item.source_reference,
        "source_timestamp": item.source_timestamp,
        "recorded_at": item.recorded_at,
        "summary": item.summary,
        "freshness": context_freshness(item, now=now),
        "is_current": is_current,
    }
