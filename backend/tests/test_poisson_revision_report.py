from datetime import datetime, timedelta, timezone

import pytest

from app.backtesting.evaluation import EvaluationPeriod, ModelsUnavailableError
from app.backtesting.poisson_revision_report import (
    PoissonVariantReport,
    RoundBandMetrics,
    build_poisson_revision_comparison,
    evaluate_poisson_promotion_rule,
)
from app.backtesting.segments import BacktestSegment
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import Match, MatchStatus, Round, Season, Sport, Team

_DUMMY_PERIOD = EvaluationPeriod(2016, 2018, 2019, 2025, 2026, 0, 0, 0)


def _variant(eval_mae, eval_brier, season_2021_mae, by_season_maes: dict[str, float]) -> PoissonVariantReport:
    return PoissonVariantReport(
        label="test",
        config=PoissonConfig(),
        period=_DUMMY_PERIOD,
        evaluation_metrics={"total_points_mae": eval_mae, "brier_score": eval_brier},
        warmup_metrics={},
        full_history_metrics={"total_points_mae": eval_mae},
        by_season=[BacktestSegment(label=season, n=10, metrics={"total_points_mae": mae}) for season, mae in by_season_maes.items()],
        early_season_bands=[],
        season_2021_bands=[RoundBandMetrics(label="full_season", n=6, metrics={"total_points_mae": season_2021_mae})],
        interval_coverage={},
    )


def _seed_matches(db_session, years):
    """6 rounds per season, goals/behinds present on every match (required
    for the Poisson model) — round_number is set from the Round row, the
    same way app/modelling/data_loading.py's load_completed_matches derives
    it for real data, so this exercises the real join path."""
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()

    teams = {}
    for name in ["Carlton", "Richmond", "Geelong", "Essendon"]:
        team = Team(sport_id=sport.id, name=name, short_name=name[:3].upper())
        db_session.add(team)
        teams[name] = team
    db_session.flush()

    matches = []
    for year in years:
        season = Season(sport_id=sport.id, year=year)
        db_session.add(season)
        db_session.flush()
        match_date = datetime(year, 3, 1, tzinfo=timezone.utc)
        for i in range(6):
            round_ = Round(season_id=season.id, round_number=i + 1)
            db_session.add(round_)
            db_session.flush()
            home, away = (teams["Carlton"], teams["Richmond"]) if i % 2 == 0 else (teams["Geelong"], teams["Essendon"])
            match = Match(
                sport_id=sport.id, season_id=season.id, round_id=round_.id,
                home_team_id=home.id, away_team_id=away.id,
                scheduled_start=match_date, status=MatchStatus.COMPLETED,
                home_score=90 + i, away_score=80 + i,
                home_goals=13, home_behinds=12, away_goals=11, away_behinds=14,
            )
            db_session.add(match)
            matches.append(match)
            match_date += timedelta(days=7)
    db_session.commit()
    return matches


def _seed_poisson_run(db_session, config=None):
    persist_model_run(db_session, "poisson", config or PoissonConfig(), tune_end_year=2018, metrics=[])


def test_raises_when_no_poisson_model_run(db_session):
    _seed_matches(db_session, [2019, 2020])
    with pytest.raises(ModelsUnavailableError):
        build_poisson_revision_comparison(db_session)


def test_original_config_matches_the_persisted_model_run(db_session):
    _seed_matches(db_session, [2019, 2020, 2021, 2022, 2023])
    persisted_config = PoissonConfig(rolling_window_games=33, min_games_for_reliable_strength=5)
    _seed_poisson_run(db_session, persisted_config)

    comparison = build_poisson_revision_comparison(db_session)

    assert comparison.original.config == persisted_config
    # the currently-persisted config predates league_window_games, so it
    # must load with the original (backward-compatible) unbounded default
    assert comparison.original.config.league_window_games is None


def test_common_match_count_covers_every_completed_match(db_session):
    matches = _seed_matches(db_session, [2019, 2020, 2021, 2022, 2023])
    _seed_poisson_run(db_session)

    comparison = build_poisson_revision_comparison(db_session)

    assert comparison.common_match_count == len(matches)


def test_early_season_bands_are_nested_and_labelled(db_session):
    _seed_matches(db_session, [2019, 2020, 2021, 2022, 2023])
    _seed_poisson_run(db_session)

    comparison = build_poisson_revision_comparison(db_session)

    for variant in (comparison.original, comparison.revised):
        labels = [b.label for b in variant.early_season_bands]
        assert labels == ["rounds_1_3", "rounds_1_5", "full_season"]
        n_by_label = {b.label: b.n for b in variant.early_season_bands}
        # each band is a superset of the previous — rounds 1-3 subset of
        # rounds 1-5 subset of the full season
        assert n_by_label["rounds_1_3"] <= n_by_label["rounds_1_5"] <= n_by_label["full_season"]
        assert n_by_label["rounds_1_3"] == 5 * 3  # 5 seasons (2019-2023) x 3 rounds
        assert n_by_label["full_season"] == 5 * 6  # 5 seasons x 6 rounds


def test_season_2021_bands_only_include_2021_matches(db_session):
    _seed_matches(db_session, [2019, 2020, 2021, 2022, 2023])
    _seed_poisson_run(db_session)

    comparison = build_poisson_revision_comparison(db_session)

    for variant in (comparison.original, comparison.revised):
        full = next(b for b in variant.season_2021_bands if b.label == "full_season")
        assert full.n == 6  # only 2021's 6 matches, not all 30


def test_tune_selection_never_touches_holdout_seasons(db_session):
    """The tune/holdout split must genuinely restrict select_best_config to
    season_year <= tune_end_year — a regression test against a future edit
    that accidentally passed the full match list into tuning instead of the
    filtered tune_matches subset. Verified by monkeypatching
    select_best_config to record exactly which seasons it was called with,
    for a seeded range that spans both sides of the default tune_end_year
    (2022)."""
    import app.backtesting.poisson_revision_report as revision_module

    _seed_matches(db_session, [2019, 2020, 2021, 2022, 2023, 2024])
    _seed_poisson_run(db_session)

    captured = {}
    original_select = revision_module.select_best_config

    def _spy(tune_matches, grid=None):
        captured["seasons"] = {m.season_year for m in tune_matches}
        return original_select(tune_matches, grid)

    revision_module.select_best_config = _spy
    try:
        build_poisson_revision_comparison(db_session)
    finally:
        revision_module.select_best_config = original_select

    assert captured["seasons"] == {2019, 2020, 2021, 2022}  # not 2023 or 2024 (holdout)


def test_result_is_deterministic_across_reruns(db_session):
    _seed_matches(db_session, [2019, 2020, 2021, 2022, 2023])
    _seed_poisson_run(db_session)

    first = build_poisson_revision_comparison(db_session)
    second = build_poisson_revision_comparison(db_session)

    assert first.original.config == second.original.config
    assert first.revised.config == second.revised.config
    assert first.original.evaluation_metrics == second.original.evaluation_metrics
    assert first.revised.evaluation_metrics == second.revised.evaluation_metrics
    assert first.common_match_count == second.common_match_count


def test_promotion_passes_when_every_criterion_clears():
    original = _variant(25.4, 0.211, 40.6, {"2019": 21.1, "2020": 22.6, "2021": 40.6, "2022": 22.7})
    revised = _variant(24.1, 0.212, 30.8, {"2019": 20.6, "2020": 26.6, "2021": 30.8, "2022": 22.1})

    decision = evaluate_poisson_promotion_rule(original, revised)

    assert decision.promote is True
    assert all("PASS" in r for r in decision.reasons)


def test_promotion_fails_when_totals_mae_does_not_improve():
    original = _variant(24.0, 0.211, 40.6, {"2019": 20.0, "2020": 20.0})
    revised = _variant(25.0, 0.212, 30.8, {"2019": 19.0, "2020": 19.0})  # worse overall MAE despite fixing 2021

    decision = evaluate_poisson_promotion_rule(original, revised)

    assert decision.promote is False
    assert any("FAIL" in r and "MAE" in r for r in decision.reasons)


def test_promotion_fails_when_2021_improvement_is_marginal():
    # only a 3% reduction in 2021 MAE — well under the 10% bar for "clearly fixes it"
    original = _variant(24.0, 0.211, 40.0, {"2019": 20.0, "2020": 20.0})
    revised = _variant(23.5, 0.211, 38.8, {"2019": 19.0, "2020": 19.0})

    decision = evaluate_poisson_promotion_rule(original, revised)

    assert decision.promote is False
    assert any("FAIL" in r and "2021" in r for r in decision.reasons)


def test_promotion_fails_when_winner_brier_regresses_materially():
    original = _variant(24.0, 0.200, 40.0, {"2019": 20.0, "2020": 20.0})
    revised = _variant(23.0, 0.230, 28.0, {"2019": 19.0, "2020": 19.0})  # brier up >5% relative

    decision = evaluate_poisson_promotion_rule(original, revised)

    assert decision.promote is False
    assert any("FAIL" in r and "Brier" in r for r in decision.reasons)


def test_promotion_fails_when_only_a_minority_of_seasons_improve():
    original = _variant(24.0, 0.211, 40.0, {"2019": 20.0, "2020": 20.0, "2021": 40.0, "2022": 20.0, "2023": 20.0})
    revised = _variant(23.9, 0.211, 30.0, {"2019": 21.0, "2020": 21.0, "2021": 30.0, "2022": 21.0, "2023": 21.0})

    decision = evaluate_poisson_promotion_rule(original, revised)

    assert decision.promote is False
    assert any("FAIL" in r and "season" in r for r in decision.reasons)


def test_promotion_reports_all_four_checks_even_on_failure():
    original = _variant(24.0, 0.200, 40.0, {"2019": 20.0})
    revised = _variant(25.0, 0.230, 39.0, {"2019": 21.0})

    decision = evaluate_poisson_promotion_rule(original, revised)

    assert len(decision.reasons) == 4
