"""Targeted tests for the Finals Market Readiness + Auto-Population stage's
provisional-roster auto-population (items 1-2): a player with a resolved,
fresh bookmaker market for the exact match and valid current-team/current-
season evidence gets a PLACEHOLDER ExpectedLineup row even with no official
lineup, but never overwrites an existing row and never invents anyone."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import (
    Bookmaker, ExpectedLineup, Match, MatchStatus, Player, PlayerMatchStat, PlayerPropMarket, Round, Season, Sport, Team,
)
from app.models.expected_lineup import SelectionStatus
from app.player_modelling.market import PlayerMarket
from app.player_modelling.provisional_roster import populate_provisional_roster

NOW = datetime.now(timezone.utc)


def _seed_match(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=25, name="Wildcard Finals")
    home = Team(sport_id=sport.id, name="Collingwood", short_name="COL")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW + timedelta(days=2), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    return match, home, away, season


def _seed_player(db, team, *, name="Test Player"):
    player = Player(sport_id=team.sport_id, display_name=name, source="afltables", source_player_id=name, current_team_id=team.id)
    db.add(player)
    db.commit()
    return player


def _seed_current_season_stat(db, player, team, season):
    round_ = Round(season_id=season.id, round_number=20)
    opponent = Team(sport_id=team.sport_id, name=f"Opponent of {team.name}", short_name="OPP")
    db.add_all([round_, opponent])
    db.flush()
    other_match = Match(
        sport_id=team.sport_id, season_id=season.id, round_id=round_.id, home_team_id=team.id, away_team_id=opponent.id,
        scheduled_start=NOW - timedelta(days=30), status=MatchStatus.COMPLETED,
    )
    db.add(other_match)
    db.flush()
    db.add(PlayerMatchStat(player_id=player.id, match_id=other_match.id, team_id=team.id, source="afltables", recorded_at=NOW - timedelta(days=30), disposals=20, goals=1))
    db.commit()


def _seed_prop_quote(db, match, player, *, recorded_at=NOW):
    bookmaker = Bookmaker(name="SportsBet")
    db.add(bookmaker)
    db.flush()
    quote = PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
        line_type="over_under", threshold=20.5, selection="over", price_decimal=1.9, recorded_at=recorded_at, source="the_odds_api",
    )
    db.add(quote)
    db.commit()
    return quote


def test_placeholder_created_for_fresh_resolved_prop_with_current_season_evidence(db_session):
    match, home, away, season = _seed_match(db_session)
    player = _seed_player(db_session, home)
    _seed_current_season_stat(db_session, player, home, season)
    _seed_prop_quote(db_session, match, player)

    report = populate_provisional_roster(db_session, match.id, now=NOW)
    assert report.players_added == 1

    row = db_session.scalar(select(ExpectedLineup).where(ExpectedLineup.match_id == match.id, ExpectedLineup.player_id == player.id))
    assert row is not None
    assert row.selection_status == SelectionStatus.PLACEHOLDER.value
    assert row.is_confirmed is False
    assert row.status == "uncertain"


def test_no_placeholder_when_prop_market_is_stale(db_session):
    match, home, away, season = _seed_match(db_session)
    player = _seed_player(db_session, home)
    _seed_current_season_stat(db_session, player, home, season)
    _seed_prop_quote(db_session, match, player, recorded_at=NOW - timedelta(days=5))

    report = populate_provisional_roster(db_session, match.id, now=NOW)
    assert report.players_added == 0
    assert db_session.scalar(select(ExpectedLineup).where(ExpectedLineup.match_id == match.id)) is None


def test_no_placeholder_without_current_season_evidence(db_session):
    """A resolved, fresh prop market alone is not enough - item 2 also
    requires valid current-team/current-season evidence."""
    match, home, away, season = _seed_match(db_session)
    player = _seed_player(db_session, home)
    _seed_prop_quote(db_session, match, player)  # no PlayerMatchStat seeded this season

    report = populate_provisional_roster(db_session, match.id, now=NOW)
    assert report.players_added == 0


def test_never_overwrites_an_existing_expected_lineup_row(db_session):
    match, home, away, season = _seed_match(db_session)
    player = _seed_player(db_session, home)
    _seed_current_season_stat(db_session, player, home, season)
    _seed_prop_quote(db_session, match, player)
    db_session.add(ExpectedLineup(
        match_id=match.id, player_id=player.id, team_id=home.id, status="expected_in",
        selection_status="confirmed_selected", is_confirmed=True, recorded_at=NOW, source="manual",
    ))
    db_session.commit()

    report = populate_provisional_roster(db_session, match.id, now=NOW)
    assert report.players_added == 0
    row = db_session.scalar(select(ExpectedLineup).where(ExpectedLineup.match_id == match.id, ExpectedLineup.player_id == player.id))
    assert row.selection_status == "confirmed_selected"  # untouched


def test_never_invents_a_player_with_no_prop_market_at_all(db_session):
    match, home, away, season = _seed_match(db_session)
    player = _seed_player(db_session, home)
    _seed_current_season_stat(db_session, player, home, season)
    # No PlayerPropMarket row for this player at all.

    report = populate_provisional_roster(db_session, match.id, now=NOW)
    assert report.players_added == 0
    assert report.players_considered == 0
