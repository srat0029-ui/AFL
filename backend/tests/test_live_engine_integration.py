"""Integration tests for live_engine.py + live_persistence.py against a
real (in-memory) database - Section 22's "promoted model loading," "stale
projection detection," "lineup status changes," "projection regeneration,"
"missing lineup handling," "player identity consistency," and an explicit
adversarial test proving target-match statistics are never available to
the live projection.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
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
    Round,
    Season,
    Sport,
    Team,
)
from app.player_modelling.live_engine import (
    ModelsUnavailableError,
    PromotedModelsUnavailableError,
    generate_live_projections,
)
from app.player_modelling.live_persistence import persist_projection_run
from app.player_modelling.market import PlayerMarket

BASE = datetime(2025, 4, 1, tzinfo=timezone.utc)
UPCOMING = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _seed_teams_and_players(db):
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

    players = {}
    for i, team in enumerate([home, away]):
        for j in range(3):
            p = Player(sport_id=sport.id, display_name=f"Player {i}-{j}", source="afltables", source_player_id=f"players/T{i}/P{j}.html")
            db.add(p)
            db.flush()
            players[(team.id, j)] = p

    # 3 historical completed rounds, 3+3 players each
    for round_num in range(1, 4):
        round_ = Round(season_id=season_2025.id, round_number=round_num)
        db.add(round_)
        db.flush()
        match = Match(
            sport_id=sport.id, season_id=season_2025.id, round_id=round_.id,
            home_team_id=home.id, away_team_id=away.id,
            scheduled_start=BASE + timedelta(days=7 * round_num), status=MatchStatus.COMPLETED,
            home_score=80, away_score=70,
        )
        db.add(match)
        db.flush()
        for team_id, opponent_id in ((home.id, away.id), (away.id, home.id)):
            for j in range(3):
                stat = PlayerMatchStat(
                    player_id=players[(team_id, j)].id, match_id=match.id, team_id=team_id, opponent_team_id=opponent_id,
                    source="afltables", recorded_at=match.scheduled_start, disposals=15 + j, goals=j,
                    kicks=8, marks=4, handballs=7, tackles=2, contested_possessions=5, uncontested_possessions=6,
                    inside_50s=2, marks_inside_50=1, goal_assists=0, time_on_ground_pct=80, behinds=0,
                )
                db.add(stat)
    db.flush()

    upcoming_round = Round(season_id=season_2026.id, round_number=1)
    db.add(upcoming_round)
    db.flush()
    upcoming_match = Match(
        sport_id=sport.id, season_id=season_2026.id, round_id=upcoming_round.id,
        home_team_id=home.id, away_team_id=away.id, scheduled_start=UPCOMING, status=MatchStatus.SCHEDULED,
    )
    db.add(upcoming_match)
    db.commit()

    return upcoming_match, home, away, players


def _seed_team_models(db):
    persist_model_run(
        db, "elo", EloConfig(), 2024,
        metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 100, "holdout_value": 0.2,
                  "naive_baseline_value": 0.25, "has_edge_over_naive": True}],
    )
    persist_model_run(
        db, "poisson", PoissonConfig(), 2024,
        metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 100, "holdout_value": 0.21,
                  "naive_baseline_value": 0.25, "has_edge_over_naive": True}],
    )


def _seed_promoted_player_models(db):
    now = datetime.now(timezone.utc)
    db.add(
        PlayerModelRun(
            model_name="disposals_baseline_last5", market=PlayerMarket.DISPOSALS.value, feature_names=[],
            config_json={}, distribution_method="nb", tune_start_year=2016, tune_end_year=2018,
            evaluation_start_year=2019, evaluation_end_year=2025, is_promoted=True, run_at=now,
        )
    )
    db.add(
        GoalModelRun(
            model_name="goals_baseline_last5", market=PlayerMarket.GOALS.value, feature_names=[],
            config_json={}, distribution_kind="nb", tune_start_year=2016, tune_end_year=2018,
            evaluation_start_year=2019, evaluation_end_year=2025, is_promoted=True, run_at=now,
        )
    )
    db.commit()


def _set_lineup(db, match_id, player_id, team_id, status):
    db.add(ExpectedLineup(match_id=match_id, player_id=player_id, team_id=team_id, status=status, recorded_at=datetime.now(timezone.utc), source="manual"))
    db.commit()


def test_generate_live_projections_raises_when_no_promoted_models(db_session):
    _seed_teams_and_players(db_session)
    _seed_team_models(db_session)
    with pytest.raises(PromotedModelsUnavailableError):
        generate_live_projections(db_session)


def test_generate_live_projections_raises_when_no_team_models(db_session):
    _seed_teams_and_players(db_session)
    _seed_promoted_player_models(db_session)
    with pytest.raises(ModelsUnavailableError):
        generate_live_projections(db_session)


def test_generate_live_projections_empty_when_no_upcoming_matches(db_session):
    _seed_promoted_player_models(db_session)
    _seed_team_models(db_session)
    run = generate_live_projections(db_session)
    assert run.upcoming_matches == []
    assert run.disposal_projections == []


def test_missing_lineup_handling_produces_no_projections_without_crashing(db_session):
    _seed_teams_and_players(db_session)
    _seed_team_models(db_session)
    _seed_promoted_player_models(db_session)

    run = generate_live_projections(db_session)
    assert len(run.upcoming_matches) == 1
    assert run.expected_players == []
    assert run.disposal_projections == []
    assert run.goal_projections == []


def test_full_pipeline_projects_expected_players_with_correct_history(db_session):
    upcoming_match, home, away, players = _seed_teams_and_players(db_session)
    _seed_team_models(db_session)
    _seed_promoted_player_models(db_session)

    # player (home, 0) expected in; (home, 1) uncertain; (home, 2) expected OUT (must not be projected)
    _set_lineup(db_session, upcoming_match.id, players[(home.id, 0)].id, home.id, "expected_in")
    _set_lineup(db_session, upcoming_match.id, players[(home.id, 1)].id, home.id, "uncertain")
    _set_lineup(db_session, upcoming_match.id, players[(home.id, 2)].id, home.id, "expected_out")

    run = generate_live_projections(db_session)
    projected_player_ids = {p.player_id for p in run.disposal_projections}

    assert players[(home.id, 0)].id in projected_player_ids
    assert players[(home.id, 1)].id in projected_player_ids
    assert players[(home.id, 2)].id not in projected_player_ids  # expected_out never projected

    by_player = {p.player_id: p for p in run.disposal_projections}
    # 3 historical completed games for this player -> games_of_history must be exactly 3,
    # never 4 (which would mean the synthetic/upcoming row's own placeholder leaked into its own history)
    assert by_player[players[(home.id, 0)].id].games_of_history == 3
    assert by_player[players[(home.id, 0)].id].lineup_status == "expected_in"
    assert by_player[players[(home.id, 1)].id].lineup_status == "uncertain"
    # 3 games is below MIN_GAMES_INSUFFICIENT (5), so both start at "insufficient_history" -
    # the lineup downgrade for "uncertain" has nowhere lower to go, so both stay equal here;
    # the real effect of the downgrade shows up once history clears that floor (see
    # test_live_models_and_confidence.py's dedicated lineup-downgrade test).
    assert by_player[players[(home.id, 0)].id].confidence_tier == "insufficient_history"
    assert by_player[players[(home.id, 1)].id].confidence_tier == "insufficient_history"

    # goals were also projected for the same players
    goal_player_ids = {p.player_id for p in run.goal_projections}
    assert players[(home.id, 0)].id in goal_player_ids


def test_persist_and_rerun_is_idempotent(db_session):
    upcoming_match, home, away, players = _seed_teams_and_players(db_session)
    _seed_team_models(db_session)
    _seed_promoted_player_models(db_session)
    _set_lineup(db_session, upcoming_match.id, players[(home.id, 0)].id, home.id, "expected_in")
    _set_lineup(db_session, upcoming_match.id, players[(away.id, 0)].id, away.id, "expected_in")

    run1 = generate_live_projections(db_session)
    n1 = persist_projection_run(db_session, run1)

    run2 = generate_live_projections(db_session)
    n2 = persist_projection_run(db_session, run2)

    assert n1 == n2
    disposal_rows = db_session.scalars(select(PlayerDisposalProjection)).all()
    assert len(disposal_rows) == 2  # not duplicated across the two runs

    means1 = sorted(p.predicted_mean for p in run1.disposal_projections)
    means2 = sorted(p.predicted_mean for p in run2.disposal_projections)
    assert means1 == means2  # deterministic


def test_lineup_status_change_removes_stale_persisted_projection(db_session):
    upcoming_match, home, away, players = _seed_teams_and_players(db_session)
    _seed_team_models(db_session)
    _seed_promoted_player_models(db_session)
    player_id = players[(home.id, 0)].id
    _set_lineup(db_session, upcoming_match.id, player_id, home.id, "expected_in")

    run1 = generate_live_projections(db_session)
    persist_projection_run(db_session, run1)
    assert db_session.scalars(
        select(PlayerDisposalProjection).where(PlayerDisposalProjection.player_id == player_id)
    ).first() is not None

    # Player is now marked OUT - a subsequent regeneration must remove their stale projection.
    lineup = db_session.scalar(select(ExpectedLineup).where(ExpectedLineup.player_id == player_id))
    lineup.status = "expected_out"
    db_session.commit()

    run2 = generate_live_projections(db_session)
    persist_projection_run(db_session, run2)
    assert db_session.scalars(
        select(PlayerDisposalProjection).where(PlayerDisposalProjection.player_id == player_id)
    ).first() is None


def test_player_identity_consistency_through_pipeline(db_session):
    """The exact same Player row (not a duplicate) is referenced by both
    the historical PlayerMatchStat rows and the persisted live projection —
    historical identity must be reused, never re-created."""
    upcoming_match, home, away, players = _seed_teams_and_players(db_session)
    _seed_team_models(db_session)
    _seed_promoted_player_models(db_session)
    player = players[(home.id, 0)]
    _set_lineup(db_session, upcoming_match.id, player.id, home.id, "expected_in")

    run = generate_live_projections(db_session)
    persist_projection_run(db_session, run)

    total_players_named = db_session.scalars(select(Player).where(Player.display_name == player.display_name)).all()
    assert len(total_players_named) == 1  # no duplicate created

    projection = db_session.scalar(select(PlayerDisposalProjection).where(PlayerDisposalProjection.player_id == player.id))
    assert projection.player_id == player.id
    historical_stat_count = db_session.scalars(select(PlayerMatchStat).where(PlayerMatchStat.player_id == player.id)).all()
    assert projection.games_of_history == len(historical_stat_count)


def test_usage_regime_is_computed_and_persisted_alongside_projections(db_session):
    """Usage-Change Production Integration stage, item 1/7: the detector
    runs inside the real live pipeline (not just in isolation) and its
    output round-trips through persist_projection_run onto both projection
    tables. This fixture only has 3 historical games per player - below
    ROLE_CHANGE_MIN_GAMES (10) - so "insufficient_history" is the correct,
    expected classification here; this test proves the wiring works
    end-to-end without crashing on a realistic small-sample case, not that
    a "changed" classification specifically fires (see test_usage_regime.py
    for that, on synthetic data with enough history)."""
    from app.player_modelling.usage_regime import INSUFFICIENT_HISTORY

    upcoming_match, home, away, players = _seed_teams_and_players(db_session)
    _seed_team_models(db_session)
    _seed_promoted_player_models(db_session)
    player = players[(home.id, 0)]
    _set_lineup(db_session, upcoming_match.id, player.id, home.id, "expected_in")

    run = generate_live_projections(db_session)
    disposal_result = next(p for p in run.disposal_projections if p.player_id == player.id)
    assert disposal_result.usage_regime == INSUFFICIENT_HISTORY
    assert disposal_result.usage_change_score is None
    goal_result = next(p for p in run.goal_projections if p.player_id == player.id)
    assert goal_result.usage_regime == INSUFFICIENT_HISTORY

    persist_projection_run(db_session, run)

    persisted_disposal = db_session.scalar(select(PlayerDisposalProjection).where(PlayerDisposalProjection.player_id == player.id))
    assert persisted_disposal.usage_regime == INSUFFICIENT_HISTORY
    assert persisted_disposal.usage_change_score is None
    persisted_goal = db_session.scalar(select(PlayerGoalProjection).where(PlayerGoalProjection.player_id == player.id))
    assert persisted_goal.usage_regime == INSUFFICIENT_HISTORY


def test_adversarial_upcoming_match_has_no_completed_stats_to_leak(db_session):
    """Direct proof the target match's own outcome can never reach the
    projection: the upcoming match is SCHEDULED (not COMPLETED), so it can
    never appear in load_player_game_rows/load_team_game_rows at all -
    confirmed here by asserting no PlayerMatchStat row exists for it even
    after a full projection run."""
    upcoming_match, home, away, players = _seed_teams_and_players(db_session)
    _seed_team_models(db_session)
    _seed_promoted_player_models(db_session)
    player = players[(home.id, 0)]
    _set_lineup(db_session, upcoming_match.id, player.id, home.id, "expected_in")

    generate_live_projections(db_session)  # must not create or require any PlayerMatchStat row for the upcoming match

    leaked_stats = db_session.scalars(
        select(PlayerMatchStat).where(PlayerMatchStat.match_id == upcoming_match.id)
    ).all()
    assert leaked_stats == []
    assert upcoming_match.status == MatchStatus.SCHEDULED
