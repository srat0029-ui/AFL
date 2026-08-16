"""API-level tests for the team-selection stage's new endpoints — Section
16: bulk-apply, suggested-roster, lineup summary/announcement-state,
lineup-status gating on Player Insights, and confirmed-out exclusion from
Prop Insights.
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
    PlayerMatchStat,
    PlayerModelRun,
    PlayerPropMarket,
    Round,
    Season,
    Sport,
    Team,
)
from app.player_modelling.market import PlayerMarket

BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _seed_match_with_players(db, n_per_team=2):
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
    match = Match(sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id, scheduled_start=BASE, status=MatchStatus.SCHEDULED)
    db.add(match)
    db.flush()

    completed_round = Round(season_id=season.id, round_number=0)
    db.add(completed_round)
    db.flush()
    completed = Match(sport_id=sport.id, season_id=season.id, round_id=completed_round.id, home_team_id=home.id, away_team_id=away.id, scheduled_start=datetime(2026, 7, 1, tzinfo=timezone.utc), status=MatchStatus.COMPLETED, home_score=80, away_score=70)
    db.add(completed)
    db.flush()

    players = []
    for i in range(n_per_team):
        p = Player(sport_id=sport.id, display_name=f"Home Player {i}", source="afltables", source_player_id=f"players/H/P{i}.html", current_team_id=home.id)
        db.add(p)
        db.flush()
        db.add(PlayerMatchStat(player_id=p.id, match_id=completed.id, team_id=home.id, opponent_team_id=away.id, source="afltables", recorded_at=completed.scheduled_start, disposals=15))
        players.append(p)
    db.commit()
    return match, home, away, players


def _ensure_promoted_models(db):
    now = datetime.now(timezone.utc)
    if db.scalar(select(PlayerModelRun).where(PlayerModelRun.model_name == "disposals_ridge")) is None:
        db.add(PlayerModelRun(model_name="disposals_ridge", market=PlayerMarket.DISPOSALS.value, feature_names=[], config_json={}, distribution_method="nb", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019, evaluation_end_year=2025, is_promoted=True, run_at=now))
    if db.scalar(select(GoalModelRun).where(GoalModelRun.model_name == "goals_hurdle")) is None:
        db.add(GoalModelRun(model_name="goals_hurdle", market=PlayerMarket.GOALS.value, feature_names=[], config_json={}, distribution_kind="hurdle", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019, evaluation_end_year=2025, is_promoted=True, run_at=now))
    db.commit()


def _seed_projection_and_lineup(db, match, player, team, selection_status="confirmed_selected", is_confirmed=True):
    _ensure_promoted_models(db)
    now = datetime.now(timezone.utc)
    db.add(PlayerDisposalProjection(match_id=match.id, player_id=player.id, team_id=team.id, model_name="disposals_ridge", model_version="v1", generated_at=now, data_cutoff=now, lineup_status_at_generation="expected_in", games_of_history=40, predicted_mean=20.0, distribution_method="nb", nb_alpha=3.0, confidence_tier="higher_confidence", warnings=[], input_features={}))
    db.add(PlayerGoalProjection(match_id=match.id, player_id=player.id, team_id=team.id, model_name="goals_hurdle", model_version="v1", generated_at=now, data_cutoff=now, lineup_status_at_generation="expected_in", games_of_history=40, predicted_mean=1.0, distribution_kind="hurdle", p_score=0.5, mu_scored=1.8, alpha_scored=1.0, scoring_archetype="regular", confidence_tier="higher_confidence", warnings=[], input_features={}))
    db.add(ExpectedLineup(match_id=match.id, player_id=player.id, team_id=team.id, status="expected_in" if is_confirmed else "uncertain", selection_status=selection_status, is_confirmed=is_confirmed, recorded_at=now, source="manual"))
    db.commit()


# --- suggested roster ---


def test_suggested_roster_returns_recent_players(client, db_session):
    match, home, away, players = _seed_match_with_players(db_session)
    resp = client.get(f"/api/afl/matches/{match.id}/lineup/suggested-roster?team_id={home.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert {p["display_name"] for p in body} == {"Home Player 0", "Home Player 1"}


def test_suggested_roster_rejects_team_not_in_match(client, db_session):
    match, home, away, players = _seed_match_with_players(db_session)
    other = Team(sport_id=home.sport_id, name="Other", short_name="OTH")
    db_session.add(other)
    db_session.commit()
    resp = client.get(f"/api/afl/matches/{match.id}/lineup/suggested-roster?team_id={other.id}")
    assert resp.status_code == 400


# --- bulk apply ---


def test_bulk_apply_creates_multiple_lineup_rows(client, db_session):
    match, home, away, players = _seed_match_with_players(db_session)
    resp = client.post(
        f"/api/afl/matches/{match.id}/lineup/bulk-apply",
        json={"entries": [{"player_id": p.id, "team_id": home.id, "selection_status": "placeholder"} for p in players]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert sorted(body["created"]) == sorted(p.id for p in players)

    rows = db_session.scalars(select(ExpectedLineup).where(ExpectedLineup.match_id == match.id)).all()
    assert len(rows) == len(players)
    assert all(r.selection_status == "placeholder" and r.is_manual_override is False for r in rows)


def test_bulk_apply_preserves_manual_override_by_default(client, db_session):
    match, home, away, players = _seed_match_with_players(db_session)
    player = players[0]
    # a direct single-player PUT sets is_manual_override=True
    client.put(f"/api/afl/matches/{match.id}/lineup/{player.id}", json={"player_id": player.id, "team_id": home.id, "status": "expected_out", "selection_status": "confirmed_out"})

    resp = client.post(
        f"/api/afl/matches/{match.id}/lineup/bulk-apply",
        json={"entries": [{"player_id": player.id, "team_id": home.id, "selection_status": "confirmed_selected"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["skipped_manual_override"] == [player.id]

    row = db_session.scalar(select(ExpectedLineup).where(ExpectedLineup.player_id == player.id))
    assert row.selection_status == "confirmed_out"  # unchanged by the bulk write


def test_bulk_apply_is_idempotent(client, db_session):
    match, home, away, players = _seed_match_with_players(db_session)
    entries = [{"player_id": p.id, "team_id": home.id, "selection_status": "confirmed_selected"} for p in players]
    r1 = client.post(f"/api/afl/matches/{match.id}/lineup/bulk-apply", json={"entries": entries})
    r2 = client.post(f"/api/afl/matches/{match.id}/lineup/bulk-apply", json={"entries": entries})
    assert r1.status_code == 200 and r2.status_code == 200
    assert sorted(r1.json()["created"]) == sorted(p.id for p in players)
    assert r2.json()["created"] == []
    assert sorted(r2.json()["updated"]) == sorted(p.id for p in players)
    rows = db_session.scalars(select(ExpectedLineup).where(ExpectedLineup.match_id == match.id)).all()
    assert len(rows) == len(players)  # no duplicates


# --- lineup summary ---


def test_lineup_summary_reflects_announcement_state(client, db_session):
    match, home, away, players = _seed_match_with_players(db_session)
    resp = client.get(f"/api/afl/matches/{match.id}/lineup/summary")
    assert resp.status_code == 200
    assert resp.json()["announcement_state"] == "teams_not_announced"

    client.post(f"/api/afl/matches/{match.id}/lineup/bulk-apply", json={"entries": [{"player_id": players[0].id, "team_id": home.id, "selection_status": "confirmed_selected"}]})
    resp2 = client.get(f"/api/afl/matches/{match.id}/lineup/summary")
    body = resp2.json()
    assert body["announcement_state"] == "final_team_confirmed"
    assert body["n_confirmed_selected"] == 1


# --- Player Insights lineup gating ---


def test_upcoming_projections_confirmed_only_excludes_unconfirmed(client, db_session):
    match, home, away, players = _seed_match_with_players(db_session)
    _seed_projection_and_lineup(db_session, match, players[0], home, selection_status="confirmed_selected", is_confirmed=True)
    _seed_projection_and_lineup(db_session, match, players[1], home, selection_status="named_in_squad", is_confirmed=False)

    all_resp = client.get("/api/afl/player-projections/upcoming?market=player_disposals&lineup_filter=include_uncertain")
    confirmed_resp = client.get("/api/afl/player-projections/upcoming?market=player_disposals&lineup_filter=confirmed_only")
    assert len(all_resp.json()["disposals"]) == 2
    assert len(confirmed_resp.json()["disposals"]) == 1
    assert confirmed_resp.json()["disposals"][0]["player_id"] == players[0].id


def test_upcoming_projections_always_excludes_confirmed_out(client, db_session):
    match, home, away, players = _seed_match_with_players(db_session)
    _seed_projection_and_lineup(db_session, match, players[0], home, selection_status="confirmed_out", is_confirmed=False)

    resp = client.get("/api/afl/player-projections/upcoming?market=player_disposals&lineup_filter=include_uncertain")
    assert resp.json()["disposals"] == []


# --- Prop Insights gating ---


def test_prop_insights_excludes_confirmed_out(client, db_session):
    match, home, away, players = _seed_match_with_players(db_session)
    _seed_projection_and_lineup(db_session, match, players[0], home, selection_status="confirmed_out", is_confirmed=False)
    bookmaker_resp = client.post(f"/api/afl/matches/{match.id}/player-props", json={"bookmaker_name": "TAB", "player_id": players[0].id, "market_type": "player_disposals", "line_type": "over_under", "threshold": 19.5, "price_decimal": 1.9})
    assert bookmaker_resp.status_code == 201

    resp = client.get("/api/afl/prop-insights")
    assert resp.json() == []


def test_prop_insights_include_uncertain_toggle(client, db_session):
    match, home, away, players = _seed_match_with_players(db_session)
    _seed_projection_and_lineup(db_session, match, players[0], home, selection_status="named_in_squad", is_confirmed=False)
    client.post(f"/api/afl/matches/{match.id}/player-props", json={"bookmaker_name": "TAB", "player_id": players[0].id, "market_type": "player_disposals", "line_type": "over_under", "threshold": 19.5, "price_decimal": 1.9})

    with_uncertain = client.get("/api/afl/prop-insights?include_uncertain=true")
    without_uncertain = client.get("/api/afl/prop-insights?include_uncertain=false")
    assert len(with_uncertain.json()) == 1
    assert len(without_uncertain.json()) == 0
