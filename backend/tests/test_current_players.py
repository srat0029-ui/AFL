"""Tests for the current-player data-scoping helper (product-quality
stage): distinguishes the current-facing display population from the full
historical evaluation population. See app/player_modelling/current_players.py."""

from datetime import datetime, timezone

from app.models import ExpectedLineup, Match, MatchStatus, Player, PlayerDisposalProjection, PlayerMatchStat, Round, Season, Sport, Team
from app.player_modelling.current_players import current_player_ids, current_season_year, is_current_player

CURRENT_YEAR = datetime.now(timezone.utc).year
OLD_YEAR = 2018


def _base(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    old_season = Season(sport_id=sport.id, year=OLD_YEAR)
    current_season = Season(sport_id=sport.id, year=CURRENT_YEAR)
    db.add_all([old_season, current_season])
    db.flush()
    old_round = Round(season_id=old_season.id, round_number=1)
    current_round = Round(season_id=current_season.id, round_number=1)
    db.add_all([old_round, current_round])
    home = Team(sport_id=sport.id, name="Melbourne", short_name="MEL")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db.add_all([home, away])
    db.flush()
    old_match = Match(
        sport_id=sport.id, season_id=old_season.id, round_id=old_round.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=datetime(OLD_YEAR, 4, 1, tzinfo=timezone.utc), status=MatchStatus.COMPLETED, home_score=90, away_score=80,
    )
    current_match = Match(
        sport_id=sport.id, season_id=current_season.id, round_id=current_round.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=datetime(CURRENT_YEAR, 4, 1, tzinfo=timezone.utc), status=MatchStatus.SCHEDULED,
    )
    db.add_all([old_match, current_match])
    db.flush()
    return {"sport": sport, "home": home, "away": away, "old_match": old_match, "current_match": current_match}


def test_current_season_year_defaults_to_calendar_year(db_session):
    _base(db_session)
    assert current_season_year(db_session) == CURRENT_YEAR


def test_current_season_year_falls_back_to_latest_ingested_season(db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    db_session.add(Season(sport_id=sport.id, year=OLD_YEAR))
    db_session.commit()
    assert current_season_year(db_session) == OLD_YEAR


def test_retired_historical_player_excluded_from_current_view(db_session):
    """A player like Jack Watts: played only in an old season, has no
    current-season match/lineup/projection, and is_active was never
    positively set - must NOT be current."""
    ctx = _base(db_session)
    retired = Player(sport_id=ctx["sport"].id, display_name="Jack Watts", current_team_id=ctx["home"].id, source="afltables", source_player_id="p_retired")
    db_session.add(retired)
    db_session.flush()
    db_session.add(PlayerMatchStat(
        player_id=retired.id, match_id=ctx["old_match"].id, team_id=ctx["home"].id, source="afltables", recorded_at=datetime.now(timezone.utc), disposals=20,
    ))
    db_session.commit()

    current_ids = current_player_ids(db_session)
    assert not is_current_player(retired.id, current_ids)


def test_current_season_player_included(db_session):
    """A player who has actually played a match in the current season."""
    ctx = _base(db_session)
    active = Player(sport_id=ctx["sport"].id, display_name="Current Star", current_team_id=ctx["home"].id, source="afltables", source_player_id="p_active")
    db_session.add(active)
    db_session.flush()
    db_session.add(PlayerMatchStat(
        player_id=active.id, match_id=ctx["current_match"].id, team_id=ctx["home"].id, source="afltables", recorded_at=datetime.now(timezone.utc), disposals=25,
    ))
    db_session.commit()

    current_ids = current_player_ids(db_session)
    assert is_current_player(active.id, current_ids)


def test_current_season_player_via_expected_lineup_included(db_session):
    """A player with no PlayerMatchStat yet this season, but named in an
    ExpectedLineup for an upcoming current-season match."""
    ctx = _base(db_session)
    upcoming = Player(sport_id=ctx["sport"].id, display_name="Named Player", current_team_id=ctx["home"].id, source="afltables", source_player_id="p_named")
    db_session.add(upcoming)
    db_session.flush()
    db_session.add(ExpectedLineup(
        match_id=ctx["current_match"].id, player_id=upcoming.id, team_id=ctx["home"].id, status="expected_in",
        selection_status="named_in_squad", is_confirmed=False, recorded_at=datetime.now(timezone.utc), source="manual",
    ))
    db_session.commit()

    current_ids = current_player_ids(db_session)
    assert is_current_player(upcoming.id, current_ids)


def test_new_debut_player_with_no_history_included(db_session):
    """A genuine 2026 debutant added via the manual new-player workflow
    (is_active=True, zero PlayerMatchStat rows) must never be hidden merely
    for lacking historical data."""
    ctx = _base(db_session)
    debut = Player(
        sport_id=ctx["sport"].id, display_name="Fresh Debutant", current_team_id=ctx["home"].id,
        source="manual_2026", source_player_id="p_debut", is_active=True,
    )
    db_session.add(debut)
    db_session.commit()

    current_ids = current_player_ids(db_session)
    assert is_current_player(debut.id, current_ids)


def test_projection_only_player_included(db_session):
    ctx = _base(db_session)
    projected = Player(sport_id=ctx["sport"].id, display_name="Projected Player", current_team_id=ctx["home"].id, source="afltables", source_player_id="p_proj")
    db_session.add(projected)
    db_session.flush()
    db_session.add(PlayerDisposalProjection(
        match_id=ctx["current_match"].id, player_id=projected.id, team_id=ctx["home"].id, model_name="disposals_nb", model_version="v1",
        generated_at=datetime.now(timezone.utc), data_cutoff=datetime.now(timezone.utc), lineup_status_at_generation="expected_in",
        games_of_history=10, predicted_mean=20.0, distribution_method="nb", nb_alpha=8.0, confidence_tier="moderate_confidence",
    ))
    db_session.commit()

    current_ids = current_player_ids(db_session)
    assert is_current_player(projected.id, current_ids)
