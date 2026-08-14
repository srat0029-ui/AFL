"""Explicit, literal tests for the two guarantees the whole backtesting
system depends on: (1) a prediction can never be influenced by a match that
happens after it, and (2) re-running the exact same walk-forward with the
exact same inputs produces byte-identical results (required for backtest
reproducibility — see the Stage brief's "two runs, same dataset and
parameters, should produce identical results").

app/modelling/elo_backtest.py already has an equivalent check
(test_elo_backtest.py::test_no_leakage_ratings_only_reflect_strictly_earlier_matches),
proven by comparing before/after ratings within a single run. This file adds
the more direct, literal version the brief asks for: take an *existing*
walk-forward result, then re-run with one additional future match appended,
and assert every earlier prediction is untouched — a future match literally
being present in the input must not change the past.
"""

from datetime import datetime, timezone

from app.modelling.baselines import (
    always_home_baseline,
    historical_home_win_rate_baseline,
    simple_form_baseline,
)
from app.modelling.elo import EloConfig
from app.modelling.elo_backtest import run_walk_forward as elo_walk_forward
from app.modelling.poisson_backtest import run_walk_forward as poisson_walk_forward
from app.modelling.poisson_model import PoissonConfig
from app.modelling.types import MatchResult


def _match(match_id, year, month, day, home, away, home_score, away_score, goals_behinds=None) -> MatchResult:
    hg, hb, ag, ab = goals_behinds or (None, None, None, None)
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


def _base_matches() -> list[MatchResult]:
    return [
        _match(1, 2024, 3, 1, 1, 2, 100, 50, (14, 16, 6, 14)),
        _match(2, 2024, 3, 8, 3, 4, 80, 90, (11, 14, 12, 18)),
        _match(3, 2024, 3, 15, 1, 3, 70, 65, (9, 16, 8, 17)),
        _match(4, 2024, 3, 22, 2, 4, 60, 88, (7, 18, 12, 16)),
    ]


def _future_match() -> MatchResult:
    # scheduled strictly after every match in _base_matches()
    return _match(5, 2024, 4, 1, 1, 4, 200, 10, (30, 20, 1, 4))


def test_elo_predictions_for_past_matches_unchanged_by_appending_a_future_match():
    matches = _base_matches()
    config = EloConfig()

    baseline_run = elo_walk_forward(matches, config)
    extended_run = elo_walk_forward(matches + [_future_match()], config)

    baseline_by_id = {p.match_id: p for p in baseline_run}
    extended_by_id = {p.match_id: p for p in extended_run}

    for match_id in (m.match_id for m in matches):
        assert extended_by_id[match_id] == baseline_by_id[match_id], (
            f"prediction for match {match_id} changed after appending a future match — leakage"
        )


def test_poisson_predictions_for_past_matches_unchanged_by_appending_a_future_match():
    matches = _base_matches()
    config = PoissonConfig()

    baseline_run = poisson_walk_forward(matches, config)
    extended_run = poisson_walk_forward(matches + [_future_match()], config)

    baseline_by_id = {p.match_id: p for p in baseline_run}
    extended_by_id = {p.match_id: p for p in extended_run}

    for match_id in (m.match_id for m in matches):
        assert extended_by_id[match_id] == baseline_by_id[match_id], (
            f"prediction for match {match_id} changed after appending a future match — leakage"
        )


def test_baselines_for_past_matches_unchanged_by_appending_a_future_match():
    matches = _base_matches()
    future = _future_match()

    for baseline_fn in (always_home_baseline, historical_home_win_rate_baseline, simple_form_baseline):
        baseline_run = baseline_fn(matches)
        extended_run = baseline_fn(matches + [future])
        baseline_by_id = {p.match_id: p for p in baseline_run}
        extended_by_id = {p.match_id: p for p in extended_run}
        for match_id in (m.match_id for m in matches):
            assert extended_by_id[match_id] == baseline_by_id[match_id], (
                f"{baseline_fn.__name__} changed a past prediction after appending a future match"
            )


def test_elo_walk_forward_is_deterministic_across_reruns():
    matches = _base_matches()
    config = EloConfig()

    run_1 = elo_walk_forward(matches, config)
    run_2 = elo_walk_forward(matches, config)

    assert run_1 == run_2


def test_poisson_walk_forward_is_deterministic_across_reruns():
    matches = _base_matches()
    config = PoissonConfig()

    run_1 = poisson_walk_forward(matches, config)
    run_2 = poisson_walk_forward(matches, config)

    assert run_1 == run_2


def test_elo_walk_forward_is_deterministic_regardless_of_input_order():
    matches = _base_matches()
    config = EloConfig()

    forward_order = elo_walk_forward(matches, config)
    reversed_order = elo_walk_forward(list(reversed(matches)), config)

    assert forward_order == reversed_order


def test_baselines_are_deterministic_across_reruns():
    matches = _base_matches()
    for baseline_fn in (always_home_baseline, historical_home_win_rate_baseline, simple_form_baseline):
        assert baseline_fn(matches) == baseline_fn(matches)
