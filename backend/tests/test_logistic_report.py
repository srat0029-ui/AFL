import random
import warnings
from datetime import datetime, timedelta, timezone

import pytest

from app.backtesting.logistic_report import ModelsUnavailableError, build_logistic_comparison
from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import Match, MatchStatus, Round, Season, Sport, Team, TeamMatchStat

warnings.filterwarnings("ignore", category=FutureWarning)


def _seed_full_dataset(db_session, seasons=(2016, 2017, 2018, 2019, 2020), games_per_season=40, seed=0):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()

    teams = []
    for i in range(8):
        t = Team(sport_id=sport.id, name=f"Team{i}", short_name=f"T{i}")
        db_session.add(t)
        teams.append(t)
    db_session.flush()

    rng = random.Random(seed)
    match_id_counter = 0
    for year in seasons:
        season = Season(sport_id=sport.id, year=year)
        db_session.add(season)
        db_session.flush()
        match_date = datetime(year, 3, 1, tzinfo=timezone.utc)
        for i in range(games_per_season):
            round_ = Round(season_id=season.id, round_number=i + 1)
            db_session.add(round_)
            db_session.flush()
            home, away = rng.sample(teams, 2)
            home_g, home_b = rng.randint(8, 18), rng.randint(8, 18)
            away_g, away_b = rng.randint(8, 18), rng.randint(8, 18)
            home_score, away_score = 6 * home_g + home_b, 6 * away_g + away_b
            match = Match(
                sport_id=sport.id, season_id=season.id, round_id=round_.id,
                home_team_id=home.id, away_team_id=away.id,
                scheduled_start=match_date, status=MatchStatus.COMPLETED,
                home_score=home_score, away_score=away_score,
                home_goals=home_g, home_behinds=home_b, away_goals=away_g, away_behinds=away_b,
            )
            db_session.add(match)
            db_session.flush()

            for team, score in [(home, home_score), (away, away_score)]:
                db_session.add(
                    TeamMatchStat(
                        match_id=match.id, team_id=team.id, source="afltables",
                        recorded_at=datetime.now(timezone.utc),
                        clearances=rng.randint(30, 50), inside_50s=rng.randint(40, 60),
                        contested_possessions=rng.randint(120, 170), tackles=rng.randint(50, 70),
                        marks_inside_50=rng.randint(3, 10),
                    )
                )
            match_date += timedelta(days=7)
            match_id_counter += 1
    db_session.commit()


def _seed_model_runs(db_session):
    persist_model_run(db_session, "elo", EloConfig(), 2018, metrics=[])
    persist_model_run(db_session, "poisson", PoissonConfig(), 2018, metrics=[])


def test_raises_when_elo_not_run(db_session):
    _seed_full_dataset(db_session)
    with pytest.raises(ModelsUnavailableError):
        build_logistic_comparison(db_session, C_stats_only=1.0, C_stats_plus_elo=1.0)


def test_build_logistic_comparison_produces_structured_report(db_session):
    _seed_full_dataset(db_session)
    _seed_model_runs(db_session)

    overview = build_logistic_comparison(db_session, C_stats_only=1.0, C_stats_plus_elo=1.0)

    assert overview.n_eval > 0
    assert overview.evaluation_start_year == 2019
    assert len(overview.baselines) == 3
    assert overview.elo.n == overview.n_eval
    assert overview.poisson.n == overview.n_eval
    assert overview.stats_only.n_eval == overview.n_eval
    assert overview.stats_plus_elo.n_eval == overview.n_eval


def test_all_models_scored_on_identical_match_count(db_session):
    """Section 8's explicit requirement: every model in the comparison must
    be scored over the exact same match set."""
    _seed_full_dataset(db_session)
    _seed_model_runs(db_session)

    overview = build_logistic_comparison(db_session, C_stats_only=1.0, C_stats_plus_elo=1.0)

    ns = {overview.elo.n, overview.poisson.n, overview.stats_only.n_eval, overview.stats_plus_elo.n_eval}
    ns |= {b.n for b in overview.baselines}
    assert len(ns) == 1  # every model scored on the same number of matches


def test_feature_group_ablation_present_for_both_variants(db_session):
    _seed_full_dataset(db_session)
    _seed_model_runs(db_session)
    overview = build_logistic_comparison(db_session, C_stats_only=1.0, C_stats_plus_elo=1.0)

    for variant in (overview.stats_only, overview.stats_plus_elo):
        labels = {a.label for a in variant.feature_group_ablation}
        assert "elo_only" in labels
        assert "elo_plus_all_stats" in labels


def test_promotion_decision_present_for_both_variants(db_session):
    _seed_full_dataset(db_session)
    _seed_model_runs(db_session)
    overview = build_logistic_comparison(db_session, C_stats_only=1.0, C_stats_plus_elo=1.0)

    assert isinstance(overview.stats_only.promotion.promote, bool)
    assert isinstance(overview.stats_plus_elo.promotion.promote, bool)
    assert len(overview.stats_only.promotion.reasons) == 5


def test_disagreement_report_present(db_session):
    _seed_full_dataset(db_session)
    _seed_model_runs(db_session)
    overview = build_logistic_comparison(db_session, C_stats_only=1.0, C_stats_plus_elo=1.0)

    assert overview.stats_only.disagreement_vs_elo.n_matches == overview.n_eval
