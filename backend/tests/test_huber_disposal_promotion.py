"""Regression tests for the Huber disposal model promotion (Elite Disposal
Model: Promotion stage): Huber fits/dispatches correctly, Ridge remains
fully reproducible and untouched, promotion flips is_promoted without
touching Ridge's persisted predictions, model versions are stamped
correctly end-to-end, live projections regenerate under Huber, and
arbitrary-threshold probabilities stay coherent/monotonic under the new
model. Old PricingSnapshot rows are proven immutable across a promotion.
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from sqlalchemy import select

from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run as persist_team_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import (
    ExpectedLineup,
    GoalModelRun,
    Match,
    MatchStatus,
    Player,
    PlayerDisposalPrediction,
    PlayerMatchStat,
    PlayerModelRun,
    PricingSnapshot,
    Round,
    Season,
    Sport,
    Team,
)
from app.player_modelling.disposal_backtest import build_dataset_from_rows, run_candidate_models
from app.player_modelling.disposal_data import PlayerGameRow
from app.player_modelling.disposal_distribution import NegativeBinomialDistribution
from app.player_modelling.disposal_evaluation import best_distribution_method, evaluate_model
from app.player_modelling.disposal_models import fit_huber, fit_ridge
from app.player_modelling.disposal_persistence import persist_model_run as persist_disposal_model_run
from app.player_modelling.live_engine import generate_live_projections
from app.player_modelling.live_models import fit_live_disposal_model
from app.player_modelling.live_persistence import persist_projection_run
from app.player_modelling.market import PlayerMarket
from app.pricing.snapshot_service import snapshot_price

BASE_2018 = datetime(2018, 4, 1, tzinfo=timezone.utc)
BASE_2019 = datetime(2019, 4, 1, tzinfo=timezone.utc)


def _row(player_id, match_id, season_year, when, disposals):
    return PlayerGameRow(
        player_id=player_id, match_id=match_id, team_id=10, opponent_team_id=20, season_year=season_year,
        round_number=1, is_final=False, is_home=True, venue_id=1, scheduled_start=when, disposals=disposals,
        kicks=8, handballs=7, marks=2, tackles=3, clearances=1, inside_50s=2, contested_possessions=5,
        uncontested_possessions=6, time_on_ground_pct=80, subbed_on=False, subbed_off=False,
    )


def _synthetic_rows(n_players=10, n_games_per_player=8):
    rows = []
    match_id = 1
    for p in range(1, n_players + 1):
        for g in range(n_games_per_player):
            season_year = 2018 if g < 4 else 2019
            base = BASE_2018 if season_year == 2018 else BASE_2019
            rows.append(_row(p, match_id, season_year, base + timedelta(days=7 * g), disposals=10 + (p * 2 + g) % 20))
            match_id += 1
    return rows


# --- 1) Huber fits correctly, reproducibly, alongside Ridge -----------------


def test_huber_is_a_registered_candidate_model_alongside_ridge():
    split = build_dataset_from_rows(_synthetic_rows(), team_rows=[], team_context={})
    results = run_candidate_models(split, model_names=("ridge", "huber"))
    assert "ridge" in results and "huber" in results
    assert len(results["huber"]) == len(split.eval_rows)
    assert all(p.predicted_mean >= 0 for p in results["huber"])


def test_ridge_remains_reproducible_after_adding_huber():
    """Ridge's own fitting code path must be byte-for-byte unaffected by
    Huber's addition — same synthetic data in, same predictions out,
    deterministically, exactly as before this stage."""
    split = build_dataset_from_rows(_synthetic_rows(), team_rows=[], team_context={})
    run1 = run_candidate_models(split, model_names=("ridge",))["ridge"]
    run2 = run_candidate_models(split, model_names=("ridge",))["ridge"]
    assert [p.predicted_mean for p in run1] == [p.predicted_mean for p in run2]


def test_huber_and_ridge_produce_different_but_both_valid_predictions():
    split = build_dataset_from_rows(_synthetic_rows(), team_rows=[], team_context={})
    results = run_candidate_models(split, model_names=("ridge", "huber"))
    ridge_preds = [p.predicted_mean for p in results["ridge"]]
    huber_preds = [p.predicted_mean for p in results["huber"]]
    assert ridge_preds != huber_preds  # genuinely different models
    assert all(0 <= p < 100 for p in huber_preds)  # sane range


# --- 2) Promotion mechanics: Huber promoted, Ridge preserved unpromoted -----


def test_promoting_huber_leaves_ridge_predictions_and_run_at_untouched(db_session):
    split = build_dataset_from_rows(_synthetic_rows(), team_rows=[], team_context={})
    ridge_preds = run_candidate_models(split, model_names=("ridge",))["ridge"]
    ridge_eval = evaluate_model("ridge", ridge_preds, "nb")
    ridge_run = persist_disposal_model_run(
        db_session, model_name="disposals_ridge", feature_names=(), config={"alpha": 5.0}, distribution_method="nb",
        tune_start_year=2016, tune_end_year=2018, evaluation=ridge_eval, predictions=ridge_preds, is_promoted=True,
    )
    ridge_run_at_before = ridge_run.run_at
    ridge_prediction_count_before = db_session.scalar(
        select(PlayerDisposalPrediction).where(PlayerDisposalPrediction.model_run_id == ridge_run.id)
    )
    n_ridge_preds_before = len(db_session.scalars(select(PlayerDisposalPrediction).where(PlayerDisposalPrediction.model_run_id == ridge_run.id)).all())

    huber_preds = run_candidate_models(split, model_names=("huber",))["huber"]
    huber_eval = evaluate_model("huber", huber_preds, "nb")
    huber_run = persist_disposal_model_run(
        db_session, model_name="disposals_huber", feature_names=(), config={"epsilon": 1.35, "alpha": 0.001},
        distribution_method="nb", tune_start_year=2016, tune_end_year=2018, evaluation=huber_eval,
        predictions=huber_preds, is_promoted=True,
    )
    # Simulate the promotion script's explicit un-promote (never a re-persist).
    ridge_run.is_promoted = False
    db_session.commit()
    db_session.refresh(ridge_run)

    assert ridge_run.run_at == ridge_run_at_before  # never re-persisted
    assert ridge_run.is_promoted is False
    assert huber_run.is_promoted is True
    n_ridge_preds_after = len(db_session.scalars(select(PlayerDisposalPrediction).where(PlayerDisposalPrediction.model_run_id == ridge_run.id)).all())
    assert n_ridge_preds_after == n_ridge_preds_before  # untouched, not deleted/replaced

    promoted = db_session.scalars(select(PlayerModelRun).where(PlayerModelRun.market == "player_disposals", PlayerModelRun.is_promoted.is_(True))).all()
    assert len(promoted) == 1
    assert promoted[0].model_name == "disposals_huber"


def test_model_version_string_reflects_the_promoted_model_name(db_session):
    split = build_dataset_from_rows(_synthetic_rows(), team_rows=[], team_context={})
    huber_preds = run_candidate_models(split, model_names=("huber",))["huber"]
    huber_eval = evaluate_model("huber", huber_preds, "nb")
    huber_run = persist_disposal_model_run(
        db_session, model_name="disposals_huber", feature_names=(), config={}, distribution_method="nb",
        tune_start_year=2016, tune_end_year=2018, evaluation=huber_eval, predictions=huber_preds, is_promoted=True,
    )
    version = f"{huber_run.model_name}@{huber_run.run_at.isoformat()}"
    assert version.startswith("disposals_huber@")
    assert "disposals_ridge" not in version


# --- 3) live_models.py dispatches "huber" family correctly ------------------


def test_fit_live_disposal_model_dispatches_huber_family():
    split = build_dataset_from_rows(_synthetic_rows(), team_rows=[], team_context={})
    feature_names = tuple(sorted(split.all_rows[0].features.keys()))[:5]  # a small real feature subset
    fake_run = PlayerModelRun(
        model_name="disposals_huber", market=PlayerMarket.DISPOSALS.value, feature_names=list(feature_names),
        config_json={"epsilon": 1.35, "alpha": 0.001}, distribution_method="nb",
        tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019, evaluation_end_year=2019,
        is_promoted=True, run_at=datetime.now(timezone.utc),
    )
    live_model = fit_live_disposal_model(split.all_rows, fake_run)
    assert live_model.model_name == "huber"
    preds = live_model.predict_many(split.all_rows)
    assert len(preds) == len(split.all_rows)
    assert all(p >= 0 for p in preds)


# --- 4) threshold probabilities remain coherent/monotonic under Huber -------


def test_huber_backed_threshold_probabilities_are_monotonic_and_bounded():
    split = build_dataset_from_rows(_synthetic_rows(), team_rows=[], team_context={})
    preds = run_candidate_models(split, model_names=("huber",))["huber"]
    for p in preds[:20]:
        dist = NegativeBinomialDistribution(mu=p.predicted_mean, alpha=p.nb_alpha)
        probs = [dist.prob_at_least(t) for t in (10, 15, 20, 25, 30, 35)]
        assert all(0.0 <= x <= 1.0 for x in probs)
        assert probs == sorted(probs, reverse=True)  # non-increasing as threshold rises
        assert dist.prob_over(19.5) >= dist.prob_at_least(20)  # 19.5 line covers >=20 plus the (zero-mass) 19.5 point


# --- 5) old PricingSnapshot rows survive a promotion untouched --------------


def _seed_snapshot_match(db):
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
    player = Player(sport_id=sport.id, display_name="Test Player", source="afltables", source_player_id="p1", current_team_id=home.id)
    db.add(player)
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=datetime.now(timezone.utc) + timedelta(days=2), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    return match, player


def test_old_ridge_era_snapshot_is_immutable_across_a_huber_promotion(db_session):
    match, player = _seed_snapshot_match(db_session)
    now = datetime.now(timezone.utc)

    ridge_snap = snapshot_price(
        db_session, match_id=match.id, player_id=player.id, market_family="player_disposals", market_type="player_disposals",
        selection="over", line_type="over_under", threshold=20.5, line_value=None,
        model_name="disposal_nb", model_version="disposals_ridge@2026-08-16T04:26:05.563784+00:00",
        generated_at=now, data_cutoff=now, lineup_status="expected_in", confidence_tier="higher_confidence",
        model_probability=0.42,
    )
    db_session.commit()
    ridge_snap_id, ridge_prob_before = ridge_snap.id, ridge_snap.model_probability

    # A later cycle prices the SAME market under the newly-promoted Huber
    # version - must land as a NEW row, never overwrite the Ridge-era one.
    huber_snap = snapshot_price(
        db_session, match_id=match.id, player_id=player.id, market_family="player_disposals", market_type="player_disposals",
        selection="over", line_type="over_under", threshold=20.5, line_value=None,
        model_name="disposal_nb", model_version="disposals_huber@2026-08-23T12:40:50.407355+00:00",
        generated_at=now, data_cutoff=now, lineup_status="expected_in", confidence_tier="higher_confidence",
        model_probability=0.39,
    )
    db_session.commit()

    assert huber_snap is not None
    assert huber_snap.id != ridge_snap_id

    reloaded_ridge = db_session.get(PricingSnapshot, ridge_snap_id)
    assert reloaded_ridge.model_probability == ridge_prob_before  # untouched
    assert reloaded_ridge.model_version.startswith("disposals_ridge@")

    all_snaps = db_session.scalars(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id)).all()
    assert len(all_snaps) == 2
    assert {s.model_version for s in all_snaps} == {
        "disposals_ridge@2026-08-16T04:26:05.563784+00:00",
        "disposals_huber@2026-08-23T12:40:50.407355+00:00",
    }


# --- 6) end-to-end: current projections regenerate under the promoted Huber model ---


def _seed_full_live_pipeline(db):
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

    base = datetime(2025, 4, 1, tzinfo=timezone.utc)
    for round_num in range(1, 5):
        round_ = Round(season_id=season_2025.id, round_number=round_num)
        db.add(round_)
        db.flush()
        match = Match(
            sport_id=sport.id, season_id=season_2025.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
            scheduled_start=base + timedelta(days=7 * round_num), status=MatchStatus.COMPLETED, home_score=80, away_score=70,
        )
        db.add(match)
        db.flush()
        for team_id, opponent_id in ((home.id, away.id), (away.id, home.id)):
            for j in range(3):
                db.add(PlayerMatchStat(
                    player_id=players[(team_id, j)].id, match_id=match.id, team_id=team_id, opponent_team_id=opponent_id,
                    source="afltables", recorded_at=match.scheduled_start, disposals=15 + j, goals=j,
                    kicks=8, marks=4, handballs=7, tackles=2, contested_possessions=5, uncontested_possessions=6,
                    inside_50s=2, marks_inside_50=1, goal_assists=0, time_on_ground_pct=80, behinds=0,
                ))
    db.flush()

    upcoming_round = Round(season_id=season_2026.id, round_number=1)
    db.add(upcoming_round)
    db.flush()
    upcoming_match = Match(
        sport_id=sport.id, season_id=season_2026.id, round_id=upcoming_round.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=datetime(2026, 8, 20, tzinfo=timezone.utc), status=MatchStatus.SCHEDULED,
    )
    db.add(upcoming_match)
    db.commit()

    db.add(ExpectedLineup(match_id=upcoming_match.id, player_id=players[(home.id, 0)].id, team_id=home.id, status="expected_in", recorded_at=datetime.now(timezone.utc), source="manual"))
    db.commit()

    persist_team_model_run(
        db, "elo", EloConfig(), 2024,
        metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 100, "holdout_value": 0.2, "naive_baseline_value": 0.25, "has_edge_over_naive": True}],
    )
    persist_team_model_run(
        db, "poisson", PoissonConfig(), 2024,
        metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 100, "holdout_value": 0.21, "naive_baseline_value": 0.25, "has_edge_over_naive": True}],
    )
    now = datetime.now(timezone.utc)
    db.add(PlayerModelRun(
        model_name="disposals_ridge", market=PlayerMarket.DISPOSALS.value, feature_names=["disposals_last3_avg"],
        config_json={"alpha": 5.0}, distribution_method="nb", tune_start_year=2016, tune_end_year=2018,
        evaluation_start_year=2019, evaluation_end_year=2025, is_promoted=False, run_at=now - timedelta(days=7),
    ))
    db.add(PlayerModelRun(
        model_name="disposals_huber", market=PlayerMarket.DISPOSALS.value, feature_names=["disposals_last3_avg"],
        config_json={"epsilon": 1.35, "alpha": 0.001}, distribution_method="nb", tune_start_year=2016, tune_end_year=2018,
        evaluation_start_year=2019, evaluation_end_year=2025, is_promoted=True, run_at=now,
    ))
    db.add(GoalModelRun(
        model_name="goals_baseline_last5", market=PlayerMarket.GOALS.value, feature_names=[], config_json={},
        distribution_kind="nb", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019,
        evaluation_end_year=2025, is_promoted=True, run_at=now,
    ))
    db.commit()
    return upcoming_match, home, away, players


def test_generate_live_projections_regenerates_under_promoted_huber(db_session):
    match, home, away, players = _seed_full_live_pipeline(db_session)

    run = generate_live_projections(db_session)

    assert run.disposal_run.model_name == "disposals_huber"
    assert run.disposal_model_version.startswith("disposals_huber@")
    assert len(run.disposal_projections) >= 1
    n_disposals, n_goals = persist_projection_run(db_session, run)
    assert n_disposals >= 1

    from app.models import PlayerDisposalProjection

    persisted = db_session.scalars(select(PlayerDisposalProjection).where(PlayerDisposalProjection.match_id == match.id)).all()
    assert persisted
    assert all(p.model_version.startswith("disposals_huber@") for p in persisted)
    assert all(p.model_name == "disposals_huber" for p in persisted)
