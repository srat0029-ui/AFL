"""Tests for the read-only data-freshness summary (product-polish stage):
category coverage, Fresh/Aging/Stale/Not available bucketing, and the
exact "waiting for bookmaker markets" / "teams not yet announced" wording
required for those two not-available cases."""

from datetime import datetime, timedelta, timezone

from app.models import Bookmaker, ExpectedLineup, Match, Player, PlayerPropMarket, Round, SelectionStatus, Season, Sport, Team, derive_coarse_status
from app.player_modelling.data_freshness import FRESH, NOT_AVAILABLE, WAITING_FOR_BOOKMAKER_MARKETS, load_data_freshness

NOW = datetime.now(timezone.utc)


def _seed_match(db, *, kickoff=None):
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
    from app.models import MatchStatus

    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=kickoff or (NOW + timedelta(days=3)), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    return match, home, away


def test_no_upcoming_matches_reports_not_available_for_every_category(db_session):
    report = load_data_freshness(db_session)
    categories = {item.category for item in report.items}
    assert categories == {"fixtures", "team_odds", "player_props", "weather", "lineup_status", "projections"}
    assert all(item.status == NOT_AVAILABLE for item in report.items)


def test_player_props_not_available_says_waiting_for_bookmaker_markets(db_session):
    _seed_match(db_session)
    report = load_data_freshness(db_session)
    props = next(i for i in report.items if i.category == "player_props")
    assert props.status == NOT_AVAILABLE
    assert props.detail == WAITING_FOR_BOOKMAKER_MARKETS

    team_odds = next(i for i in report.items if i.category == "team_odds")
    assert team_odds.status == NOT_AVAILABLE
    assert team_odds.detail == WAITING_FOR_BOOKMAKER_MARKETS


def test_lineup_status_says_teams_not_yet_announced(db_session):
    _seed_match(db_session)
    report = load_data_freshness(db_session)
    lineup = next(i for i in report.items if i.category == "lineup_status")
    assert lineup.status == NOT_AVAILABLE
    assert "not yet announced" in lineup.detail.lower()


def test_lineup_status_fresh_when_all_teams_confirmed(db_session):
    match, home, away = _seed_match(db_session)
    player = Player(sport_id=match.sport_id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    db_session.add(player)
    db_session.flush()
    db_session.add(ExpectedLineup(
        match_id=match.id, player_id=player.id, team_id=home.id,
        status=derive_coarse_status(SelectionStatus.CONFIRMED_SELECTED.value), selection_status=SelectionStatus.CONFIRMED_SELECTED.value,
        is_confirmed=True, recorded_at=NOW, source="manual",
    ))
    db_session.commit()

    report = load_data_freshness(db_session)
    lineup = next(i for i in report.items if i.category == "lineup_status")
    assert lineup.status == FRESH
    assert lineup.detail == "All upcoming matches have confirmed teams."


def test_player_props_fresh_when_recently_refreshed(db_session):
    match, home, away = _seed_match(db_session)
    player = Player(sport_id=match.sport_id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    bookmaker = Bookmaker(name="SportsBet")
    db_session.add_all([player, bookmaker])
    db_session.flush()
    db_session.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type="player_disposals",
        line_type="over_under", threshold=29.5, selection="over", price_decimal=1.9,
        recorded_at=NOW, source="the_odds_api",
    ))
    db_session.commit()

    report = load_data_freshness(db_session)
    props = next(i for i in report.items if i.category == "player_props")
    assert props.status == FRESH
    assert props.last_refreshed is not None
