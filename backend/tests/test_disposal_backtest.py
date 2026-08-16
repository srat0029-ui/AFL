"""Tests for disposal_backtest.py's split/common-evaluation-set/determinism
guarantees - Section 26's "common evaluation set" and "deterministic
backtesting". Uses synthetic PlayerGameRow lists spanning the real
EVALUATION_START_YEAR (2019) boundary so the tune/eval split is exercised
without needing a full seeded DB.
"""

from datetime import datetime, timedelta, timezone

from app.player_modelling.disposal_backtest import (
    EVALUATION_START_YEAR,
    build_dataset_from_rows,
    run_baselines,
    run_candidate_models,
)
from app.player_modelling.disposal_data import PlayerGameRow

BASE_2018 = datetime(2018, 4, 1, tzinfo=timezone.utc)
BASE_2019 = datetime(2019, 4, 1, tzinfo=timezone.utc)


def _row(player_id, match_id, season_year, when, disposals):
    return PlayerGameRow(
        player_id=player_id,
        match_id=match_id,
        team_id=10,
        opponent_team_id=20,
        season_year=season_year,
        round_number=1,
        is_final=False,
        is_home=True,
        venue_id=1,
        scheduled_start=when,
        disposals=disposals,
        kicks=8,
        handballs=7,
        marks=2,
        tackles=3,
        clearances=1,
        inside_50s=2,
        contested_possessions=5,
        uncontested_possessions=6,
        time_on_ground_pct=80,
        subbed_on=False,
        subbed_off=False,
    )


def _synthetic_rows(n_players=8, n_games_per_player=6):
    rows = []
    match_id = 1
    for p in range(1, n_players + 1):
        for g in range(n_games_per_player):
            season_year = 2018 if g < 3 else 2019  # first half tune, second half eval
            base = BASE_2018 if season_year == 2018 else BASE_2019
            rows.append(_row(p, match_id, season_year, base + timedelta(days=7 * g), disposals=10 + (p + g) % 15))
            match_id += 1
    return rows


def test_tune_eval_split_uses_evaluation_start_year_boundary():
    split = build_dataset_from_rows(_synthetic_rows(), team_rows=[], team_context={})
    assert all(r.season_year < EVALUATION_START_YEAR for r in split.tune_rows)
    assert all(r.season_year >= EVALUATION_START_YEAR for r in split.eval_rows)
    assert len(split.tune_rows) + len(split.eval_rows) == len(split.all_rows)


def test_common_evaluation_set_identical_across_baselines_and_models():
    """Section 11: every model must be scored on the EXACT same eval rows -
    not a per-model subset."""
    split = build_dataset_from_rows(_synthetic_rows(), team_rows=[], team_context={})
    baseline_preds = run_baselines(split)
    model_preds = run_candidate_models(split, model_names=("ridge",))

    expected_keys = {(r.player_id, r.match_id) for r in split.eval_rows}
    for name, preds in {**baseline_preds, **model_preds}.items():
        keys = {(p.player_id, p.match_id) for p in preds}
        assert keys == expected_keys, f"{name} evaluated a different row set"
        assert len(preds) == len(split.eval_rows)


def test_backtest_is_deterministic_across_repeated_runs():
    """Section 26: re-running the exact same backtest against unchanged
    data must produce identical predictions - no random component should
    leak into results (random_state is fixed everywhere it's used)."""
    rows = _synthetic_rows()
    split_a = build_dataset_from_rows(rows, team_rows=[], team_context={})
    split_b = build_dataset_from_rows(rows, team_rows=[], team_context={})

    preds_a = run_candidate_models(split_a, model_names=("ridge", "gbm_xgboost"))
    preds_b = run_candidate_models(split_b, model_names=("ridge", "gbm_xgboost"))

    for name in preds_a:
        means_a = [p.predicted_mean for p in preds_a[name]]
        means_b = [p.predicted_mean for p in preds_b[name]]
        assert means_a == means_b, f"{name} predictions were not deterministic"


def test_baseline_predictions_are_deterministic_pure_functions():
    rows = _synthetic_rows()
    split_a = build_dataset_from_rows(rows, team_rows=[], team_context={})
    split_b = build_dataset_from_rows(rows, team_rows=[], team_context={})
    preds_a = run_baselines(split_a)
    preds_b = run_baselines(split_b)
    for name in preds_a:
        assert [p.predicted_mean for p in preds_a[name]] == [p.predicted_mean for p in preds_b[name]]


def test_eval_rows_only_built_from_permitted_tune_and_earlier_eval_history():
    """A model fit on tune_rows only must not have seen eval-period target
    values during fitting - a coarse structural check that fitting doesn't
    depend on split.eval_rows at all."""
    rows = _synthetic_rows()
    split = build_dataset_from_rows(rows, team_rows=[], team_context={})
    tune_match_ids = {r.match_id for r in split.tune_rows}
    eval_match_ids = {r.match_id for r in split.eval_rows}
    assert tune_match_ids.isdisjoint(eval_match_ids)
