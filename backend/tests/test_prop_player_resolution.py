from datetime import datetime, timezone

from app.models import Match, MatchStatus, Player, Round, Season, Sport, Team
from app.player_modelling.prop_player_resolution import (
    RESOLUTION_AMBIGUOUS,
    RESOLUTION_EXACT,
    RESOLUTION_NORMALIZED_EXACT,
    RESOLUTION_SAFELY_RESOLVED,
    RESOLUTION_UNRESOLVED,
    resolve_prop_player,
)


def _seed(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name="Collingwood", short_name="COL")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=datetime(2026, 8, 20, tzinfo=timezone.utc), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.flush()
    daicos = Player(sport_id=sport.id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    db.add(daicos)
    db.commit()
    return match, home, away, daicos


def test_exact_match_on_home_team(db_session):
    match, home, away, daicos = _seed(db_session)
    resolution = resolve_prop_player(db_session, match, "Nick Daicos")
    assert resolution.tier == RESOLUTION_EXACT
    assert resolution.player.id == daicos.id
    assert resolution.team_id == home.id


def test_case_insensitive_exact_match(db_session):
    match, *_ = _seed(db_session)
    resolution = resolve_prop_player(db_session, match, "nick daicos")
    assert resolution.tier == RESOLUTION_EXACT


def test_punctuation_difference_resolves_via_normalized_exact(db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season = Season(sport_id=sport.id, year=2026)
    db_session.add(season)
    db_session.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name="Geelong", short_name="GEE")
    away = Team(sport_id=sport.id, name="Essendon", short_name="ESS")
    db_session.add_all([round_, home, away])
    db_session.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=datetime(2026, 8, 20, tzinfo=timezone.utc), status=MatchStatus.SCHEDULED,
    )
    db_session.add(match)
    db_session.flush()
    player = Player(sport_id=sport.id, display_name="Bailey O'Brien-Smith", source="afltables", source_player_id="p2", current_team_id=home.id)
    db_session.add(player)
    db_session.commit()

    resolution = resolve_prop_player(db_session, match, "Bailey OBrien Smith")
    assert resolution.tier == RESOLUTION_NORMALIZED_EXACT
    assert resolution.player.id == player.id


def test_initial_and_surname_resolves_via_safely_resolved(db_session):
    match, home, away, daicos = _seed(db_session)
    resolution = resolve_prop_player(db_session, match, "N. Daicos")
    assert resolution.tier == RESOLUTION_SAFELY_RESOLVED
    assert resolution.player.id == daicos.id


def test_initial_and_surname_ambiguous_when_two_players_share_surname_and_initial(db_session):
    match, home, away, daicos = _seed(db_session)
    # "Nathan Daicos" collides with "Nick Daicos" on both surname AND first
    # initial ("N") - a genuine ambiguity "N. Daicos" can't safely resolve.
    db_session.add(Player(sport_id=daicos.sport_id, display_name="Nathan Daicos", source="afltables", source_player_id="p3", current_team_id=home.id))
    db_session.commit()
    resolution = resolve_prop_player(db_session, match, "N. Daicos")
    assert resolution.tier == RESOLUTION_AMBIGUOUS


def test_initial_and_surname_not_ambiguous_when_initial_disambiguates(db_session):
    match, home, away, daicos = _seed(db_session)
    # "Josh Daicos" shares the surname but NOT the first initial - "N.
    # Daicos" should still resolve unambiguously to Nick.
    db_session.add(Player(sport_id=daicos.sport_id, display_name="Josh Daicos", source="afltables", source_player_id="p3", current_team_id=home.id))
    db_session.commit()
    resolution = resolve_prop_player(db_session, match, "N. Daicos")
    assert resolution.tier == RESOLUTION_SAFELY_RESOLVED
    assert resolution.player.id == daicos.id


def test_name_on_neither_team_is_unresolved(db_session):
    match, *_ = _seed(db_session)
    resolution = resolve_prop_player(db_session, match, "Someone Else")
    assert resolution.tier == RESOLUTION_UNRESOLVED
    assert resolution.player is None


def test_same_name_on_both_teams_is_ambiguous_never_guessed(db_session):
    match, home, away, daicos = _seed(db_session)
    db_session.add(Player(sport_id=daicos.sport_id, display_name="Nick Daicos", source="afltables", source_player_id="p4", current_team_id=away.id))
    db_session.commit()
    resolution = resolve_prop_player(db_session, match, "Nick Daicos")
    assert resolution.tier == RESOLUTION_AMBIGUOUS
    assert resolution.player is None
