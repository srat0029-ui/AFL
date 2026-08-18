"""API-level tests for the Market Integrity + Final Weekly Picks stage's
new endpoints: Final Shortlist, Model vs Market Disagreements, elite
disposal diagnostic, and bookmaker eligibility settings."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import (
    Bookmaker,
    ExpectedLineup,
    Match,
    MatchStatus,
    OddsQuote,
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


def _seed_base(db):
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
    db.commit()
    return match, home, away


def _seed_confirmed_player_opportunity(db, match, home):
    player = Player(sport_id=match.sport_id, display_name="Darcy Gardiner", source="afltables", source_player_id="p1", current_team_id=home.id)
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
            warnings=[], input_features={},
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
    db.add(
        PlayerPropMarket(
            match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
            line_type="over_under", threshold=10.5, selection="over", price_decimal=3.5,
            recorded_at=NOW, source="the_odds_api",
        )
    )
    db.commit()
    return player


def test_final_shortlist_endpoint_returns_valid_schema(client, db_session):
    match, home, away = _seed_base(db_session)
    _seed_confirmed_player_opportunity(db_session, match, home)

    resp = client.get("/api/afl/best-opportunities/final-shortlist")
    assert resp.status_code == 200
    body = resp.json()
    assert "opportunities" in body
    assert "excluded" in body
    assert "empty_state_reason" in body
    assert "any_confirmed_player_lineups" in body
    assert len(body["opportunities"]) == 1
    assert body["opportunities"][0]["player_name"] == "Darcy Gardiner"


def test_final_shortlist_endpoint_respects_limit_param(client, db_session):
    match, home, away = _seed_base(db_session)
    _seed_confirmed_player_opportunity(db_session, match, home)

    resp = client.get("/api/afl/best-opportunities/final-shortlist", params={"limit": 5})
    assert resp.status_code == 200
    assert len(resp.json()["opportunities"]) <= 5


def test_final_shortlist_endpoint_empty_state_when_no_data(client, db_session):
    resp = client.get("/api/afl/best-opportunities/final-shortlist")
    assert resp.status_code == 200
    body = resp.json()
    assert body["opportunities"] == []
    assert body["empty_state_reason"] is not None


def test_model_market_disagreements_endpoint(client, db_session):
    resp = client.get("/api/afl/model-vs-market-disagreements")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_elite_disposal_diagnostic_endpoint_returns_null_without_promoted_model(client, db_session):
    resp = client.get("/api/afl/elite-disposal-diagnostic")
    assert resp.status_code == 200
    assert resp.json() is None


def test_bookmaker_eligibility_list_endpoint(client, db_session):
    db_session.add(Bookmaker(name="TAB", provider_key="tab", is_exchange=False, eligibility="included"))
    db_session.add(Bookmaker(name="Betfair", provider_key="betfair_ex_au", is_exchange=True, eligibility="informational_only"))
    db_session.commit()

    resp = client.get("/api/bookmakers/eligibility")
    assert resp.status_code == 200
    body = resp.json()
    names = {b["name"] for b in body}
    assert {"TAB", "Betfair"} <= names
    betfair = next(b for b in body if b["name"] == "Betfair")
    assert betfair["is_exchange"] is True
    assert betfair["eligibility"] == "informational_only"


def test_bookmaker_eligibility_patch_endpoint(client, db_session):
    bookmaker = Bookmaker(name="TAB", provider_key="tab", is_exchange=False, eligibility="included")
    db_session.add(bookmaker)
    db_session.commit()

    resp = client.patch(f"/api/bookmakers/{bookmaker.id}/eligibility", json={"eligibility": "excluded"})
    assert resp.status_code == 200
    assert resp.json()["eligibility"] == "excluded"

    reloaded = db_session.scalar(select(Bookmaker).where(Bookmaker.id == bookmaker.id))
    assert reloaded.eligibility == "excluded"


def test_bookmaker_eligibility_patch_rejects_invalid_value(client, db_session):
    bookmaker = Bookmaker(name="TAB")
    db_session.add(bookmaker)
    db_session.commit()

    resp = client.patch(f"/api/bookmakers/{bookmaker.id}/eligibility", json={"eligibility": "bogus"})
    assert resp.status_code == 400


def test_bookmaker_eligibility_patch_404_for_missing_bookmaker(client, db_session):
    resp = client.patch("/api/bookmakers/999999/eligibility", json={"eligibility": "included"})
    assert resp.status_code == 404
