"""Writes prospective evidence — player availability, team rosters, depth
charts, game lineups — as append-only observations.

The one rule: never overwrite history when new information arrives.

How a recording step works, for every kind of evidence:

1. Compare what the source shows now with the latest stored observation
   for the same subject (by content hash).
2. If it differs — or nothing is stored yet — append a NEW observation
   stamped with the time the response was received. The old row is not
   touched; it stays as the record of what was shown before.
3. If it is identical, write no observation. Storing the same state on
   every poll would add rows without adding meaning.
4. Either way, append a poll row: "the source was read at this time".
   Together the two tables give each state its full temporal meaning:
   first seen at the observation's `observed_at`, last confirmed at the
   latest poll before the next observation.

Each call is one transaction. An interruption part-way through leaves
nothing behind, so re-running after a crash cannot duplicate or half-write.

Nothing is inferred. A player who drops out of the injury feed gets a
"no longer listed" row, not a healthy status. A status with no safe
canonical meaning is stored raw with the canonical value empty.

Provider-agnostic: consumes app/providers/nba/evidence_types.py records.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.prospective import ensure_utc
from app.models.nba import (
    NbaAvailabilityStatus,
    NbaEvidencePoll,
    NbaGame,
    NbaGameLineupObservation,
    NbaPlayer,
    NbaPlayerAvailabilityReport,
    NbaTeam,
    NbaTeamObservation,
)
from app.models.nba.evidence import POLL_AVAILABILITY, POLL_GAME_LINEUP, POLL_TEAM_DEPTH_CHART, POLL_TEAM_ROSTER, TEAM_OBSERVATION_DEPTH_CHART, TEAM_OBSERVATION_ROSTER
from app.nba.ingestion import teams_by_source_id
from app.providers.nba.evidence_types import NbaDepthChart, NbaGameLineup, NbaInjuryFeed, NbaInjuryItem, NbaTeamRoster


class EvidenceRejected(Exception):
    """A response was received but is not credible enough to record.
    Nothing is written: an append-only store cannot be cleaned up afterwards,
    so a doubtful response is better skipped and retried next cycle."""


def content_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


# Only a raw status that IS one of the league's own terms is given a
# canonical value. Anything else - "Day-To-Day" included - stays unmapped.
_CANONICAL_STATUSES = {status.value: status.value for status in NbaAvailabilityStatus}


def canonical_status(source_status: str | None) -> str | None:
    if not source_status:
        return None
    return _CANONICAL_STATUSES.get(source_status.strip().lower())


# --- availability ----------------------------------------------------------

# If the feed lists fewer than this fraction of the players it listed last
# time, it is treated as a broken response rather than as that many players
# recovering at once. Applied only once enough players are listed for the
# comparison to mean anything.
MIN_FEED_RETENTION = 0.5
MIN_LISTED_FOR_RETENTION_CHECK = 20


@dataclass
class AvailabilityRecordReport:
    items_seen: int = 0
    observations_added: int = 0
    unchanged: int = 0
    newly_listed: int = 0
    changed: int = 0
    delisted: int = 0
    players_created: int = 0
    unresolvable_items: int = 0
    duplicate_items: int = 0
    unknown_team_items: int = 0
    statuses: dict[str, int] = field(default_factory=dict)


def _latest_availability_by_player(db: Session, source: str) -> dict[int, NbaPlayerAvailabilityReport]:
    latest_ids = select(func.max(NbaPlayerAvailabilityReport.id)).where(NbaPlayerAvailabilityReport.source == source).group_by(NbaPlayerAvailabilityReport.player_id)
    rows = db.scalars(select(NbaPlayerAvailabilityReport).where(NbaPlayerAvailabilityReport.id.in_(latest_ids))).all()
    return {row.player_id: row for row in rows}


def _availability_row(item: NbaInjuryItem, player: NbaPlayer, team: NbaTeam | None, feed: NbaInjuryFeed, digest: str) -> NbaPlayerAvailabilityReport:
    return NbaPlayerAvailabilityReport(
        player_id=player.id,
        team_id=team.id if team else None,
        is_listed=True,
        source_status=item.source_status,
        source_status_type=item.source_status_type,
        status=canonical_status(item.source_status),
        injury_type=item.injury_type,
        injury_location=item.injury_location,
        injury_side=item.injury_side,
        injury_detail=item.injury_detail,
        fantasy_status=item.fantasy_status,
        expected_return_date=item.expected_return_date,
        short_comment=item.short_comment,
        long_comment=item.long_comment,
        source=feed.source,
        source_report_id=item.source_report_id,
        source_team_id=item.source_team_id,
        source_athlete_team_id=item.source_athlete_team_id,
        source_published_at=item.source_published_at,
        observed_at=feed.fetched_at,
        content_hash=digest,
        raw=item.raw,
    )


def record_availability(db: Session, feed: NbaInjuryFeed) -> AvailabilityRecordReport:
    """Record one read of the injury feed. Appends a row for each player
    whose entry is new or changed, and a "no longer listed" row for each
    player who was listed last time and is absent now."""
    report = AvailabilityRecordReport(items_seen=len(feed.items), unresolvable_items=feed.unresolvable_items)
    latest = _latest_availability_by_player(db, feed.source)
    listed_before = {player_id for player_id, row in latest.items() if row.is_listed}
    if len(listed_before) >= MIN_LISTED_FOR_RETENTION_CHECK and len(feed.items) < MIN_FEED_RETENTION * len(listed_before):
        raise EvidenceRejected(f"injury feed lists {len(feed.items)} players where {len(listed_before)} were listed at the last poll - treated as a broken response, not recorded")

    teams = teams_by_source_id(db, feed.source)
    players = {
        p.source_player_id: p
        for p in db.scalars(select(NbaPlayer).where(NbaPlayer.source == feed.source, NbaPlayer.source_player_id.in_([i.source_player_id for i in feed.items]))).all()
    }
    new_rows: list[NbaPlayerAvailabilityReport] = []
    listed_now: set[int] = set()
    for item in feed.items:
        report.statuses[item.source_status] = report.statuses.get(item.source_status, 0) + 1
        team = teams.get(item.source_team_id)
        if team is None:
            report.unknown_team_items += 1
        player = players.get(item.source_player_id)
        if player is None:
            # Identified by the source's own player id; a rookie or a player
            # who has not appeared in a stored box score is new to us.
            player = NbaPlayer(
                display_name=item.player_name, position=item.position, source=feed.source, source_player_id=item.source_player_id,
                current_team_id=team.id if team else None,
            )
            db.add(player)
            db.flush()
            players[item.source_player_id] = player
            report.players_created += 1
        if player.id in listed_now:
            report.duplicate_items += 1
            continue
        listed_now.add(player.id)

        digest = content_hash(item.raw)
        previous = latest.get(player.id)
        if previous is not None and previous.is_listed and previous.content_hash == digest:
            report.unchanged += 1
            continue
        if previous is None or not previous.is_listed:
            report.newly_listed += 1
        else:
            report.changed += 1
        new_rows.append(_availability_row(item, player, team, feed, digest))

    for player_id in listed_before - listed_now:
        previous = latest[player_id]
        report.delisted += 1
        new_rows.append(
            NbaPlayerAvailabilityReport(
                player_id=player_id, team_id=previous.team_id, is_listed=False, source_status=None, status=None, source=feed.source,
                source_team_id=previous.source_team_id, observed_at=feed.fetched_at,
            )
        )

    report.observations_added = len(new_rows)
    poll = NbaEvidencePoll(
        kind=POLL_AVAILABILITY, source=feed.source, observed_at=feed.fetched_at, source_timestamp=feed.source_timestamp,
        items_seen=len(feed.items), observations_added=len(new_rows), payload_sha256=feed.payload_sha256,
    )
    db.add(poll)
    db.flush()
    for row in new_rows:
        row.poll_id = poll.id
        db.add(row)
    db.commit()
    return report


# --- team roster and depth chart -------------------------------------------


def _record_team_observation(
    db: Session, *, team: NbaTeam, kind: str, poll_kind: str, source: str, source_team_id: str, payload: dict, observed_at: datetime, source_timestamp: datetime | None, items: int
) -> bool:
    """Append a team observation if it differs from the latest one; always
    append the poll. Returns whether a new observation was written."""
    digest = content_hash(payload)
    latest = db.scalar(
        select(NbaTeamObservation)
        .where(NbaTeamObservation.team_id == team.id, NbaTeamObservation.kind == kind, NbaTeamObservation.source == source)
        .order_by(NbaTeamObservation.id.desc())
        .limit(1)
    )
    changed = latest is None or latest.content_hash != digest
    poll = NbaEvidencePoll(
        kind=poll_kind, scope=source_team_id, source=source, observed_at=observed_at, source_timestamp=source_timestamp,
        items_seen=items, observations_added=int(changed), payload_sha256=digest,
    )
    db.add(poll)
    db.flush()
    if changed:
        db.add(
            NbaTeamObservation(
                poll_id=poll.id, team_id=team.id, kind=kind, source=source, observed_at=observed_at, source_timestamp=source_timestamp, payload=payload, content_hash=digest
            )
        )
    db.commit()
    return changed


def _team_for(db: Session, source: str, source_team_id: str) -> NbaTeam:
    team = teams_by_source_id(db, source).get(source_team_id)
    if team is None:
        raise EvidenceRejected(f"no stored NBA team has {source} id {source_team_id!r}")
    return team


def record_team_roster(db: Session, roster: NbaTeamRoster) -> bool:
    team = _team_for(db, roster.source, roster.source_team_id)
    players = sorted(
        ({"id": p.source_player_id, "name": p.name, "position": p.position, "jersey": p.jersey, "status": p.status} for p in roster.players), key=lambda p: p["id"]
    )
    return _record_team_observation(
        db, team=team, kind=TEAM_OBSERVATION_ROSTER, poll_kind=POLL_TEAM_ROSTER, source=roster.source, source_team_id=roster.source_team_id,
        payload={"players": players}, observed_at=roster.fetched_at, source_timestamp=roster.source_timestamp, items=len(players),
    )


def record_team_depth_chart(db: Session, chart: NbaDepthChart) -> bool:
    team = _team_for(db, chart.source, chart.source_team_id)
    return _record_team_observation(
        db, team=team, kind=TEAM_OBSERVATION_DEPTH_CHART, poll_kind=POLL_TEAM_DEPTH_CHART, source=chart.source, source_team_id=chart.source_team_id,
        payload={"positions": chart.positions}, observed_at=chart.fetched_at, source_timestamp=None, items=sum(len(v) for v in chart.positions.values()),
    )


# --- game lineup -----------------------------------------------------------


def lineup_scope(source_game_id: str, source_team_id: str) -> str:
    return f"{source_game_id}:{source_team_id}"


def record_game_lineup(db: Session, game: NbaGame, lineup: NbaGameLineup) -> bool:
    """Record what the source lists for one team in one game right now,
    together with how far from tip-off that is. Returns whether a new
    observation was written."""
    team = _team_for(db, lineup.source, lineup.source_team_id)
    if team.id not in (game.home_team_id, game.away_team_id):
        raise EvidenceRejected(f"team {lineup.source_team_id} is not playing in game {game.source_game_id}")
    entries = sorted(
        ({"id": e.source_player_id, "starter": e.starter, "active": e.active, "did_not_play": e.did_not_play, "reason": e.reason} for e in lineup.entries),
        key=lambda e: e["id"],
    )
    payload = {"entries": entries}
    # The source's flag is part of what was observed, so a flip of the flag
    # alone is recorded as a change.
    digest = content_hash({"entries": entries, "lineup_available": lineup.lineup_available, "has_starter_field": lineup.has_starter_field})
    latest = db.scalar(
        select(NbaGameLineupObservation)
        .where(NbaGameLineupObservation.game_id == game.id, NbaGameLineupObservation.team_id == team.id, NbaGameLineupObservation.source == lineup.source)
        .order_by(NbaGameLineupObservation.id.desc())
        .limit(1)
    )
    changed = latest is None or latest.content_hash != digest
    poll = NbaEvidencePoll(
        kind=POLL_GAME_LINEUP, scope=lineup_scope(lineup.source_game_id, lineup.source_team_id), source=lineup.source, observed_at=lineup.fetched_at,
        items_seen=len(entries), observations_added=int(changed), payload_sha256=digest,
    )
    db.add(poll)
    db.flush()
    if changed:
        db.add(
            NbaGameLineupObservation(
                poll_id=poll.id, game_id=game.id, team_id=team.id, source=lineup.source, observed_at=lineup.fetched_at,
                tipoff_at_observation=ensure_utc(game.scheduled_start), game_status_at_observation=game.status,
                lineup_available=lineup.lineup_available, has_starter_field=lineup.has_starter_field,
                starters_flagged=sum(1 for e in entries if e["starter"] is True), players_listed=len(entries), payload=payload, content_hash=digest,
            )
        )
    db.commit()
    return changed
