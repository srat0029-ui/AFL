from datetime import datetime, timezone

import pytest

from app.backtesting.evaluation import (
    EVALUATION_START_YEAR,
    build_scoring_evaluation,
    build_win_prob_evaluation,
    split_by_period,
)
from app.modelling.elo import EloConfig
from app.modelling.elo_backtest import run_walk_forward as elo_walk_forward
from app.modelling.poisson_backtest import run_walk_forward as poisson_walk_forward
from app.modelling.poisson_model import PoissonConfig
from app.modelling.types import MatchResult


def _match(match_id, year, home, away, home_score, away_score, month=3, day=1) -> MatchResult:
    hg, hb = home_score // 6, home_score % 6
    ag, ab = away_score // 6, away_score % 6
    return MatchResult(
        match_id=match_id,
        season_year=year,
        scheduled_start=datetime(year, month, day, tzinfo=timezone.utc),
        home_team_id=home,
        away_team_id=away,
        home_score=home_score,
        away_score=away_score,
        home_goals=hg,
        home_behinds=hb,
        away_goals=ag,
        away_behinds=ab,
    )


def _multi_season_matches(years) -> list[MatchResult]:
    matches = []
    match_id = 1
    for year in years:
        for round_ in range(1, 4):
            matches.append(_match(match_id, year, 1, 2, 90, 80, day=round_))
            match_id += 1
            matches.append(_match(match_id, year, 3, 4, 70, 95, day=round_))
            match_id += 1
    return matches


def test_split_by_period_separates_warmup_evaluation_and_current_season():
    matches = _multi_season_matches([2016, 2017, 2019, 2020, 2026])
    predictions = elo_walk_forward(matches, EloConfig())

    warmup, evaluation, current, period = split_by_period(predictions, evaluation_start_year=2019, current_year=2026)

    assert all(p.season_year < 2019 for p in warmup)
    assert all(2019 <= p.season_year < 2026 for p in evaluation)
    assert all(p.season_year >= 2026 for p in current)
    assert period.n_warmup == len(warmup)
    assert period.n_evaluation == len(evaluation)
    assert period.n_current_season == len(current)
    assert period.warmup_start_year == 2016
    assert period.evaluation_start_year == 2019
    assert period.current_season_year == 2026


def test_split_by_period_handles_empty_predictions():
    _warmup, _evaluation, _current, period = split_by_period([], evaluation_start_year=2019)
    assert period.n_warmup == 0
    assert period.n_evaluation == 0
    assert period.n_current_season == 0


def test_split_by_period_default_evaluation_start_year_is_2019():
    matches = _multi_season_matches([2018, 2019])
    predictions = elo_walk_forward(matches, EloConfig())
    _warmup, evaluation, _current, period = split_by_period(predictions, current_year=2030)
    assert period.evaluation_start_year == EVALUATION_START_YEAR
    assert all(p.season_year >= EVALUATION_START_YEAR for p in evaluation)


def test_win_prob_evaluation_excludes_warmup_from_evaluation_metrics():
    matches = _multi_season_matches([2016, 2017, 2020, 2021])
    predictions = elo_walk_forward(matches, EloConfig())

    report = build_win_prob_evaluation("elo", matches, predictions, evaluation_start_year=2020)

    # every by_season row must be from the evaluation period only
    assert all(int(seg.label) >= 2020 for seg in report.by_season)
    assert report.period.n_evaluation + report.period.n_warmup == len(predictions)


def test_win_prob_evaluation_baseline_comparison_uses_same_match_set_as_model():
    matches = _multi_season_matches([2019, 2020])
    predictions = elo_walk_forward(matches, EloConfig())

    report = build_win_prob_evaluation("elo", matches, predictions, evaluation_start_year=2019)

    names = {row.name for row in report.baseline_comparison}
    assert "elo" in names
    assert "baseline_always_home" in names
    assert "baseline_historical_home_rate" in names
    assert "baseline_simple_form" in names
    # every row scored over the identical evaluation-period match count
    ns = {row.n for row in report.baseline_comparison}
    assert len(ns) == 1


def test_win_prob_evaluation_calibration_and_ece_present():
    matches = _multi_season_matches([2019, 2020, 2021])
    predictions = elo_walk_forward(matches, EloConfig())
    report = build_win_prob_evaluation("elo", matches, predictions, evaluation_start_year=2019)

    assert report.calibration
    assert report.calibration_ece is None or report.calibration_ece >= 0.0


def test_scoring_evaluation_reports_interval_coverage():
    matches = _multi_season_matches([2019, 2020, 2021])
    predictions = poisson_walk_forward(matches, PoissonConfig())
    report = build_scoring_evaluation(matches, predictions, PoissonConfig(), evaluation_start_year=2019)

    assert "50pct" in report.interval_coverage
    assert "80pct" in report.interval_coverage
    assert 0.0 <= report.interval_coverage["80pct"]["total_hit_rate"] <= 1.0
    assert 0.0 <= report.interval_coverage["80pct"]["margin_hit_rate"] <= 1.0


def test_scoring_evaluation_metrics_include_bias():
    matches = _multi_season_matches([2019, 2020])
    predictions = poisson_walk_forward(matches, PoissonConfig())
    report = build_scoring_evaluation(matches, predictions, PoissonConfig(), evaluation_start_year=2019)

    assert "total_points_bias" in report.evaluation_metrics
    assert "margin_bias" in report.evaluation_metrics
