from datetime import datetime, timedelta, timezone

from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import Match, MatchStatus, Round, Season, Sport, Team


def _seed_matches(db_session):
    """Spans 2017-2020 so, against the default EVALUATION_START_YEAR=2019,
    2017-2018 land in warm-up and 2019-2020 land in the evaluation period —
    exercising the actual split, not just an all-evaluation edge case."""
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()

    teams = {}
    for name in ["Carlton", "Richmond", "Geelong", "Essendon"]:
        team = Team(sport_id=sport.id, name=name, short_name=name[:3].upper())
        db_session.add(team)
        teams[name] = team
    db_session.flush()

    matches = []
    for year in [2017, 2018, 2019, 2020]:
        season = Season(sport_id=sport.id, year=year)
        db_session.add(season)
        db_session.flush()
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
    persist_model_run(db_session, "elo", EloConfig(), 2018, metrics=[])
    persist_model_run(db_session, "poisson", PoissonConfig(), 2018, metrics=[])


def test_list_backtests_503_when_models_not_run(client, db_session):
    _seed_matches(db_session)
    response = client.get("/api/backtests")
    assert response.status_code == 200  # list endpoint degrades to empty, doesn't 503
    assert response.json() == []


def test_list_backtests_returns_elo_and_poisson_summaries(client, db_session):
    _seed_matches(db_session)
    _seed_model_runs(db_session)

    response = client.get("/api/backtests")

    assert response.status_code == 200
    body = response.json()
    ids = {row["id"] for row in body}
    assert ids == {"elo", "poisson"}
    for row in body:
        assert row["evaluation_start_year"] == 2019
        assert row["n_evaluation"] == 12  # 2019 + 2020, 6 matches each
        assert "headline_metrics" in row


def test_backtest_detail_unknown_id_404(client, db_session):
    _seed_matches(db_session)
    _seed_model_runs(db_session)
    response = client.get("/api/backtests/xgboost")
    assert response.status_code == 404


def test_backtest_detail_503_when_model_not_run(client, db_session):
    _seed_matches(db_session)
    response = client.get("/api/backtests/elo")
    assert response.status_code == 503


def test_elo_backtest_detail_excludes_scoring(client, db_session):
    _seed_matches(db_session)
    _seed_model_runs(db_session)

    response = client.get("/api/backtests/elo")

    assert response.status_code == 200
    body = response.json()
    assert body["model_name"] == "elo"
    assert body["scoring"] is None
    assert body["win_prob"]["period"]["n_evaluation"] == 12
    assert body["win_prob"]["period"]["n_warmup"] == 12  # 2017+2018
    assert len(body["win_prob"]["baseline_comparison"]) == 4  # model + 3 baselines


def test_poisson_backtest_detail_includes_scoring(client, db_session):
    _seed_matches(db_session)
    _seed_model_runs(db_session)

    response = client.get("/api/backtests/poisson")

    assert response.status_code == 200
    body = response.json()
    assert body["scoring"] is not None
    assert "total_points_mae" in body["scoring"]["evaluation_metrics"]
    assert "interval_coverage" in body["scoring"]


def test_backtest_calibration_endpoint(client, db_session):
    _seed_matches(db_session)
    _seed_model_runs(db_session)

    response = client.get("/api/backtests/elo/calibration")

    assert response.status_code == 200
    body = response.json()
    assert body["model_name"] == "elo"
    assert isinstance(body["buckets"], list)
    assert body["buckets"]  # favourite-perspective buckets always present, even if mostly n=0


def test_backtest_seasons_endpoint_elo(client, db_session):
    _seed_matches(db_session)
    _seed_model_runs(db_session)

    response = client.get("/api/backtests/elo/seasons")

    assert response.status_code == 200
    body = response.json()
    labels = {row["label"] for row in body["win_prob_by_season"]}
    assert labels == {"2019", "2020"}  # evaluation period only, warm-up excluded
    assert body["scoring_by_season"] is None


def test_backtest_seasons_endpoint_poisson_includes_scoring(client, db_session):
    _seed_matches(db_session)
    _seed_model_runs(db_session)

    response = client.get("/api/backtests/poisson/seasons")

    assert response.status_code == 200
    body = response.json()
    assert body["scoring_by_season"] is not None
    assert {row["label"] for row in body["scoring_by_season"]} == {"2019", "2020"}


def test_comparison_endpoint_503_when_models_not_run(client, db_session):
    _seed_matches(db_session)
    response = client.get("/api/backtests/comparison")
    assert response.status_code == 503


def test_comparison_endpoint_returns_disagreement_and_stability(client, db_session):
    _seed_matches(db_session)
    _seed_model_runs(db_session)

    response = client.get("/api/backtests/comparison")

    assert response.status_code == 200
    body = response.json()
    assert body["n_matches"] == 24  # all seeded matches, not just evaluation period
    assert len(body["disagreement_buckets"]) == 4
    assert {row["season_year"] for row in body["season_stability"]} == {"2017", "2018", "2019", "2020"}
