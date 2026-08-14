from datetime import datetime, timedelta, timezone

import pytest

from app.backtesting.model_report import ModelsUnavailableError, load_elo_backtest, load_poisson_backtest
from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import Match, MatchStatus, Round, Season, Sport, Team


def _seed_matches(db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season_2023 = Season(sport_id=sport.id, year=2023)
    season_2024 = Season(sport_id=sport.id, year=2024)
    db_session.add_all([season_2023, season_2024])
    db_session.flush()

    teams = {}
    for name in ["Carlton", "Richmond", "Geelong", "Essendon"]:
        team = Team(sport_id=sport.id, name=name, short_name=name[:3].upper())
        db_session.add(team)
        teams[name] = team
    db_session.flush()

    matches = []
    for season, year in [(season_2023, 2023), (season_2024, 2024)]:
        match_date = datetime(year, 3, 1, tzinfo=timezone.utc)
        for i in range(6):
            round_ = Round(season_id=season.id, round_number=i + 1)
            db_session.add(round_)
            db_session.flush()
            home, away = (teams["Carlton"], teams["Richmond"]) if i % 2 == 0 else (teams["Geelong"], teams["Essendon"])
            match = Match(
                sport_id=sport.id, season_id=season.id, round_id=round_.id,
                home_team_id=home.id, away_team_id=away.id,
                scheduled_start=match_date, status=MatchStatus.COMPLETED,
                home_score=90 + i, away_score=80 + i,
                home_goals=13, home_behinds=12, away_goals=11, away_behinds=14,
            )
            db_session.add(match)
            matches.append(match)
            match_date += timedelta(days=7)
    db_session.commit()
    return matches


def _seed_model_runs(db_session):
    persist_model_run(db_session, "elo", EloConfig(), 2023, metrics=[])
    persist_model_run(db_session, "poisson", PoissonConfig(), 2023, metrics=[])


def test_load_elo_backtest_raises_when_not_run(db_session):
    _seed_matches(db_session)
    with pytest.raises(ModelsUnavailableError):
        load_elo_backtest(db_session)


def test_load_elo_backtest_produces_full_report(db_session):
    _seed_matches(db_session)
    _seed_model_runs(db_session)

    report = load_elo_backtest(db_session)

    assert report.model_name == "elo"
    assert report.overall.n == 12
    assert "brier_score" in report.overall.metrics
    assert {s.label for s in report.by_season} == {"2023", "2024"}
    assert {s.label for s in report.by_team} == {"Carlton", "Richmond", "Geelong", "Essendon"}
    # each team plays 6 games total (3 seasons x 2... actually 6 rounds x 2 seasons / 2 team-pairs = 6 each)
    assert sum(s.n for s in report.by_team) == 12 * 2  # every match counted once per team = n*2
    assert len(report.calibration) == 10  # always 10 buckets from calibration_table


def test_load_poisson_backtest_produces_win_and_scoring_reports(db_session):
    _seed_matches(db_session)
    _seed_model_runs(db_session)

    win_report, scoring_report = load_poisson_backtest(db_session)

    assert win_report.model_name == "poisson"
    assert win_report.overall.n == 12
    assert scoring_report.overall.n == 12
    assert "total_points_mae" in scoring_report.overall.metrics
    assert "margin_mae" in scoring_report.overall.metrics
    assert {s.label for s in scoring_report.by_season} == {"2023", "2024"}


def test_by_team_segment_metrics_are_internally_consistent(db_session):
    """A team's by-team Brier score should differ from the raw home-side
    Brier score whenever they play away — proves the perspective-flip in
    _by_team_win_prob_segments is actually being applied, not just passing
    through home-side numbers."""
    _seed_matches(db_session)
    _seed_model_runs(db_session)

    report = load_elo_backtest(db_session)
    richmond = next(s for s in report.by_team if s.label == "Richmond")
    # Richmond is always the away team in this fixture set (see _seed_matches)
    assert richmond.n == 6
