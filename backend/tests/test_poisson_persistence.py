from datetime import datetime, timezone

from sqlalchemy import select

from app.modelling.poisson_backtest import PoissonPrediction
from app.modelling.poisson_persistence import persist_poisson_predictions
from app.models import Match, MatchStatus, PoissonMatchPrediction, Round, Season, Sport, Team


def _seed_match(db_session, match_id_hint: int) -> Match:
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

    round_ = Round(season_id=season.id, round_number=match_id_hint)
    home = Team(sport_id=sport.id, name=f"Home{match_id_hint}", short_name=f"H{match_id_hint}")
    away = Team(sport_id=sport.id, name=f"Away{match_id_hint}", short_name=f"A{match_id_hint}")
    db_session.add_all([round_, home, away])
    db_session.flush()

    match = Match(
        sport_id=sport.id,
        season_id=season.id,
        round_id=round_.id,
        home_team_id=home.id,
        away_team_id=away.id,
        scheduled_start=datetime(2024, 3, match_id_hint, tzinfo=timezone.utc),
        status=MatchStatus.COMPLETED,
        home_score=93,
        away_score=79,
    )
    db_session.add(match)
    db_session.flush()
    return match


def _prediction_for(match: Match) -> PoissonPrediction:
    return PoissonPrediction(
        match_id=match.id,
        season_year=2024,
        round_number=1,
        scheduled_start=match.scheduled_start,
        home_team_id=match.home_team_id,
        away_team_id=match.away_team_id,
        home_expected_goals=13.0,
        home_expected_behinds=15.0,
        away_expected_goals=11.0,
        away_expected_behinds=13.0,
        home_win_probability=0.6,
        draw_probability=0.02,
        away_win_probability=0.38,
        expected_total_points=176.0,
        expected_margin=14.0,
        actual_home_outcome=1.0,
        actual_total_points=172,
        actual_margin=14,
    )


def test_persist_creates_one_row_per_prediction(db_session):
    match = _seed_match(db_session, 1)
    written = persist_poisson_predictions(db_session, [_prediction_for(match)])

    assert written == 1
    row = db_session.scalar(select(PoissonMatchPrediction).where(PoissonMatchPrediction.match_id == match.id))
    assert row.expected_total_points == 176.0
    assert row.actual_total_points == 172


def test_persist_is_wholesale_replace_not_additive(db_session):
    match1 = _seed_match(db_session, 1)
    match2 = _seed_match(db_session, 2)

    persist_poisson_predictions(db_session, [_prediction_for(match1)])
    assert len(db_session.scalars(select(PoissonMatchPrediction)).all()) == 1

    persist_poisson_predictions(db_session, [_prediction_for(match2)])
    rows = db_session.scalars(select(PoissonMatchPrediction)).all()

    assert len(rows) == 1
    assert rows[0].match_id == match2.id


def test_sequence_reflects_prediction_order(db_session):
    match1 = _seed_match(db_session, 1)
    match2 = _seed_match(db_session, 2)

    persist_poisson_predictions(db_session, [_prediction_for(match1), _prediction_for(match2)])

    row1 = db_session.scalar(select(PoissonMatchPrediction).where(PoissonMatchPrediction.match_id == match1.id))
    row2 = db_session.scalar(select(PoissonMatchPrediction).where(PoissonMatchPrediction.match_id == match2.id))

    assert row1.sequence == 0
    assert row2.sequence == 1
