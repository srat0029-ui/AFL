"""Tests for the AFL data-validation system.

Most checks are pure functions over already-loaded rows, so most tests here
build ORM objects in memory (never added to a session) rather than touching
a database — see app/validation/checks.py's module docstring.
"""

from datetime import datetime, timezone

from app.models import Match, MatchStatus, Round, Season, Sport, Team, TeamMatchStat, Venue
from app.validation.checks import (
    build_season_summary,
    build_team_stats_coverage,
    check_matches,
    check_seasons,
    check_team_match_stats,
    check_teams,
    check_venues,
)
from app.validation.report import Level, ValidationReport
from app.validation.run import run_validation


def _team(id_: int, sport_id: int = 1, name: str = "Carlton", short_name: str = "CAR") -> Team:
    t = Team(sport_id=sport_id, name=name, short_name=short_name)
    t.id = id_
    return t


def _venue(id_: int, name: str) -> Venue:
    v = Venue(name=name)
    v.id = id_
    return v


def _season(id_: int, sport_id: int = 1, year: int = 2024) -> Season:
    s = Season(sport_id=sport_id, year=year)
    s.id = id_
    return s


def _match(
    id_: int,
    home_id: int,
    away_id: int,
    season_id: int = 1,
    round_id: int = 1,
    status: MatchStatus = MatchStatus.SCHEDULED,
    home_score: int | None = None,
    away_score: int | None = None,
    scheduled_start: datetime | None = None,
    external_ids: dict | None = None,
) -> Match:
    m = Match(
        sport_id=1,
        season_id=season_id,
        round_id=round_id,
        home_team_id=home_id,
        away_team_id=away_id,
        status=status,
        home_score=home_score,
        away_score=away_score,
        scheduled_start=scheduled_start or datetime(2024, 3, 1, tzinfo=timezone.utc),
        external_ids=external_ids,
    )
    m.id = id_
    return m


def test_check_teams_detects_duplicate_names():
    report = ValidationReport()
    check_teams([_team(1, name="Carlton"), _team(2, name="Carlton")], report)
    assert report.has_failures
    assert any("Duplicate team names" in r.message for r in report.results)


def test_check_teams_flags_missing_short_name():
    team = _team(1, short_name="")
    report = ValidationReport()
    check_teams([team], report)
    assert report.has_failures
    assert any("missing a required field" in r.message for r in report.results)


def test_check_teams_passes_for_typical_afl_dataset():
    teams = [_team(i, name=f"Team{i}", short_name=f"T{i}") for i in range(1, 19)]
    report = ValidationReport()
    check_teams(teams, report)
    assert not report.has_failures


def test_check_teams_warns_but_does_not_fail_on_unusual_count():
    report = ValidationReport()
    check_teams([_team(1)], report)
    assert report.has_warnings
    assert not report.has_failures


def test_check_venues_detects_duplicates():
    report = ValidationReport()
    check_venues([_venue(1, "M.C.G."), _venue(2, "M.C.G.")], report)
    assert report.has_failures
    assert any("Duplicate venue names" in r.message for r in report.results)


def test_check_venues_passes_for_unique_names():
    report = ValidationReport()
    check_venues([_venue(1, "M.C.G."), _venue(2, "S.C.G.")], report)
    assert not report.has_failures


def test_check_matches_detects_same_home_away_team():
    report = ValidationReport()
    check_matches([_match(1, home_id=1, away_id=1)], season_ids={1}, round_ids={1}, team_ids={1}, report=report)
    assert report.has_failures
    assert any("same team as home and away" in r.message for r in report.results)


def test_check_matches_detects_completed_match_without_scores():
    m = _match(1, home_id=1, away_id=2, status=MatchStatus.COMPLETED)
    report = ValidationReport()
    check_matches([m], season_ids={1}, round_ids={1}, team_ids={1, 2}, report=report)
    assert report.has_failures
    assert any("Completed matches missing scores" in r.message for r in report.results)


def test_check_matches_detects_upcoming_match_with_scores():
    m = _match(1, home_id=1, away_id=2, status=MatchStatus.SCHEDULED, home_score=90, away_score=80)
    report = ValidationReport()
    check_matches([m], season_ids={1}, round_ids={1}, team_ids={1, 2}, report=report)
    assert report.has_failures
    assert any("unexpectedly carry a final score" in r.message for r in report.results)


def test_check_matches_detects_duplicate_squiggle_ids():
    m1 = _match(1, home_id=1, away_id=2, external_ids={"squiggle": "100"})
    m2 = _match(2, home_id=1, away_id=2, external_ids={"squiggle": "100"})
    report = ValidationReport()
    check_matches([m1, m2], season_ids={1}, round_ids={1}, team_ids={1, 2}, report=report)
    assert report.has_failures
    assert any("Duplicate Squiggle match ids" in r.message for r in report.results)


def test_check_matches_detects_negative_score():
    m = _match(1, home_id=1, away_id=2, status=MatchStatus.COMPLETED, home_score=-5, away_score=80)
    report = ValidationReport()
    check_matches([m], season_ids={1}, round_ids={1}, team_ids={1, 2}, report=report)
    assert report.has_failures
    assert any("negative score" in r.message for r in report.results)


def test_check_matches_detects_orphan_season_reference():
    m = _match(1, home_id=1, away_id=2, season_id=999)
    report = ValidationReport()
    check_matches([m], season_ids={1}, round_ids={1}, team_ids={1, 2}, report=report)
    assert report.has_failures
    assert any("missing season" in r.message for r in report.results)


def test_check_matches_detects_orphan_team_reference():
    m = _match(1, home_id=1, away_id=999)
    report = ValidationReport()
    check_matches([m], season_ids={1}, round_ids={1}, team_ids={1}, report=report)
    assert report.has_failures
    assert any("missing team" in r.message for r in report.results)


def test_check_matches_passes_for_valid_dataset():
    m1 = _match(
        1, home_id=1, away_id=2, status=MatchStatus.COMPLETED,
        home_score=90, away_score=80, external_ids={"squiggle": "1"},
    )
    m2 = _match(2, home_id=1, away_id=2, status=MatchStatus.SCHEDULED, external_ids={"squiggle": "2"})
    report = ValidationReport()
    check_matches([m1, m2], season_ids={1}, round_ids={1}, team_ids={1, 2}, report=report)
    assert not report.has_failures


def test_check_seasons_detects_duplicates():
    report = ValidationReport()
    check_seasons([_season(1, year=2024), _season(2, year=2024)], report)
    assert report.has_failures


def test_check_seasons_detects_implausible_year():
    report = ValidationReport()
    check_seasons([_season(1, year=1500)], report)
    assert report.has_failures


def test_check_seasons_passes_for_valid_dataset():
    report = ValidationReport()
    check_seasons([_season(1, year=2024), _season(2, year=2025)], report)
    assert not report.has_failures


def test_warnings_do_not_count_as_failures():
    report = ValidationReport()
    report.add(Level.WARNING, "teams", "just a warning")
    assert report.has_warnings
    assert not report.has_failures


def test_build_season_summary_groups_by_year_and_flags_status_mix():
    m1 = _match(1, home_id=1, away_id=2, season_id=10, status=MatchStatus.COMPLETED, home_score=1, away_score=0)
    m2 = _match(2, home_id=1, away_id=2, season_id=10, status=MatchStatus.COMPLETED, home_score=1, away_score=0)
    m3 = _match(3, home_id=1, away_id=2, season_id=20, status=MatchStatus.SCHEDULED)
    lines = build_season_summary([m1, m2, m3], {10: 2024, 20: 2025})
    assert lines == ["2024: 2 matches  <- unusual match count, worth checking", "2025: 1 upcoming"]


def test_run_validation_end_to_end_on_seeded_db(db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season = Season(sport_id=sport.id, year=2024)
    db_session.add(season)
    db_session.flush()
    round_ = Round(season_id=season.id, round_number=1)
    venue = Venue(name="M.C.G.")
    home = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    away = Team(sport_id=sport.id, name="Richmond", short_name="RIC")
    db_session.add_all([round_, venue, home, away])
    db_session.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id,
        home_team_id=home.id, away_team_id=away.id, venue_id=venue.id,
        scheduled_start=datetime(2024, 3, 1, tzinfo=timezone.utc), status=MatchStatus.COMPLETED,
        home_score=90, away_score=80, external_ids={"squiggle": "1"},
    )
    db_session.add(match)
    db_session.commit()

    report = run_validation(db_session)

    assert not report.has_failures
    assert any("2024" in line for line in report.season_summary)


def test_run_validation_fails_cleanly_with_no_sport_row(db_session):
    report = run_validation(db_session)
    assert report.has_failures


def _team_stat(id_: int, match_id: int, team_id: int, goals: int | None = None, behinds: int | None = None) -> TeamMatchStat:
    s = TeamMatchStat(
        match_id=match_id, team_id=team_id, source="afltables",
        recorded_at=datetime(2024, 1, 1, tzinfo=timezone.utc), goals=goals, behinds=behinds,
    )
    s.id = id_
    return s


def test_check_team_match_stats_no_data_warns():
    report = ValidationReport()
    check_team_match_stats([], {}, report)
    assert report.has_warnings
    assert not report.has_failures


def test_check_team_match_stats_detects_goal_mismatch():
    stat = _team_stat(1, match_id=1, team_id=1, goals=20, behinds=10)
    report = ValidationReport()
    check_team_match_stats([stat], {(1, 1): (22, 10)}, report)  # official says 22 goals, source says 20
    assert report.has_failures
    assert any("goals disagree" in r.message for r in report.results)


def test_check_team_match_stats_small_behinds_gap_is_expected_and_passes():
    # 3-point gap is the documented "rushed behinds" case — should not fail or warn
    stat = _team_stat(1, match_id=1, team_id=1, goals=22, behinds=9)
    report = ValidationReport()
    check_team_match_stats([stat], {(1, 1): (22, 12)}, report)
    assert not report.has_failures
    assert not report.has_warnings


def test_check_team_match_stats_large_behinds_gap_warns():
    stat = _team_stat(1, match_id=1, team_id=1, goals=22, behinds=2)
    report = ValidationReport()
    check_team_match_stats([stat], {(1, 1): (22, 20)}, report)  # 18-point gap is implausible as "rushed"
    assert report.has_warnings
    assert not report.has_failures


def test_check_team_match_stats_detects_duplicates():
    stats = [_team_stat(1, match_id=1, team_id=1), _team_stat(2, match_id=1, team_id=1)]
    report = ValidationReport()
    check_team_match_stats(stats, {}, report)
    assert report.has_failures
    assert any("Duplicate team-match-stat" in r.message for r in report.results)


def test_build_team_stats_coverage_computes_percentage():
    completed = {2024: {1, 2, 3, 4}}
    covered = {2024: {1, 2, 3}}
    lines = build_team_stats_coverage(completed, covered)
    assert lines == ["2024: 3/4 matches (75%)"]


def test_build_team_stats_coverage_handles_missing_season():
    completed = {2024: {1, 2}}
    covered = {}
    lines = build_team_stats_coverage(completed, covered)
    assert lines == ["2024: 0/2 matches (0%)"]
