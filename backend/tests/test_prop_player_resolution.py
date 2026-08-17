from datetime import datetime, timezone

from app.models import Match, MatchStatus, Player, PlayerAlias, PlayerMatchStat, Round, Season, Sport, Team
from app.player_modelling.prop_player_resolution import (
    RESOLUTION_ALIAS,
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


def test_player_traded_between_the_two_teams_facing_each_other_is_not_ambiguous(db_session):
    """Real bug found via a live API run: a player who moved from one team
    to the other resolves via the CURRENT roster on their new team AND via
    historical PlayerMatchStat on their old team - both lookups correctly
    return the SAME real Player, which must resolve unambiguously to that
    player on their current team, not be flagged as two candidates."""
    match, home, away, daicos = _seed(db_session)
    traded_player = Player(sport_id=daicos.sport_id, display_name="Traded Player", source="afltables", source_player_id="p5", current_team_id=away.id)
    db_session.add(traded_player)
    db_session.flush()
    # historical stats show them having played for the HOME team before the trade
    db_session.add(PlayerMatchStat(player_id=traded_player.id, match_id=match.id, team_id=home.id, opponent_team_id=away.id, source="afltables", recorded_at=match.scheduled_start, disposals=15))
    db_session.commit()

    resolution = resolve_prop_player(db_session, match, "Traded Player")
    assert resolution.tier == RESOLUTION_EXACT
    assert resolution.player.id == traded_player.id
    assert resolution.team_id == away.id  # their current team, not the historical one


# --- Alias resolution (live-operations stage, Sections 9, 20) --------------


def test_alias_resolves_ahead_of_other_tiers(db_session):
    """An explicit alias is tried FIRST - even a string that would also
    match some OTHER player via a weaker tier must resolve to the aliased
    player, since a human explicitly reviewed and confirmed this mapping."""
    match, home, away, daicos = _seed(db_session)
    db_session.add(PlayerAlias(player_id=daicos.id, alias_name="The Wizard", source=None))
    db_session.commit()

    resolution = resolve_prop_player(db_session, match, "The Wizard")

    assert resolution.tier == RESOLUTION_ALIAS
    assert resolution.player.id == daicos.id


def test_alias_scoped_to_source_when_source_specified(db_session):
    match, home, away, daicos = _seed(db_session)
    db_session.add(PlayerAlias(player_id=daicos.id, alias_name="N. Wizard", source="the_odds_api"))
    db_session.commit()

    matched = resolve_prop_player(db_session, match, "N. Wizard", source="the_odds_api")
    assert matched.tier == RESOLUTION_ALIAS
    assert matched.player.id == daicos.id


def test_source_agnostic_alias_applies_regardless_of_provider(db_session):
    match, home, away, daicos = _seed(db_session)
    db_session.add(PlayerAlias(player_id=daicos.id, alias_name="Common Nickname", source=None))
    db_session.commit()

    matched = resolve_prop_player(db_session, match, "Common Nickname", source="some_other_provider")
    assert matched.tier == RESOLUTION_ALIAS
    assert matched.player.id == daicos.id


def test_alias_for_player_not_on_either_team_falls_through(db_session):
    """A stale alias pointing at a player no longer on either of this
    match's two teams must not force a false positive - it simply doesn't
    match and resolution falls through to the next tier (unresolved here,
    since the alias text itself isn't a real name)."""
    match, home, away, daicos = _seed(db_session)
    other_team = Team(sport_id=daicos.sport_id, name="Richmond", short_name="RIC")
    db_session.add(other_team)
    db_session.flush()
    elsewhere_player = Player(sport_id=daicos.sport_id, display_name="Elsewhere Player", source="afltables", source_player_id="pX", current_team_id=other_team.id)
    db_session.add(elsewhere_player)
    db_session.flush()
    db_session.add(PlayerAlias(player_id=elsewhere_player.id, alias_name="Stale Alias Text", source=None))
    db_session.commit()

    resolution = resolve_prop_player(db_session, match, "Stale Alias Text")
    assert resolution.tier == RESOLUTION_UNRESOLVED
