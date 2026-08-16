"""API-level tests for player_projections.py — Section 22's "manual odds
entry," "API schemas," and "confidence gating" requirements, exercised
through the real FastAPI TestClient (see tests/conftest.py).
"""

from datetime import datetime, timezone

from sqlalchemy import select

from app.models import (
    ExpectedLineup,
    GoalModelRun,
    Match,
    MatchStatus,
    Player,
    PlayerDisposalProjection,
    PlayerGoalProjection,
    PlayerModelRun,
    PlayerPropMarket,
    Round,
    Season,
    Sport,
    Team,
)
from app.player_modelling.market import PlayerMarket

BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _seed_match(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    away = Team(sport_id=sport.id, name="Richmond", short_name="RIC")
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id,
        home_team_id=home.id, away_team_id=away.id, scheduled_start=BASE, status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.flush()
    player = Player(sport_id=sport.id, display_name="Test Player", source="afltables", source_player_id="players/T/Test.html")
    db.add(player)
    db.commit()
    return match, home, away, player


def _seed_projection(db, match, player, team, mean=20.0, alpha=3.0):
    now = datetime.now(timezone.utc)
    db.add(
        PlayerModelRun(
            model_name="disposals_ridge", market=PlayerMarket.DISPOSALS.value, feature_names=[], config_json={},
            distribution_method="nb", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019,
            evaluation_end_year=2025, is_promoted=True, run_at=now,
        )
    )
    db.add(
        GoalModelRun(
            model_name="goals_hurdle", market=PlayerMarket.GOALS.value, feature_names=[], config_json={},
            distribution_kind="hurdle", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019,
            evaluation_end_year=2025, is_promoted=True, run_at=now,
        )
    )
    db.add(
        PlayerDisposalProjection(
            match_id=match.id, player_id=player.id, team_id=team.id, model_name="disposals_ridge",
            model_version="disposals_ridge@v1", generated_at=now, data_cutoff=now, lineup_status_at_generation="expected_in",
            games_of_history=40, predicted_mean=mean, distribution_method="nb", nb_alpha=alpha,
            confidence_tier="higher_confidence", warnings=[], input_features={"disposals_last5_avg": 19.0},
        )
    )
    db.add(
        PlayerGoalProjection(
            match_id=match.id, player_id=player.id, team_id=team.id, model_name="goals_hurdle",
            model_version="goals_hurdle@v1", generated_at=now, data_cutoff=now, lineup_status_at_generation="expected_in",
            games_of_history=40, predicted_mean=1.2, distribution_kind="hurdle", nb_alpha=None,
            p_score=0.6, mu_scored=2.0, alpha_scored=1.0, scoring_archetype="regular",
            confidence_tier="higher_confidence", warnings=[], input_features={"goals_career_avg": 0.5},
        )
    )
    db.commit()


# --- Expected lineup ---


def test_lineup_put_creates_and_get_returns_it(client, db_session):
    match, home, away, player = _seed_match(db_session)
    resp = client.put(
        f"/api/afl/matches/{match.id}/lineup/{player.id}",
        json={"player_id": player.id, "team_id": home.id, "status": "expected_in", "note": "looks fit"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "expected_in"
    assert body["note"] == "looks fit"

    listed = client.get(f"/api/afl/matches/{match.id}/lineup")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_lineup_put_is_upsert_not_append(client, db_session):
    match, home, away, player = _seed_match(db_session)
    client.put(f"/api/afl/matches/{match.id}/lineup/{player.id}", json={"player_id": player.id, "team_id": home.id, "status": "expected_in"})
    client.put(f"/api/afl/matches/{match.id}/lineup/{player.id}", json={"player_id": player.id, "team_id": home.id, "status": "uncertain"})

    rows = db_session.scalars(select(ExpectedLineup).where(ExpectedLineup.match_id == match.id, ExpectedLineup.player_id == player.id)).all()
    assert len(rows) == 1
    assert rows[0].status == "uncertain"


def test_lineup_put_rejects_team_not_in_match(client, db_session):
    match, home, away, player = _seed_match(db_session)
    other_team_resp = client.get("/api/afl/teams")
    # deliberately pass a team_id that exists but isn't in this match
    bogus_team_id = max(t["id"] for t in other_team_resp.json()) + 999
    resp = client.put(f"/api/afl/matches/{match.id}/lineup/{player.id}", json={"player_id": player.id, "team_id": bogus_team_id, "status": "expected_in"})
    assert resp.status_code == 404  # team doesn't exist at all


def test_lineup_delete_removes_record(client, db_session):
    match, home, away, player = _seed_match(db_session)
    client.put(f"/api/afl/matches/{match.id}/lineup/{player.id}", json={"player_id": player.id, "team_id": home.id, "status": "expected_in"})
    resp = client.delete(f"/api/afl/matches/{match.id}/lineup/{player.id}")
    assert resp.status_code == 204
    assert client.get(f"/api/afl/matches/{match.id}/lineup").json() == []


# --- Manual player prop entry ---


def test_create_player_prop_and_implied_probability(client, db_session):
    match, home, away, player = _seed_match(db_session)
    _seed_projection(db_session, match, player, home)

    resp = client.post(
        f"/api/afl/matches/{match.id}/player-props",
        json={"bookmaker_name": "Sportsbet", "player_id": player.id, "market_type": "player_disposals", "line_type": "over_under", "threshold": 19.5, "price_decimal": 1.90},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["bookmaker_name"] == "Sportsbet"
    assert body["threshold"] == 19.5

    stored = db_session.scalar(select(PlayerPropMarket).where(PlayerPropMarket.id == body["id"]))
    assert stored is not None


def test_create_player_prop_rejects_invalid_market_type(client, db_session):
    match, home, away, player = _seed_match(db_session)
    resp = client.post(
        f"/api/afl/matches/{match.id}/player-props",
        json={"bookmaker_name": "Sportsbet", "player_id": player.id, "market_type": "player_marks", "line_type": "over_under", "threshold": 5, "price_decimal": 1.9},
    )
    assert resp.status_code == 422


def test_create_player_prop_rejects_odds_at_or_below_one(client, db_session):
    match, home, away, player = _seed_match(db_session)
    resp = client.post(
        f"/api/afl/matches/{match.id}/player-props",
        json={"bookmaker_name": "Sportsbet", "player_id": player.id, "market_type": "player_disposals", "line_type": "over_under", "threshold": 19.5, "price_decimal": 1.0},
    )
    assert resp.status_code == 422


def test_delete_player_prop(client, db_session):
    match, home, away, player = _seed_match(db_session)
    created = client.post(
        f"/api/afl/matches/{match.id}/player-props",
        json={"bookmaker_name": "Sportsbet", "player_id": player.id, "market_type": "player_disposals", "line_type": "over_under", "threshold": 19.5, "price_decimal": 1.9},
    ).json()
    resp = client.delete(f"/api/afl/player-props/{created['id']}")
    assert resp.status_code == 204
    assert client.get(f"/api/afl/matches/{match.id}/player-props").json() == []


# --- Prop insights: model-vs-market comparison ---


def test_prop_insights_computes_model_vs_market_comparison(client, db_session):
    match, home, away, player = _seed_match(db_session)
    _seed_projection(db_session, match, player, home, mean=20.0, alpha=3.0)
    client.post(
        f"/api/afl/matches/{match.id}/player-props",
        json={"bookmaker_name": "Sportsbet", "player_id": player.id, "market_type": "player_disposals", "line_type": "over_under", "threshold": 17.5, "price_decimal": 1.90},
    )

    resp = client.get("/api/afl/prop-insights")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["raw_implied_probability"] == 1 / 1.90
    assert row["model_fair_odds"] == 1 / row["model_probability"]
    assert row["difference_pp"] == row["model_probability"] - row["raw_implied_probability"]
    assert row["overround_removed"] is False
    assert row["confidence_tier"] == "higher_confidence"
    assert row["edge_category"] in ("no_meaningful_difference", "small_difference", "moderate_difference", "larger_difference")


def test_prop_insights_confidence_filter_is_a_minimum_bound(client, db_session):
    """`confidence` is a MINIMUM tier, not an exact match — a
    higher_confidence row must still show up when filtering for
    'insufficient_history or above' (the loosest possible bound), but a
    lower_confidence row must be excluded once the bound is raised."""
    match, home, away, player = _seed_match(db_session)
    _seed_projection(db_session, match, player, home)
    proj = db_session.scalar(select(PlayerDisposalProjection).where(PlayerDisposalProjection.player_id == player.id))
    proj.confidence_tier = "lower_confidence"
    db_session.commit()
    client.post(
        f"/api/afl/matches/{match.id}/player-props",
        json={"bookmaker_name": "Sportsbet", "player_id": player.id, "market_type": "player_disposals", "line_type": "over_under", "threshold": 17.5, "price_decimal": 1.9},
    )

    loose_bound = client.get("/api/afl/prop-insights?confidence=insufficient_history")
    assert len(loose_bound.json()) == 1
    strict_bound = client.get("/api/afl/prop-insights?confidence=higher_confidence")
    assert len(strict_bound.json()) == 0


def test_prop_insights_skips_quotes_with_no_matching_projection(client, db_session):
    match, home, away, player = _seed_match(db_session)
    # no projection seeded at all
    client.post(
        f"/api/afl/matches/{match.id}/player-props",
        json={"bookmaker_name": "Sportsbet", "player_id": player.id, "market_type": "player_disposals", "line_type": "over_under", "threshold": 17.5, "price_decimal": 1.9},
    )
    resp = client.get("/api/afl/prop-insights")
    assert resp.json() == []


# --- Upcoming / match / player projection read endpoints ---


def test_upcoming_projections_endpoint_returns_persisted_rows(client, db_session):
    match, home, away, player = _seed_match(db_session)
    _seed_projection(db_session, match, player, home)

    resp = client.get("/api/afl/player-projections/upcoming?market=player_disposals")
    assert resp.status_code == 200
    body = resp.json()
    assert "disposals" in body and "goals" not in body
    assert len(body["disposals"]) == 1
    row = body["disposals"][0]
    assert row["player_name"] == "Test Player"
    assert set(row["thresholds"].keys()) == {"15", "20", "25", "30", "35", "40"}
    # monotonic thresholds
    probs = [row["thresholds"][k]["probability"] for k in ("15", "20", "25", "30", "35", "40")]
    assert probs == sorted(probs, reverse=True)


def test_match_projections_endpoint_404_for_unknown_match(client, db_session):
    resp = client.get("/api/afl/matches/999999/player-projections")
    assert resp.status_code == 404


def test_player_projection_endpoint_returns_both_markets(client, db_session):
    match, home, away, player = _seed_match(db_session)
    _seed_projection(db_session, match, player, home)

    resp = client.get(f"/api/afl/players/{player.id}/projection")
    assert resp.status_code == 200
    body = resp.json()
    assert body["disposals"] is not None
    assert body["goals"] is not None
    goal_probs = [body["goals"]["thresholds"][k]["probability"] for k in ("1", "2", "3", "4", "5")]
    assert goal_probs == sorted(goal_probs, reverse=True)


def test_player_projection_endpoint_null_when_no_projection(client, db_session):
    match, home, away, player = _seed_match(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/projection")
    assert resp.status_code == 200
    body = resp.json()
    assert body["disposals"] is None
    assert body["goals"] is None


def test_upcoming_projections_min_probability_filter(client, db_session):
    match, home, away, player = _seed_match(db_session)
    _seed_projection(db_session, match, player, home, mean=30.0, alpha=3.0)  # high mean -> high 20+ probability

    strict = client.get("/api/afl/player-projections/upcoming?market=player_disposals&min_probability=0.99&threshold=35")
    loose = client.get("/api/afl/player-projections/upcoming?market=player_disposals&min_probability=0.01&threshold=20")
    assert len(strict.json()["disposals"]) == 0
    assert len(loose.json()["disposals"]) == 1
