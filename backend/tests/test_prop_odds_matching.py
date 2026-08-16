from datetime import datetime, timezone

from app.models import Match, MatchStatus, Round, Season, Sport, Team
from app.player_modelling.prop_odds_matching import get_or_create_bookmaker, resolve_event_to_match
from app.providers.types import ProviderEvent


def _seed_match(db, home_name="Collingwood", away_name="Carlton", kickoff=None):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name=home_name, short_name=home_name[:3].upper())
    away = Team(sport_id=sport.id, name=away_name, short_name=away_name[:3].upper())
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=kickoff or datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    return match, home, away


def _event(home_team, away_team, commence_time, event_id="evt1"):
    return ProviderEvent(
        provider="the_odds_api", event_id=event_id, sport_key="aussierules_afl",
        home_team=home_team, away_team=away_team, commence_time=commence_time,
    )


def test_exact_team_name_resolves(db_session):
    match, home, away = _seed_match(db_session)
    event = _event("Collingwood", "Carlton", match.scheduled_start)
    resolution = resolve_event_to_match(db_session, event)
    assert resolution.match is not None
    assert resolution.match.id == match.id


def test_aliased_team_name_resolves(db_session):
    match, home, away = _seed_match(db_session, home_name="Greater Western Sydney", away_name="Gold Coast")
    event = _event("GWS Giants", "Gold Coast Suns", match.scheduled_start)
    resolution = resolve_event_to_match(db_session, event)
    assert resolution.match is not None
    assert resolution.match.id == match.id


def test_unrecognised_team_name_is_unresolved_not_guessed(db_session):
    match, home, away = _seed_match(db_session)
    event = _event("Some Unknown Team", "Carlton", match.scheduled_start)
    resolution = resolve_event_to_match(db_session, event)
    assert resolution.match is None
    assert "unresolved team name" in resolution.reason


def test_no_match_near_commence_time_is_unresolved(db_session):
    match, home, away = _seed_match(db_session)
    far_future = datetime(2026, 12, 25, tzinfo=timezone.utc)
    event = _event("Collingwood", "Carlton", far_future)
    resolution = resolve_event_to_match(db_session, event)
    assert resolution.match is None
    assert "no Match found" in resolution.reason


def test_ambiguous_when_teams_play_twice_near_same_time(db_session):
    match1, home, away = _seed_match(db_session)
    sport_id = home.sport_id
    # a second match between the same two teams, close enough in time to
    # both fall within the resolution tolerance window
    from app.models import Round as RoundModel

    season_row = db_session.query(Season).filter_by(sport_id=sport_id).one()
    round2 = RoundModel(season_id=season_row.id, round_number=2)
    db_session.add(round2)
    db_session.flush()
    match2 = Match(
        sport_id=sport_id, season_id=season_row.id, round_id=round2.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=match1.scheduled_start, status=MatchStatus.SCHEDULED,
    )
    db_session.add(match2)
    db_session.commit()

    event = _event("Collingwood", "Carlton", match1.scheduled_start)
    resolution = resolve_event_to_match(db_session, event)
    assert resolution.match is None
    assert "ambiguous" in resolution.reason


def test_resolved_match_caches_provider_event_id(db_session):
    match, home, away = _seed_match(db_session)
    event = _event("Collingwood", "Carlton", match.scheduled_start, event_id="evt-xyz")
    resolution = resolve_event_to_match(db_session, event)
    assert resolution.match.external_ids == {"the_odds_api": "evt-xyz"}


def test_get_or_create_bookmaker_creates_new(db_session):
    bookmaker = get_or_create_bookmaker(db_session, "sportsbet", "Sportsbet", "au")
    assert bookmaker.id is not None
    assert bookmaker.name == "Sportsbet"
    assert bookmaker.provider_key == "sportsbet"
    assert bookmaker.region == "au"


def test_get_or_create_bookmaker_reuses_manual_entry_row(db_session):
    from app.models import Bookmaker

    manual = Bookmaker(name="TAB")  # as if created via manual prop entry, no provider_key
    db_session.add(manual)
    db_session.commit()

    bookmaker = get_or_create_bookmaker(db_session, "tab", "TAB", "au")
    assert bookmaker.id == manual.id  # same row, not a duplicate
    assert bookmaker.provider_key == "tab"  # enriched in place
