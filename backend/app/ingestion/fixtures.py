"""Idempotent ingestion of Fixture DTOs into the core schema.

Get-or-create for Sport/Team/Venue/Season/Round; upsert for Match, keyed by
the provider's external id (stored in Match.external_ids). Safe to re-run:
matches that already exist are updated in place (e.g. a completed score
overwrites a scheduled placeholder) rather than duplicated.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.team_metadata import get_team_metadata
from app.models import Match, MatchStatus, Round, Season, Sport, Team, Venue
from app.providers.types import Fixture

# Key under which a provider's own id is stored in Match.external_ids, e.g.
# {"squiggle": "35704"}. A second fixture provider would use its own key.
SQUIGGLE_KEY = "squiggle"

_STATUS_MAP = {
    "scheduled": MatchStatus.SCHEDULED,
    "in_progress": MatchStatus.IN_PROGRESS,
    "completed": MatchStatus.COMPLETED,
    "postponed": MatchStatus.POSTPONED,
    "cancelled": MatchStatus.CANCELLED,
}


@dataclass
class IngestionResult:
    matches_created: int = 0
    matches_updated: int = 0
    matches_unchanged: int = 0
    teams_created: int = 0
    venues_created: int = 0
    seasons_created: int = 0
    rounds_created: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def matches_seen(self) -> int:
        return self.matches_created + self.matches_updated + self.matches_unchanged


def ingest_fixtures(db: Session, fixtures: list[Fixture], provider_key: str = SQUIGGLE_KEY) -> IngestionResult:
    result = IngestionResult()
    if not fixtures:
        return result

    sport_cache: dict[str, Sport] = {}
    team_cache: dict[tuple[int, str], Team] = {}
    venue_cache: dict[str, Venue] = {}
    season_cache: dict[tuple[int, int], Season] = {}
    round_cache: dict[tuple[int, int], Round] = {}
    match_index_cache: dict[int, dict[str, Match]] = {}  # season_id -> {external_id: Match}

    for fixture in fixtures:
        sport = _get_or_create_sport(db, sport_cache, fixture.sport_code)
        season = _get_or_create_season(db, season_cache, sport, fixture.season_year, result)
        round_ = _get_or_create_round(db, round_cache, season, fixture.round_number, fixture.round_name, result)
        home_team = _get_or_create_team(db, team_cache, sport, fixture.home_team, result)
        away_team = _get_or_create_team(db, team_cache, sport, fixture.away_team, result)
        venue = _get_or_create_venue(db, venue_cache, fixture.venue_name, result) if fixture.venue_name else None

        if season.id not in match_index_cache:
            match_index_cache[season.id] = _index_existing_matches(db, season.id, provider_key)
        existing = match_index_cache[season.id].get(fixture.external_id)

        status = _STATUS_MAP.get(fixture.status, MatchStatus.SCHEDULED)

        if existing is None:
            match = Match(
                sport_id=sport.id,
                season_id=season.id,
                round_id=round_.id,
                home_team_id=home_team.id,
                away_team_id=away_team.id,
                venue_id=venue.id if venue else None,
                scheduled_start=fixture.scheduled_start,
                status=status,
                home_score=fixture.home_score,
                away_score=fixture.away_score,
                home_score_by_quarter=fixture.home_score_by_quarter,
                away_score_by_quarter=fixture.away_score_by_quarter,
                external_ids={provider_key: fixture.external_id},
            )
            db.add(match)
            db.flush()
            match_index_cache[season.id][fixture.external_id] = match
            result.matches_created += 1
        else:
            changed = _apply_fixture_to_match(existing, fixture, status, round_, venue)
            if changed:
                result.matches_updated += 1
            else:
                result.matches_unchanged += 1

    db.commit()
    return result


def _get_or_create_sport(db: Session, cache: dict[str, Sport], code: str) -> Sport:
    if code in cache:
        return cache[code]
    sport = db.scalar(select(Sport).where(Sport.code == code))
    if sport is None:
        name = "Australian Football League" if code == "AFL" else code
        sport = Sport(code=code, name=name)
        db.add(sport)
        db.flush()
    cache[code] = sport
    return sport


def _get_or_create_team(
    db: Session, cache: dict[tuple[int, str], Team], sport: Sport, name: str, result: IngestionResult
) -> Team:
    key = (sport.id, name)
    if key in cache:
        return cache[key]
    team = db.scalar(select(Team).where(Team.sport_id == sport.id, Team.name == name))
    if team is None:
        abbrev, primary, secondary = get_team_metadata(name)
        team = Team(sport_id=sport.id, name=name, short_name=abbrev, primary_colour=primary, secondary_colour=secondary)
        db.add(team)
        db.flush()
        result.teams_created += 1
    cache[key] = team
    return team


def _get_or_create_venue(
    db: Session, cache: dict[str, Venue], name: str, result: IngestionResult
) -> Venue:
    if name in cache:
        return cache[name]
    venue = db.scalar(select(Venue).where(Venue.name == name))
    if venue is None:
        venue = Venue(name=name)
        db.add(venue)
        db.flush()
        result.venues_created += 1
    cache[name] = venue
    return venue


def _get_or_create_season(
    db: Session, cache: dict[tuple[int, int], Season], sport: Sport, year: int, result: IngestionResult
) -> Season:
    key = (sport.id, year)
    if key in cache:
        return cache[key]
    season = db.scalar(select(Season).where(Season.sport_id == sport.id, Season.year == year))
    if season is None:
        season = Season(sport_id=sport.id, year=year)
        db.add(season)
        db.flush()
        result.seasons_created += 1
    cache[key] = season
    return season


def _get_or_create_round(
    db: Session,
    cache: dict[tuple[int, int], Round],
    season: Season,
    round_number: int,
    round_name: str | None,
    result: IngestionResult,
) -> Round:
    key = (season.id, round_number)
    if key in cache:
        return cache[key]
    round_ = db.scalar(select(Round).where(Round.season_id == season.id, Round.round_number == round_number))
    if round_ is None:
        round_ = Round(season_id=season.id, round_number=round_number, name=round_name)
        db.add(round_)
        db.flush()
        result.rounds_created += 1
    cache[key] = round_
    return round_


def _index_existing_matches(db: Session, season_id: int, provider_key: str) -> dict[str, Match]:
    matches = db.scalars(select(Match).where(Match.season_id == season_id)).all()
    index: dict[str, Match] = {}
    for match in matches:
        ext_id = (match.external_ids or {}).get(provider_key)
        if ext_id is not None:
            index[str(ext_id)] = match
    return index


def _normalize_dt(dt: datetime) -> datetime:
    """SQLite doesn't preserve tzinfo across a round trip, so a value just
    read back from the DB may come back naive even though we always store
    UTC. Normalize both sides to naive-UTC before comparing so a match isn't
    spuriously reported as "changed" on every re-run."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _apply_fixture_to_match(
    match: Match, fixture: Fixture, status: MatchStatus, round_: Round, venue: Venue | None
) -> bool:
    changed = False

    if match.status != status:
        match.status = status
        changed = True
    if match.home_score != fixture.home_score:
        match.home_score = fixture.home_score
        changed = True
    if match.away_score != fixture.away_score:
        match.away_score = fixture.away_score
        changed = True
    if match.home_score_by_quarter != fixture.home_score_by_quarter:
        match.home_score_by_quarter = fixture.home_score_by_quarter
        changed = True
    if match.away_score_by_quarter != fixture.away_score_by_quarter:
        match.away_score_by_quarter = fixture.away_score_by_quarter
        changed = True
    if _normalize_dt(match.scheduled_start) != _normalize_dt(fixture.scheduled_start):
        match.scheduled_start = fixture.scheduled_start
        changed = True
    if match.round_id != round_.id:
        match.round_id = round_.id
        changed = True

    new_venue_id = venue.id if venue else None
    if match.venue_id != new_venue_id:
        match.venue_id = new_venue_id
        changed = True

    return changed
