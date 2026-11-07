"""Research panel endpoint: GET /api/nba/stat-predictions/games/{id}, over
frozen Stat Model V1 rows. Read-only, no odds, no recomputation."""

from datetime import timedelta

from app.models.nba import NbaPlayerGameLog, NbaPropProjection
from app.nba.stats import models as sm
from app.nba.stats.prospective import freeze_due
from app.nba.stats.read import nb_interval
from tests.test_nba_minutes_prospective import artifact_dir  # noqa: F401  (fixture)
from tests.test_nba_outlook_api import no_network  # noqa: F401  (fixture)
from tests.test_nba_stat_prospective import AT_T4H, TIP, _snapshot, _world_with_stats


def _frozen_world(db):
    out = _world_with_stats(db)
    _snapshot(db)
    freeze_due(db, now=AT_T4H + timedelta(minutes=2))
    return out


def test_shows_the_frozen_values_unchanged(client, db_session, artifact_dir, no_network):  # noqa: F811
    _, _, vet, _, game, _ = _frozen_world(db_session)
    stored = {r.market: r for r in db_session.query(NbaPropProjection).filter_by(player_id=vet.id)}
    body = client.get(f"/api/nba/stat-predictions/games/{game.id}").json()

    assert body["model_version"] == "stat-v1.0" and body["labels"] == ["T-4h", "T-1h"]
    assert [w["label"] for w in body["windows"]] == ["T-4h"]
    assert body["rows"] == db_session.query(NbaPropProjection).filter_by(game_id=game.id).count()
    players = {p["player_id"]: p for team in (body["home"], body["away"]) for p in team["players"]}
    pred = players[vet.id]["predictions"]["T-4h"]
    assert pred["stats"]["points"]["mean"] == stored["player_points"].predicted_mean
    assert pred["stats"]["rebounds"]["baselines"] == stored["player_rebounds"].inputs["baselines"]
    assert pred["expected_minutes"] == stored["player_points"].expected_minutes
    assert players[vet.id]["result"] is None


def test_points_has_no_range_and_the_validated_stats_do(client, db_session, artifact_dir, no_network):  # noqa: F811
    _, _, vet, _, game, _ = _frozen_world(db_session)
    body = client.get(f"/api/nba/stat-predictions/games/{game.id}").json()
    assert body["validation"]["points"]["calibrated"] is False
    assert body["validation"]["points"]["distribution_kind"] == "nb_bucket_uncalibrated"
    stats = next(p for t in (body["home"], body["away"]) for p in t["players"] if p["player_id"] == vet.id)["predictions"]["T-4h"]["stats"]
    assert stats["points"]["interval_80"] is None and stats["points"]["calibrated"] is False
    for stat in ("rebounds", "assists"):
        interval = stats[stat]["interval_80"]
        assert stats[stat]["calibrated"] is True and interval["p10"] <= stats[stat]["mean"] <= interval["p90"] + 1


def test_final_game_shows_results_and_dnps(client, db_session, artifact_dir, no_network):  # noqa: F811
    _, _, vet, _, game, _ = _frozen_world(db_session)
    game.status = "final"
    template = db_session.query(NbaPlayerGameLog).first()
    db_session.add(NbaPlayerGameLog(player_id=vet.id, game_id=game.id, team_id=template.team_id, opponent_team_id=template.opponent_team_id, is_home=True, source="test",
                                    recorded_at=TIP + timedelta(hours=3), minutes=30.0, points=20, rebounds=5, assists=4, did_not_play=False))
    db_session.commit()
    body = client.get(f"/api/nba/stat-predictions/games/{game.id}").json()
    players = [p for t in (body["home"], body["away"]) for p in t["players"]]
    vet_row = next(p for p in players if p["player_id"] == vet.id)
    assert vet_row["result"] == {"played": True, "minutes": 30.0, "points": 20, "rebounds": 5, "assists": 4}
    assert all(p["result"]["played"] is False for p in players if p["player_id"] != vet.id)


def test_game_without_predictions_is_empty_and_unknown_game_404(client, db_session, artifact_dir, no_network):  # noqa: F811
    _, _, _, _, game, _ = _world_with_stats(db_session)
    body = client.get(f"/api/nba/stat-predictions/games/{game.id}").json()
    assert body["rows"] == 0 and body["windows"] == [] and body["home"]["players"] == [] and body["away"]["players"] == []
    assert client.get("/api/nba/stat-predictions/games/999999").status_code == 404


def test_response_has_no_odds_or_recommendations(client, db_session, artifact_dir, no_network):  # noqa: F811
    _, _, _, _, game, _ = _frozen_world(db_session)
    text = client.get(f"/api/nba/stat-predictions/games/{game.id}").text.lower()
    for word in ("odds", "price", "edge", "stake", "bet", "line", "recommend", "value"):
        assert f'"{word}' not in text


def test_interval_matches_the_stored_distribution():
    mean, alpha = 6.0, 0.2
    low, high = nb_interval(mean, alpha)
    assert sm.cdf(low, [mean], alpha)[0] >= 0.1 > sm.cdf(low - 1, [mean], alpha)[0]
    assert sm.cdf(high, [mean], alpha)[0] >= 0.9 > sm.cdf(high - 1, [mean], alpha)[0]
    assert nb_interval(0.0, alpha)[0] == 0


def test_endpoint_is_read_only(client, db_session, artifact_dir, no_network):  # noqa: F811
    _, _, _, _, game, _ = _frozen_world(db_session)
    before = db_session.query(NbaPropProjection).count()
    client.get(f"/api/nba/stat-predictions/games/{game.id}")
    db_session.expire_all()
    assert db_session.query(NbaPropProjection).count() == before
    assert client.post(f"/api/nba/stat-predictions/games/{game.id}").status_code == 405

