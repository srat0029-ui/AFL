"""Tests for player_identity.py (Sections 8-9, 20 of the live-operations
stage brief): safe creation of a genuine 2026 debutant with zero
fabricated history, the duplicate-name safety net, and alias CRUD."""

from app.models import Player, PlayerAlias, PlayerMatchStat, Sport, Team
from app.player_modelling.player_identity import (
    DuplicateNameWarning,
    NEW_PLAYER_SOURCE_PREFIX,
    add_player_alias,
    create_new_player,
    delete_player_alias,
    list_player_aliases,
)


def _seed_team(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    team = Team(sport_id=sport.id, name="Collingwood", short_name="COL")
    db.add(team)
    db.commit()
    return team


def test_create_new_player_has_zero_historical_games(db_session):
    team = _seed_team(db_session)

    player = create_new_player(db_session, display_name="Bailey Debutant", team_id=team.id)

    assert isinstance(player, Player)
    assert player.display_name == "Bailey Debutant"
    assert player.current_team_id == team.id
    assert player.source == NEW_PLAYER_SOURCE_PREFIX
    # No historical stats were fabricated - zero PlayerMatchStat rows exist
    # for this player, exactly as a genuine debutant should have.
    n_stats = db_session.query(PlayerMatchStat).filter_by(player_id=player.id).count()
    assert n_stats == 0


def test_create_new_player_source_player_id_is_unique_per_call(db_session):
    team = _seed_team(db_session)

    p1 = create_new_player(db_session, display_name="Player A", team_id=team.id)
    p2 = create_new_player(db_session, display_name="Player B", team_id=team.id)

    assert p1.source_player_id != p2.source_player_id


def test_duplicate_display_name_returns_warning_not_a_new_player(db_session):
    team = _seed_team(db_session)
    db_session.add(Player(sport_id=team.sport_id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=team.id))
    db_session.commit()

    result = create_new_player(db_session, display_name="Nick Daicos", team_id=team.id)

    assert isinstance(result, DuplicateNameWarning)
    assert result.existing_player_source == "afltables"
    # No second player was created.
    assert db_session.query(Player).filter_by(display_name="Nick Daicos").count() == 1


def test_force_bypasses_duplicate_name_check(db_session):
    team = _seed_team(db_session)
    db_session.add(Player(sport_id=team.sport_id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=team.id))
    db_session.commit()

    result = create_new_player(db_session, display_name="Nick Daicos", team_id=team.id, force=True)

    assert isinstance(result, Player)
    assert db_session.query(Player).filter_by(display_name="Nick Daicos").count() == 2


def test_create_new_player_raises_for_unknown_team(db_session):
    _seed_team(db_session)
    try:
        create_new_player(db_session, display_name="Someone", team_id=99999)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_add_and_list_player_alias(db_session):
    team = _seed_team(db_session)
    player = Player(sport_id=team.sport_id, display_name="Cam Rayner", source="afltables", source_player_id="p1", current_team_id=team.id)
    db_session.add(player)
    db_session.commit()

    alias = add_player_alias(db_session, player_id=player.id, alias_name="Cameron Rayner", source="the_odds_api", note="provider full name")

    assert isinstance(alias, PlayerAlias)
    aliases = list_player_aliases(db_session, player_id=player.id)
    assert len(aliases) == 1
    assert aliases[0].alias_name == "Cameron Rayner"


def test_delete_player_alias(db_session):
    team = _seed_team(db_session)
    player = Player(sport_id=team.sport_id, display_name="Cam Rayner", source="afltables", source_player_id="p1", current_team_id=team.id)
    db_session.add(player)
    db_session.commit()
    alias = add_player_alias(db_session, player_id=player.id, alias_name="Cameron Rayner")

    deleted = delete_player_alias(db_session, alias.id)
    deleted_again = delete_player_alias(db_session, alias.id)

    assert deleted is True
    assert deleted_again is False
    assert list_player_aliases(db_session, player_id=player.id) == []
