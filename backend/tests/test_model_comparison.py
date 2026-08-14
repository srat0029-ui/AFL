from datetime import datetime, timezone

from app.backtesting.model_comparison import build_model_comparison
from app.modelling.elo import EloConfig
from app.modelling.elo_backtest import run_walk_forward as elo_walk_forward
from app.modelling.poisson_backtest import run_walk_forward as poisson_walk_forward
from app.modelling.poisson_model import PoissonConfig
from app.modelling.types import MatchResult


def _match(match_id, year, home, away, home_score, away_score, day=1) -> MatchResult:
    hg, hb = home_score // 6, home_score % 6
    ag, ab = away_score // 6, away_score % 6
    return MatchResult(
        match_id=match_id,
        season_year=year,
        scheduled_start=datetime(year, 3, day, tzinfo=timezone.utc),
        home_team_id=home,
        away_team_id=away,
        home_score=home_score,
        away_score=away_score,
        home_goals=hg,
        home_behinds=hb,
        away_goals=ag,
        away_behinds=ab,
    )


def _matches(years) -> list[MatchResult]:
    matches = []
    match_id = 1
    for year in years:
        for day in range(1, 6):
            matches.append(_match(match_id, year, 1, 2, 90, 80, day=day))
            match_id += 1
            matches.append(_match(match_id, year, 3, 4, 70, 95, day=day))
            match_id += 1
    return matches


def test_build_model_comparison_pairs_by_match_id():
    matches = _matches([2020, 2021])
    elo_preds = elo_walk_forward(matches, EloConfig())
    poisson_preds = poisson_walk_forward(matches, PoissonConfig())

    report = build_model_comparison(elo_preds, poisson_preds)

    assert report.n_matches == len(matches)


def test_build_model_comparison_ignores_unmatched_predictions():
    matches = _matches([2020])
    elo_preds = elo_walk_forward(matches, EloConfig())
    poisson_preds = poisson_walk_forward(matches, PoissonConfig())[:-1]  # drop one

    report = build_model_comparison(elo_preds, poisson_preds)

    assert report.n_matches == len(matches) - 1


def test_disagreement_buckets_partition_all_matches():
    matches = _matches([2019, 2020, 2021])
    elo_preds = elo_walk_forward(matches, EloConfig())
    poisson_preds = poisson_walk_forward(matches, PoissonConfig())

    report = build_model_comparison(elo_preds, poisson_preds)

    assert sum(b.n for b in report.disagreement_buckets) == report.n_matches
    assert [b.label for b in report.disagreement_buckets] == [
        "agree within 5pp",
        "disagree 5-10pp",
        "disagree 10-20pp",
        "disagree 20pp+",
    ]


def test_disagreement_bucket_with_zero_games_has_no_metrics():
    # two matches with identical inputs to both models will always land in
    # "agree within 5pp" since Elo/Poisson start from the same neutral prior
    matches = _matches([2020])
    elo_preds = elo_walk_forward(matches, EloConfig())
    poisson_preds = poisson_walk_forward(matches, PoissonConfig())
    report = build_model_comparison(elo_preds, poisson_preds)

    empty_buckets = [b for b in report.disagreement_buckets if b.n == 0]
    for b in empty_buckets:
        assert b.elo_metrics == {}
        assert b.poisson_metrics == {}
        assert b.actual_home_win_rate is None


def test_season_stability_has_one_row_per_season():
    matches = _matches([2019, 2020, 2021])
    elo_preds = elo_walk_forward(matches, EloConfig())
    poisson_preds = poisson_walk_forward(matches, PoissonConfig())

    report = build_model_comparison(elo_preds, poisson_preds)

    assert [row.season_year for row in report.season_stability] == ["2019", "2020", "2021"]
    assert all(row.n_games == 10 for row in report.season_stability)


def test_model_comparison_empty_input_does_not_crash():
    report = build_model_comparison([], [])
    assert report.n_matches == 0
    assert report.disagreement_buckets == []
    assert report.season_stability == []


def test_mean_absolute_disagreement_is_nonnegative():
    matches = _matches([2020, 2021])
    elo_preds = elo_walk_forward(matches, EloConfig())
    poisson_preds = poisson_walk_forward(matches, PoissonConfig())

    report = build_model_comparison(elo_preds, poisson_preds)

    assert report.mean_absolute_disagreement >= 0.0
