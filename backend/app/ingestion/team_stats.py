"""Resolves TeamStatLine rows (from AFLTablesStatsProvider) against
already-ingested Match rows and upserts TeamMatchStat.

There's no shared id scheme between AFL Tables and Squiggle (our fixture
source), so resolution is by natural key: (season, the two team names, date
within a small tolerance) — see the module docstring in
app/providers/afl/afltables.py. A team never plays the same opponent twice
within a few days in a real AFL season, so this is unambiguous even though a
team pair can appear twice in one season (home leg and away leg, months
apart) — matches_by_pair below can hold multiple candidates per pair; the
date is what picks the right one.
"""

from dataclasses import dataclass, field
from datetime import date as date_type

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, Season, Sport, Team, TeamMatchStat
from app.providers.types import TeamStatLine

SOURCE_NAME = "afltables"
_DATE_TOLERANCE_DAYS = 1

# Order matches app/providers/afl/afltables.py's _TABLE1_FIELDS + _TABLE2_FIELDS.
STAT_FIELDS = [
    "kicks", "marks", "handballs", "disposals", "goals", "behinds", "hitouts", "tackles",
    "rebound_50s", "inside_50s", "clearances", "clangers", "frees_for", "frees_against",
    "brownlow_votes", "contested_possessions", "uncontested_possessions", "contested_marks",
    "marks_inside_50", "one_percenters", "bounces", "goal_assists",
]


@dataclass
class TeamStatsIngestionResult:
    rows_seen: int = 0
    stats_created: int = 0
    stats_updated: int = 0
    stats_unchanged: int = 0
    unmatched: list[str] = field(default_factory=list)

    @property
    def matched(self) -> int:
        return self.stats_created + self.stats_updated + self.stats_unchanged


def ingest_team_stats(
    db: Session, rows: list[TeamStatLine], season_year: int, source: str = SOURCE_NAME
) -> TeamStatsIngestionResult:
    result = TeamStatsIngestionResult()
    if not rows:
        return result

    sport = db.scalar(select(Sport).where(Sport.code == "AFL"))
    if sport is None:
        result.unmatched.append("no AFL sport row found — has fixture ingestion run?")
        return result

    season = db.scalar(select(Season).where(Season.sport_id == sport.id, Season.year == season_year))
    if season is None:
        result.unmatched.append(f"no season {season_year} found — run fixture ingestion for this year first")
        return result

    teams_by_name = {t.name: t for t in db.scalars(select(Team).where(Team.sport_id == sport.id)).all()}
    season_matches = db.scalars(select(Match).where(Match.season_id == season.id)).all()

    matches_by_pair: dict[frozenset[str], list[Match]] = {}
    for m in season_matches:
        key = frozenset({m.home_team.name, m.away_team.name})
        matches_by_pair.setdefault(key, []).append(m)

    for row in rows:
        result.rows_seen += 1
        team = teams_by_name.get(row.team_name)
        opponent = teams_by_name.get(row.opponent_name) if row.opponent_name else None
        if team is None or opponent is None:
            result.unmatched.append(
                f"unknown team(s): {row.team_name!r} vs {row.opponent_name!r} on {row.match_date}"
            )
            continue

        candidates = matches_by_pair.get(frozenset({row.team_name, row.opponent_name}), [])
        match = _resolve_by_date(candidates, row.match_date)
        if match is None:
            result.unmatched.append(f"no match found: {row.team_name} vs {row.opponent_name} on {row.match_date}")
            continue

        outcome = _upsert_stat(db, match, team, opponent, row, source)
        if outcome == "created":
            result.stats_created += 1
        elif outcome == "updated":
            result.stats_updated += 1
        else:
            result.stats_unchanged += 1

    db.commit()
    return result


def _resolve_by_date(candidates: list[Match], target_date: date_type | None) -> Match | None:
    if not candidates:
        return None
    if target_date is None:
        return candidates[0] if len(candidates) == 1 else None

    best, best_diff = None, None
    for m in candidates:
        diff = abs((m.scheduled_start.date() - target_date).days)
        if diff <= _DATE_TOLERANCE_DAYS and (best_diff is None or diff < best_diff):
            best, best_diff = m, diff
    return best


def _upsert_stat(db: Session, match: Match, team: Team, opponent: Team, row: TeamStatLine, source: str) -> str:
    existing = db.scalar(
        select(TeamMatchStat).where(
            TeamMatchStat.match_id == match.id,
            TeamMatchStat.team_id == team.id,
            TeamMatchStat.source == source,
        )
    )
    external_ids = {"afltables_game_url": row.match_external_id}

    if existing is None:
        stat = TeamMatchStat(
            match_id=match.id,
            team_id=team.id,
            opponent_team_id=opponent.id,
            source=source,
            recorded_at=row.recorded_at,
            external_ids=external_ids,
            **{field_name: row.stats.get(field_name) for field_name in STAT_FIELDS},
        )
        db.add(stat)
        db.flush()
        return "created"

    changed = False
    for field_name in STAT_FIELDS:
        new_value = row.stats.get(field_name)
        if getattr(existing, field_name) != new_value:
            setattr(existing, field_name, new_value)
            changed = True
    if existing.opponent_team_id != opponent.id:
        existing.opponent_team_id = opponent.id
        changed = True
    if existing.external_ids != external_ids:
        existing.external_ids = external_ids
        changed = True
    if changed:
        existing.recorded_at = row.recorded_at
        return "updated"
    return "unchanged"
