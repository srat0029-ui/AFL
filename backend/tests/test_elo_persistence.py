from datetime import datetime, timezone

from sqlalchemy import select

from app.modelling.elo_backtest import EloPrediction
from app.modelling.elo_persistence import persist_elo_ratings
from app.models import EloRating, Match, MatchStatus, Round, Season, Sport, Team


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
        home_score=80,
        away_score=70,
    )
    db_session.add(match)
    db_session.flush()
    return match


def _prediction_for(match: Match) -> EloPrediction:
    return EloPrediction(
        match_id=match.id,
        season_year=2024,
        scheduled_start=match.scheduled_start,
        home_team_id=match.home_team_id,
        away_team_id=match.away_team_id,
        home_rating_before=1500.0,
        away_rating_before=1500.0,
        home_rating_after=1516.0,
        away_rating_after=1484.0,
        home_win_probability=0.55,
        actual_home_outcome=1.0,
    )


def test_persist_creates_two_rows_per_prediction(db_session):
    match = _seed_match(db_session, 1)
    prediction = _prediction_for(match)

    written = persist_elo_ratings(db_session, [prediction])

    assert written == 2
    rows = db_session.scalars(select(EloRating).where(EloRating.match_id == match.id)).all()
    assert len(rows) == 2
    ratings_by_team = {r.team_id: r for r in rows}
    assert ratings_by_team[match.home_team_id].rating_after == 1516.0
    assert ratings_by_team[match.away_team_id].rating_after == 1484.0


def test_persist_is_wholesale_replace_not_additive(db_session):
    match1 = _seed_match(db_session, 1)
    match2 = _seed_match(db_session, 2)

    persist_elo_ratings(db_session, [_prediction_for(match1)])
    assert len(db_session.scalars(select(EloRating)).all()) == 2

    # rerun with a different (single) prediction — should replace, not accumulate
    persist_elo_ratings(db_session, [_prediction_for(match2)])
    rows = db_session.scalars(select(EloRating)).all()

    assert len(rows) == 2
    assert all(r.match_id == match2.id for r in rows)


def test_sequence_reflects_prediction_order(db_session):
    match1 = _seed_match(db_session, 1)
    match2 = _seed_match(db_session, 2)

    persist_elo_ratings(db_session, [_prediction_for(match1), _prediction_for(match2)])

    rows_match1 = db_session.scalars(select(EloRating).where(EloRating.match_id == match1.id)).all()
    rows_match2 = db_session.scalars(select(EloRating).where(EloRating.match_id == match2.id)).all()

    assert rows_match1[0].sequence == 0
    assert rows_match2[0].sequence == 1
