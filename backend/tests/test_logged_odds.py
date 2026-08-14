from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.backtesting.logged_odds import ModelsUnavailableError, actual_outcome_for_quote, build_logged_odds_report
from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import Bookmaker, Match, MatchStatus, OddsQuote, Round, Season, Sport, Team


def _seed_completed_match(db_session, home_score=90, away_score=80, round_number=1) -> Match:
    sport = db_session.scalar(select(Sport).where(Sport.code == "AFL"))
    if sport is None:
        sport = Sport(code="AFL", name="Australian Football League")
        db_session.add(sport)
        db_session.flush()
    season = db_session.scalar(select(Season).where(Season.sport_id == sport.id, Season.year == 2024))
    if season is None:
        season = Season(sport_id=sport.id, year=2024)
        db_session.add(season)
        db_session.flush()
    round_ = Round(season_id=season.id, round_number=round_number)
    home = Team(sport_id=sport.id, name=f"Home{round_number}", short_name=f"H{round_number}")
    away = Team(sport_id=sport.id, name=f"Away{round_number}", short_name=f"A{round_number}")
    db_session.add_all([round_, home, away])
    db_session.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id,
        home_team_id=home.id, away_team_id=away.id,
        scheduled_start=datetime(2024, 3, round_number, tzinfo=timezone.utc), status=MatchStatus.COMPLETED,
        home_score=home_score, away_score=away_score,
        home_goals=13, home_behinds=home_score - 13 * 6, away_goals=11, away_behinds=away_score - 11 * 6,
    )
    db_session.add(match)
    db_session.commit()
    return match


def _add_quote(db_session, match, market_type, selection, price, line_value=None, bookmaker_name="Sportsbet"):
    bookmaker = db_session.scalar(select(Bookmaker).where(Bookmaker.name == bookmaker_name))
    if bookmaker is None:
        bookmaker = Bookmaker(name=bookmaker_name)
        db_session.add(bookmaker)
        db_session.flush()
    quote = OddsQuote(
        match_id=match.id, bookmaker_id=bookmaker.id, market_type=market_type, selection=selection,
        line_value=line_value, price_decimal=price, recorded_at=datetime.now(timezone.utc),
        source="manual", is_closing_line=False,
    )
    db_session.add(quote)
    db_session.commit()
    return quote


def _seed_model_runs(db_session):
    persist_model_run(db_session, "elo", EloConfig(), 2023, metrics=[])
    persist_model_run(db_session, "poisson", PoissonConfig(), 2023, metrics=[])


class TestActualOutcomeForQuote:
    def test_h2h_home_win(self, db_session):
        match = _seed_completed_match(db_session, home_score=90, away_score=80)
        quote = _add_quote(db_session, match, "h2h", match.home_team.name, 1.85)
        assert actual_outcome_for_quote(match, quote) is True

    def test_h2h_home_loss(self, db_session):
        match = _seed_completed_match(db_session, home_score=70, away_score=90)
        quote = _add_quote(db_session, match, "h2h", match.home_team.name, 1.85)
        assert actual_outcome_for_quote(match, quote) is False

    def test_h2h_draw_is_void(self, db_session):
        match = _seed_completed_match(db_session, home_score=80, away_score=80)
        quote = _add_quote(db_session, match, "h2h", match.home_team.name, 1.85)
        assert actual_outcome_for_quote(match, quote) is None

    def test_unresolved_match_returns_none(self, db_session):
        match = _seed_completed_match(db_session, home_score=90, away_score=80)
        match.status = MatchStatus.SCHEDULED
        match.home_score = None
        match.away_score = None
        db_session.commit()
        quote = _add_quote(db_session, match, "h2h", match.home_team.name, 1.85)
        assert actual_outcome_for_quote(match, quote) is None

    def test_line_home_favourite_covers(self, db_session):
        match = _seed_completed_match(db_session, home_score=100, away_score=80)  # margin +20
        quote = _add_quote(db_session, match, "line", match.home_team.name, 1.9, line_value=-12.5)
        assert actual_outcome_for_quote(match, quote) is True

    def test_line_home_favourite_fails_to_cover(self, db_session):
        match = _seed_completed_match(db_session, home_score=90, away_score=85)  # margin +5
        quote = _add_quote(db_session, match, "line", match.home_team.name, 1.9, line_value=-12.5)
        assert actual_outcome_for_quote(match, quote) is False

    def test_total_over_covered(self, db_session):
        match = _seed_completed_match(db_session, home_score=100, away_score=80)  # total 180
        quote = _add_quote(db_session, match, "total", "over", 1.9, line_value=165.5)
        assert actual_outcome_for_quote(match, quote) is True

    def test_total_under_not_covered(self, db_session):
        match = _seed_completed_match(db_session, home_score=100, away_score=80)  # total 180
        quote = _add_quote(db_session, match, "total", "under", 1.9, line_value=165.5)
        assert actual_outcome_for_quote(match, quote) is False


class TestBuildLoggedOddsReport:
    def test_raises_when_models_not_run(self, db_session):
        with pytest.raises(ModelsUnavailableError):
            build_logged_odds_report(db_session)

    def test_empty_report_when_no_odds_on_completed_matches(self, db_session):
        _seed_model_runs(db_session)
        report = build_logged_odds_report(db_session)

        assert report.n_total == 0
        assert report.n_resolved == 0
        assert report.win_rate is None
        assert report.roi_pct is None
        assert report.total_pnl_units == pytest.approx(0.0)

    def test_winning_h2h_selection(self, db_session):
        _seed_model_runs(db_session)
        match = _seed_completed_match(db_session, home_score=100, away_score=70)
        _add_quote(db_session, match, "h2h", match.home_team.name, 2.00)

        report = build_logged_odds_report(db_session)

        assert report.n_resolved == 1
        assert report.win_rate == pytest.approx(1.0)
        assert report.total_pnl_units == pytest.approx(1.0)  # price 2.00 - 1
        assert report.roi_pct == pytest.approx(100.0)

    def test_losing_h2h_selection(self, db_session):
        _seed_model_runs(db_session)
        match = _seed_completed_match(db_session, home_score=70, away_score=100)
        _add_quote(db_session, match, "h2h", match.home_team.name, 2.00)

        report = build_logged_odds_report(db_session)

        assert report.win_rate == pytest.approx(0.0)
        assert report.total_pnl_units == pytest.approx(-1.0)
        assert report.roi_pct == pytest.approx(-100.0)

    def test_void_draw_excluded_from_win_rate_but_counted_in_total(self, db_session):
        _seed_model_runs(db_session)
        match = _seed_completed_match(db_session, home_score=80, away_score=80)
        _add_quote(db_session, match, "h2h", match.home_team.name, 1.85)

        report = build_logged_odds_report(db_session)

        assert report.n_total == 1
        assert report.n_resolved == 0
        assert report.n_void == 1
        assert report.win_rate is None
        assert report.total_pnl_units == pytest.approx(0.0)

    def test_mixed_selections_aggregate_correctly(self, db_session):
        _seed_model_runs(db_session)
        win_match = _seed_completed_match(db_session, home_score=100, away_score=70, round_number=1)
        lose_match = _seed_completed_match(db_session, home_score=70, away_score=100, round_number=2)
        _add_quote(db_session, win_match, "h2h", win_match.home_team.name, 1.50)
        _add_quote(db_session, lose_match, "h2h", lose_match.home_team.name, 3.00)

        report = build_logged_odds_report(db_session)

        assert report.n_resolved == 2
        assert report.win_rate == pytest.approx(0.5)
        assert report.total_pnl_units == pytest.approx(0.50 - 1.0)
        assert len(report.selections) == 2

    def test_selection_has_model_probability_populated(self, db_session):
        _seed_model_runs(db_session)
        match = _seed_completed_match(db_session, home_score=100, away_score=70)
        _add_quote(db_session, match, "h2h", match.home_team.name, 1.85)

        report = build_logged_odds_report(db_session)

        assert 0.0 <= report.selections[0].model_probability <= 1.0

    def test_line_and_total_selections_are_scored_via_poisson(self, db_session):
        _seed_model_runs(db_session)
        match = _seed_completed_match(db_session, home_score=100, away_score=70)
        _add_quote(db_session, match, "line", match.home_team.name, 1.9, line_value=-12.5)
        _add_quote(db_session, match, "total", "over", 1.9, line_value=150.5)

        report = build_logged_odds_report(db_session)

        assert report.n_total == 2
        for s in report.selections:
            assert s.won is True  # home won by 30, well over any of these lines
