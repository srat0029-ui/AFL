"""API-level tests for GET /api/afl/best-opportunities/diversified — the
full schema-serialization round trip (dict -> DiversifiedOpportunityRead),
which the lower-level module tests don't exercise."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import (
    Bookmaker,
    ExpectedLineup,
    Match,
    MatchStatus,
    Player,
    PlayerDisposalProjection,
    PlayerModelRun,
    PlayerPropMarket,
    Round,
    Season,
    Sport,
    Team,
)
from app.player_modelling.market import PlayerMarket

NOW = datetime.now(timezone.utc)


def _seed_opportunity(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=5)
    home = Team(sport_id=sport.id, name="Collingwood", short_name="COL")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW + timedelta(days=1), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.flush()

    player = Player(sport_id=sport.id, display_name="Darcy Gardiner", source="afltables", source_player_id="p1", current_team_id=home.id)
    db.add(player)
    db.flush()

    run = PlayerModelRun(
        model_name="disposals_ridge", market=PlayerMarket.DISPOSALS.value, feature_names=[], config_json={},
        distribution_method="nb", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019,
        evaluation_end_year=2025, is_promoted=True, run_at=NOW,
    )
    db.add(run)
    db.add(
        PlayerDisposalProjection(
            match_id=match.id, player_id=player.id, team_id=home.id, model_name="disposals_ridge", model_version="v1",
            generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
            predicted_mean=28.0, distribution_method="nb", nb_alpha=3.0, confidence_tier="higher_confidence",
            warnings=[], input_features={"disposals_last5_avg": 28.0},
        )
    )
    db.add(
        ExpectedLineup(
            match_id=match.id, player_id=player.id, team_id=home.id, status="expected_in",
            selection_status="confirmed_selected", is_confirmed=True, recorded_at=NOW, source="manual",
        )
    )
    bookmaker = Bookmaker(name="SportsBet")
    db.add(bookmaker)
    db.flush()
    for threshold, price in [(10.5, 3.5), (11.5, 4.0)]:
        db.add(
            PlayerPropMarket(
                match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
                line_type="over_under", threshold=threshold, selection="over", price_decimal=price,
                recorded_at=NOW, source="the_odds_api",
            )
        )
    db.commit()
    return match, player


def test_diversified_endpoint_returns_valid_schema(client, db_session):
    _seed_opportunity(db_session)
    resp = client.get("/api/afl/best-opportunities/diversified", params={"include_uncertain": True})
    assert resp.status_code == 200
    body = resp.json()
    assert "opportunities" in body
    assert "summary" in body
    assert "bookmaker_coverage" in body
    assert len(body["opportunities"]) == 1
    opp = body["opportunities"][0]
    assert opp["player_name"] == "Darcy Gardiner"
    assert len(opp["alternate_lines"]) == 1


def test_diversified_endpoint_summary_reflects_gated_universe(client, db_session):
    _seed_opportunity(db_session)
    resp = client.get("/api/afl/best-opportunities/diversified", params={"include_uncertain": True})
    body = resp.json()
    assert body["summary"]["round_number"] == 5
    assert body["summary"]["n_opportunities_passing_gates"] == 2  # both raw lines, before family collapsing
    assert body["summary"]["n_unique_players"] == 1
    assert body["summary"]["n_unique_matches"] == 1


def test_diversified_endpoint_view_disposals_only(client, db_session):
    _seed_opportunity(db_session)
    resp = client.get("/api/afl/best-opportunities/diversified", params={"view": "disposals", "include_uncertain": True})
    assert resp.status_code == 200
    body = resp.json()
    assert all(o["market_type"] == "player_disposals" for o in body["opportunities"])


def test_diversified_endpoint_view_goals_empty_when_none_exist(client, db_session):
    _seed_opportunity(db_session)
    resp = client.get("/api/afl/best-opportunities/diversified", params={"view": "goals", "include_uncertain": True})
    assert resp.status_code == 200
    assert resp.json()["opportunities"] == []


def test_diversified_endpoint_default_gates_exclude_uncertain_participation(client, db_session):
    match, player = _seed_opportunity(db_session)
    lineup = db_session.scalar(select(ExpectedLineup).where(ExpectedLineup.player_id == player.id))
    lineup.is_confirmed = False
    lineup.selection_status = "uncertain"
    db_session.commit()

    resp = client.get("/api/afl/best-opportunities/diversified")
    assert resp.json()["opportunities"] == []


def test_diversified_endpoint_bookmaker_coverage_present(client, db_session):
    _seed_opportunity(db_session)
    resp = client.get("/api/afl/best-opportunities/diversified", params={"include_uncertain": True})
    coverage = resp.json()["bookmaker_coverage"]
    assert any(c["bookmaker_name"] == "SportsBet" for c in coverage)
