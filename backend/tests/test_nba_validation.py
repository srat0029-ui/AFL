"""app/nba/validation.py - the data-quality report. Each check is given a
small database containing exactly the defect it exists to catch."""

from datetime import date, datetime, timedelta, timezone

from app.models.nba import NbaBoxScoreState, NbaGame, NbaPlayer, NbaPlayerGameLog, NbaTeam
from app.nba.validation import (
    ABNORMAL_SEASONS,
    STANDARD_REGULAR_SEASON_GAMES,
    expected_regular_season_games,
    format_report,
    season_label,
    validate_nba_data,
)

TODAY = date(2026, 10, 2)


def _teams(db, n=2):
    teams = [NbaTeam(name=f"Team {i}", abbreviation=f"T{i}", external_ids={"test": str(i)}) for i in range(1, n + 1)]
    db.add_all(teams)
    db.flush()
    return teams


def _game(db, home, away, *, gid, year=2024, season_type="regular", status="final", home_score=20, away_score=16, day=date(2025, 1, 10), state="ingested"):
    game = NbaGame(
        season_start_year=year, season_type=season_type, game_date=day, scheduled_start=datetime(day.year, day.month, day.day, tzinfo=timezone.utc),
        status=status, home_team_id=home.id, away_team_id=away.id, home_score=home_score if status == "final" else None,
        away_score=away_score if status == "final" else None, source="test", source_game_id=gid, box_score_state=state if status == "final" else None,
    )
    db.add(game)
    db.flush()
    return game


def _full_box(db, game, home, away, *, tag, home_points=20, away_points=16, rows_per_team=8):
    """A credible box score: `rows_per_team` players a side, five starters,
    points adding up to the given totals."""
    for team, opponent, total, is_home in ((home, away, home_points, True), (away, home, away_points, False)):
        for i in range(rows_per_team):
            player = NbaPlayer(display_name=f"{tag} {team.abbreviation} P{i}", source="test", source_player_id=f"{tag}-{team.id}-{i}", current_team_id=team.id)
            db.add(player)
            db.flush()
            db.add(
                NbaPlayerGameLog(
                    player_id=player.id, game_id=game.id, team_id=team.id, opponent_team_id=opponent.id, is_home=is_home, source="test",
                    recorded_at=game.scheduled_start + timedelta(hours=5), started=i < 5, minutes=24.0, points=total if i == 0 else 0, rebounds=1, assists=1,
                )
            )
    db.commit()


def test_empty_database_reports_nothing_and_does_not_crash(db_session):
    report = validate_nba_data(db_session, today=TODAY)
    assert (report.games_total, report.player_game_logs_total, report.seasons, report.flags) == (0, 0, [], [])
    assert "FLAGS: none" in format_report(report)


def test_a_clean_game_raises_no_integrity_flag(db_session):
    home, away = _teams(db_session)
    _full_box(db_session, _game(db_session, home, away, gid="g1"), home, away, tag="a")
    report = validate_nba_data(db_session, today=TODAY)

    season = report.seasons[0]
    assert (season.label, season.games_total, season.regular_season_final, season.player_game_logs, season.unique_players, season.unique_teams) == ("2024-25", 1, 1, 16, 16, 2)
    assert (season.first_game_date, season.last_game_date) == (date(2025, 1, 10), date(2025, 1, 10))
    assert (report.games_points_mismatch_count, report.games_thin_box_score_count, report.games_wrong_starter_count_count) == (0, 0, 0)
    assert (report.duplicate_games, report.duplicate_players, report.duplicate_player_game_logs, report.logs_with_team_not_in_game) == (0, 0, 0, 0)
    assert not any(flag.startswith("INTEGRITY") for flag in report.flags)
    # One game is far short of a full season, and that IS flagged.
    assert any("1 final regular-season games stored, 1231 expected" in flag for flag in report.flags)


def test_points_that_do_not_add_up_are_caught(db_session):
    home, away = _teams(db_session)
    _full_box(db_session, _game(db_session, home, away, gid="g1", home_score=25), home, away, tag="a", home_points=20)
    report = validate_nba_data(db_session, today=TODAY)
    assert report.games_points_mismatch_count == 1 and report.games_points_mismatch == ["g1 (2025-01-10)"]
    assert report.seasons[0].games_with_suspect_logs == 1
    assert any("suspect box score" in flag for flag in report.flags)


def test_thin_box_scores_and_wrong_starter_counts_are_caught(db_session):
    home, away = _teams(db_session)
    _full_box(db_session, _game(db_session, home, away, gid="thin"), home, away, tag="a", rows_per_team=4)
    report = validate_nba_data(db_session, today=TODAY)
    assert report.games_thin_box_score == ["thin (2025-01-10)"]
    assert report.games_wrong_starter_count == ["thin (2025-01-10)"]  # four a side cannot be five starters each


def test_final_games_without_a_box_score_are_counted_by_season_and_preseason_is_not(db_session):
    home, away = _teams(db_session)
    _game(db_session, home, away, gid="missing", state="rejected")
    _game(db_session, home, away, gid="pre", season_type="preseason", day=date(2024, 10, 5), state=None)
    _game(db_session, home, away, gid="playoff", season_type="playoffs", day=date(2025, 5, 1), state=None)
    db_session.commit()
    report = validate_nba_data(db_session, today=TODAY)

    season = report.seasons[0]
    assert season.competitive_final_missing_box_score == 2  # the preseason game is not part of the modelling dataset
    assert (season.games_by_type, season.postseason_final) == ({"regular": 1, "preseason": 1, "playoffs": 1}, 1)
    assert report.box_score_states == {"rejected": 1, "not_attempted": 1}
    assert any("2 final competitive game(s) have no player logs" in flag for flag in report.flags)
    assert any("failed or was never attempted" in flag for flag in report.flags)


def test_abnormal_seasons_are_flagged_with_their_own_expected_counts(db_session):
    home, away = _teams(db_session)
    _game(db_session, home, away, gid="bubble", year=2019, day=date(2020, 8, 1))
    _game(db_session, home, away, gid="short", year=2020, day=date(2021, 1, 10))
    db_session.commit()
    report = validate_nba_data(db_session, today=TODAY)

    assert [(s.label, s.regular_season_expected) for s in report.seasons] == [("2019-20", 1059), ("2020-21", 1080)]
    assert all(s.abnormal_note for s in report.seasons)
    assert sum("KNOWN ABNORMAL SEASON" in flag for flag in report.flags) == 2
    assert STANDARD_REGULAR_SEASON_GAMES == 1230 and set(ABNORMAL_SEASONS) == {2019, 2020}


def test_expected_regular_season_games_by_season():
    assert expected_regular_season_games(2018) == 1230
    assert (expected_regular_season_games(2019), expected_regular_season_games(2020)) == (1059, 1080)
    # From 2023-24 the NBA Cup final is an extra game the source lists as regular season.
    assert (expected_regular_season_games(2022), expected_regular_season_games(2023), expected_regular_season_games(2025)) == (1230, 1231, 1231)


def test_a_partially_stored_game_is_reported_as_partial_not_as_a_points_mismatch(db_session):
    home, away = _teams(db_session)
    game = _game(db_session, home, away, gid="partial", home_score=25, state=NbaBoxScoreState.INGESTED_PARTIAL.value)
    _full_box(db_session, game, home, away, tag="a", home_points=20)  # 5 points belong to a player row with no id
    report = validate_nba_data(db_session, today=TODAY)
    assert (report.games_ingested_partial, report.games_points_mismatch_count) == (1, 0)
    assert any("one player row left out" in flag for flag in report.flags)


def test_a_season_still_in_progress_is_not_flagged_as_short(db_session):
    home, away = _teams(db_session)
    _game(db_session, home, away, gid="played", year=2026, day=date(2026, 10, 1))
    _game(db_session, home, away, gid="future", year=2026, day=date(2026, 12, 1), status="scheduled")
    db_session.commit()
    report = validate_nba_data(db_session, today=TODAY)
    assert any("2026-27: season not complete (1 games still scheduled)" in flag for flag in report.flags)
    assert not any("expected - review" in flag for flag in report.flags)


def test_broken_relationships_and_shared_names_are_reported(db_session):
    home, away, third = _teams(db_session, 3)
    game = _game(db_session, home, away, gid="g1")
    _full_box(db_session, game, home, away, tag="a")
    stray = NbaPlayer(display_name="a T1 P0", source="test", source_player_id="twin")  # same name as an existing player, different id
    idle = NbaPlayer(display_name="Never Played", source="test", source_player_id="idle")
    db_session.add_all([stray, idle])
    db_session.flush()
    db_session.add(
        NbaPlayerGameLog(
            player_id=stray.id, game_id=game.id, team_id=third.id, opponent_team_id=home.id, is_home=False, source="test",
            recorded_at=game.scheduled_start, minutes=None, points=0,
        )
    )
    db_session.commit()
    report = validate_nba_data(db_session, today=TODAY)

    assert report.logs_with_team_not_in_game == 1
    assert report.players_without_logs == 1
    assert report.players_sharing_a_name == ["a T1 P0 x2"]
    assert report.played_logs_missing_minutes == 1
    assert any("team did not play in that game" in flag for flag in report.flags)
    assert any("have no minutes value in the source" in flag for flag in report.flags)
    assert any("held by more than one player id" in flag for flag in report.flags)


def test_players_on_more_than_one_team_in_a_season_are_counted_not_flagged(db_session):
    home, away, third = _teams(db_session, 3)
    first = _game(db_session, home, away, gid="g1")
    second = _game(db_session, third, away, gid="g2", day=date(2025, 2, 10))
    _full_box(db_session, first, home, away, tag="a")
    traded = db_session.query(NbaPlayer).filter_by(source_player_id=f"a-{home.id}-1").one()
    db_session.add(
        NbaPlayerGameLog(player_id=traded.id, game_id=second.id, team_id=third.id, opponent_team_id=away.id, is_home=True, source="test", recorded_at=second.scheduled_start, minutes=20.0, points=0)
    )
    db_session.commit()
    report = validate_nba_data(db_session, today=TODAY)
    assert report.players_multiple_teams_in_a_season == 1
    assert not any("more than one team" in flag for flag in report.flags)  # a trade is normal, not a defect


def test_report_is_serialisable(db_session):
    home, away = _teams(db_session)
    _full_box(db_session, _game(db_session, home, away, gid="g1"), home, away, tag="a")
    report = validate_nba_data(db_session, today=TODAY)
    assert '"label": "2024-25"' in report.to_json() and "2024-25" in format_report(report)
    assert season_label(1999) == "1999-00" and NbaBoxScoreState.REJECTED.value == "rejected"
