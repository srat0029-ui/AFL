"""Regression test: confirming a player's lineup status via the lineup
API must regenerate that match's projections immediately, so the
already-persisted "Expected lineup not confirmed" warning (frozen at
whatever the lineup looked like when projections were last generated -
see live_confidence.py) doesn't keep contradicting the now-live
is_confirmed/selection_status fields. No model retrain, no bookmaker API
call - just the existing generate_live_projections pipeline, scoped to
the one match that changed."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import ExpectedLineup, GoalModelRun, Match, MatchStatus, Player, PlayerDisposalProjection, PlayerMatchStat, PlayerModelRun, Round, Season, Sport, Team
from app.player_modelling.market import PlayerMarket

BASE = datetime(2025, 4, 1, tzinfo=timezone.utc)
UPCOMING = datetime.now(timezone.utc) + timedelta(days=3)


def _seed(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season_2025 = Season(sport_id=sport.id, year=2025)
    season_2026 = Season(sport_id=sport.id, year=2026)
    db.add_all([season_2025, season_2026])
    db.flush()
    home = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    away = Team(sport_id=sport.id, name="Richmond", short_name="RIC")
    db.add_all([home, away])
    db.flush()

    player = Player(sport_id=sport.id, display_name="Test Player", source="afltables", source_player_id="players/T/P.html", current_team_id=home.id)
    db.add(player)
    db.flush()

    for round_num in range(1, 4):
        round_ = Round(season_id=season_2025.id, round_number=round_num)
        db.add(round_)
        db.flush()
        match = Match(
            sport_id=sport.id, season_id=season_2025.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
            scheduled_start=BASE + timedelta(days=7 * round_num), status=MatchStatus.COMPLETED, home_score=80, away_score=70,
        )
        db.add(match)
        db.flush()
        db.add(PlayerMatchStat(
            player_id=player.id, match_id=match.id, team_id=home.id, opponent_team_id=away.id, source="afltables",
            recorded_at=match.scheduled_start, disposals=20, goals=1, kicks=10, marks=5, handballs=10, tackles=3,
            contested_possessions=6, uncontested_possessions=8, inside_50s=3, marks_inside_50=1, goal_assists=0,
            time_on_ground_pct=85, behinds=0,
        ))
    db.flush()

    upcoming_round = Round(season_id=season_2026.id, round_number=1)
    db.add(upcoming_round)
    db.flush()
    upcoming_match = Match(
        sport_id=sport.id, season_id=season_2026.id, round_id=upcoming_round.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=UPCOMING, status=MatchStatus.SCHEDULED,
    )
    db.add(upcoming_match)
    db.commit()

    persist_model_run(
        db, "elo", EloConfig(), 2024,
        metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 100, "holdout_value": 0.2, "naive_baseline_value": 0.25, "has_edge_over_naive": True}],
    )
    persist_model_run(
        db, "poisson", PoissonConfig(), 2024,
        metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 100, "holdout_value": 0.21, "naive_baseline_value": 0.25, "has_edge_over_naive": True}],
    )
    now = datetime.now(timezone.utc)
    db.add(PlayerModelRun(
        model_name="disposals_baseline_last5", market=PlayerMarket.DISPOSALS.value, feature_names=[], config_json={},
        distribution_method="nb", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019, evaluation_end_year=2025,
        is_promoted=True, run_at=now,
    ))
    db.add(GoalModelRun(
        model_name="goals_baseline_last5", market=PlayerMarket.GOALS.value, feature_names=[], config_json={},
        distribution_kind="nb", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019, evaluation_end_year=2025,
        is_promoted=True, run_at=now,
    ))
    db.commit()

    return upcoming_match, home, away, player


def test_confirming_lineup_regenerates_projection_and_clears_stale_warning(client, db_session):
    match, home, away, player = _seed(db_session)

    # First projection pass: player is only "uncertain" - live_confidence.py
    # bakes an "Expected lineup not confirmed" warning into the projection.
    response = client.put(
        f"/api/afl/matches/{match.id}/lineup/{player.id}",
        json={"player_id": player.id, "team_id": home.id, "status": "uncertain", "selection_status": "uncertain"},
    )
    assert response.status_code == 200

    proj = db_session.scalar(select(PlayerDisposalProjection).where(PlayerDisposalProjection.player_id == player.id))
    assert proj is not None
    assert any("not confirmed" in w.lower() for w in proj.warnings), "expected the baseline stale-lineup warning to exist first"

    # Now confirm the player - the PUT itself should regenerate the
    # projection so the warning clears in the SAME request/response cycle.
    response2 = client.put(
        f"/api/afl/matches/{match.id}/lineup/{player.id}",
        json={"player_id": player.id, "team_id": home.id, "status": "expected_in", "selection_status": "confirmed_selected"},
    )
    assert response2.status_code == 200

    db_session.expire_all()
    proj2 = db_session.scalar(select(PlayerDisposalProjection).where(PlayerDisposalProjection.player_id == player.id))
    assert proj2 is not None
    assert not any("not confirmed" in w.lower() for w in proj2.warnings), f"stale warning should be cleared, got {proj2.warnings}"
    assert proj2.lineup_status_at_generation == "expected_in"


def test_bulk_apply_also_regenerates_projections(client, db_session):
    match, home, away, player = _seed(db_session)

    # Seed directly (not via the single-player PUT, which marks the row a
    # manual override that bulk-apply would then correctly refuse to touch
    # - a different, already-tested behaviour, not what this test is about).
    db_session.add(ExpectedLineup(
        match_id=match.id, player_id=player.id, team_id=home.id, status="uncertain", selection_status="uncertain",
        is_confirmed=False, recorded_at=datetime.now(timezone.utc), source="manual",
    ))
    db_session.commit()
    client.post(f"/api/afl/matches/{match.id}/lineup/bulk-apply", json={"entries": [], "source": "seed"})  # no-op call just to trigger a regeneration pass via the API path
    proj = db_session.scalar(select(PlayerDisposalProjection).where(PlayerDisposalProjection.player_id == player.id))
    assert proj is not None
    assert any("not confirmed" in w.lower() for w in proj.warnings)

    response = client.post(
        f"/api/afl/matches/{match.id}/lineup/bulk-apply",
        json={"entries": [{"player_id": player.id, "team_id": home.id, "selection_status": "confirmed_selected"}], "source": "manual_tick_list"},
    )
    assert response.status_code == 200

    db_session.expire_all()
    proj2 = db_session.scalar(select(PlayerDisposalProjection).where(PlayerDisposalProjection.player_id == player.id))
    assert not any("not confirmed" in w.lower() for w in proj2.warnings)
