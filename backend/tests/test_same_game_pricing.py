from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import (
    Match, MatchStatus, Player, PlayerDisposalProjection, PlayerGoalProjection, Round, Season,
    SgmDependenceCoefficient, Sport, Team,
)
from app.pricing.same_game_pricing import SgmLegRequest, SgmValidationError, price_same_game_multi

NOW = datetime.now(timezone.utc)


def _seed_match(db, *, home_name="Collingwood", away_name="Carlton"):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name=home_name, short_name=home_name[:3].upper())
    away = Team(sport_id=sport.id, name=away_name, short_name=away_name[:3].upper())
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW + timedelta(days=1), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    return match, home, away


def _seed_model_runs(db):
    persist_model_run(
        db, "elo", EloConfig(), 2022,
        metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 648,
                  "holdout_value": 0.20, "naive_baseline_value": 0.25, "has_edge_over_naive": True}],
    )
    persist_model_run(
        db, "poisson", PoissonConfig(), 2022,
        metrics=[
            {"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 648,
             "holdout_value": 0.205, "naive_baseline_value": 0.25, "has_edge_over_naive": True},
            {"market_type": "line", "metric_name": "mae", "holdout_n": 648,
             "holdout_value": 27.2, "naive_baseline_value": 31.3, "has_edge_over_naive": True},
        ],
    )


def _seed_disposal_player(db, match, team, *, predicted_mean=22.0, nb_alpha=0.15):
    player = Player(sport_id=match.sport_id, display_name="Test Disposals Player", source="afltables", source_player_id="test-disposals", current_team_id=team.id)
    db.add(player)
    db.flush()
    db.add(PlayerDisposalProjection(
        match_id=match.id, player_id=player.id, team_id=team.id, model_name="disposals_ridge", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
        predicted_mean=predicted_mean, distribution_method="nb", nb_alpha=nb_alpha,
        confidence_tier="higher_confidence", warnings=[], input_features={},
    ))
    db.commit()
    return player


def _seed_goal_player(db, match, team, *, p_score=0.6, mu_scored=1.3, alpha_scored=0.3):
    player = Player(sport_id=match.sport_id, display_name="Test Goals Player", source="afltables", source_player_id="test-goals", current_team_id=team.id)
    db.add(player)
    db.flush()
    db.add(PlayerGoalProjection(
        match_id=match.id, player_id=player.id, team_id=team.id, model_name="goals_hurdle", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
        predicted_mean=p_score * mu_scored, distribution_kind="hurdle", nb_alpha=None,
        p_score=p_score, mu_scored=mu_scored, alpha_scored=alpha_scored, scoring_archetype="forward",
        confidence_tier="higher_confidence", warnings=[], input_features={},
    ))
    db.commit()
    return player


class TestValidation:
    def test_rejects_fewer_than_two_legs(self, db_session):
        _seed_model_runs(db_session)
        match, home, away = _seed_match(db_session)
        with pytest.raises(SgmValidationError):
            price_same_game_multi(db_session, match.id, [SgmLegRequest(leg_type="h2h", team_id=home.id)])

    def test_rejects_pure_team_combo(self, db_session):
        _seed_model_runs(db_session)
        match, home, away = _seed_match(db_session)
        with pytest.raises(SgmValidationError):
            price_same_game_multi(db_session, match.id, [
                SgmLegRequest(leg_type="h2h", team_id=home.id),
                SgmLegRequest(leg_type="total", line_value=165.5, is_over=True),
            ])

    def test_rejects_multiple_team_legs(self, db_session):
        _seed_model_runs(db_session)
        match, home, away = _seed_match(db_session)
        player = _seed_disposal_player(db_session, match, home)
        with pytest.raises(SgmValidationError):
            price_same_game_multi(db_session, match.id, [
                SgmLegRequest(leg_type="h2h", team_id=home.id),
                SgmLegRequest(leg_type="total", line_value=165.5, is_over=True),
                SgmLegRequest(leg_type="disposals", player_id=player.id, threshold=21.5),
            ])

    def test_strong_correlation_helper_flags_team_directional_pair(self):
        # Exercises app/pricing/same_game_pricing._check_no_strong_correlation
        # directly against a raw h2h+line pair for the same team - the same
        # CORRELATION_TEAM_DIRECTIONAL case Multi Builder hard-rejects
        # (app/player_modelling/market_correlation.py). price_same_game_multi
        # itself never reaches this branch today because its own "at most one
        # team leg" restriction (test_rejects_multiple_team_legs) already
        # rejects any h2h+line combo first - this check stays in place for
        # if/when that restriction is lifted.
        from app.pricing.same_game_pricing import _check_no_strong_correlation

        with pytest.raises(SgmValidationError):
            _check_no_strong_correlation(
                [
                    SgmLegRequest(leg_type="h2h", team_id=1),
                    SgmLegRequest(leg_type="line", team_id=1, line_value=-12.5),
                ],
                match_id=1,
            )

    def test_missing_projection_raises(self, db_session):
        _seed_model_runs(db_session)
        match, home, away = _seed_match(db_session)
        with pytest.raises(SgmValidationError):
            price_same_game_multi(db_session, match.id, [
                SgmLegRequest(leg_type="h2h", team_id=home.id),
                SgmLegRequest(leg_type="disposals", player_id=999999, threshold=21.5),
            ])


class TestPricing:
    def test_prices_valid_disposals_combo(self, db_session):
        _seed_model_runs(db_session)
        match, home, away = _seed_match(db_session)
        player = _seed_disposal_player(db_session, match, home)

        price = price_same_game_multi(db_session, match.id, [
            SgmLegRequest(leg_type="h2h", team_id=home.id),
            SgmLegRequest(leg_type="disposals", player_id=player.id, threshold=21.5),
        ], n_simulations=20_000, seed=1)

        assert 0.0 < price.model_probability < 1.0
        assert price.model_fair_odds == pytest.approx(1.0 / price.model_probability, rel=1e-6)
        assert 0.0 < price.naive_independence_probability < 1.0
        assert price.n_simulations == 20_000
        assert len(price.legs) == 2
        assert price.dependence_validated is False  # no SgmDependenceCoefficient row persisted in this test DB
        assert "independence_fallback" in price.model_version

    def test_prices_valid_goals_combo(self, db_session):
        _seed_model_runs(db_session)
        match, home, away = _seed_match(db_session)
        player = _seed_goal_player(db_session, match, home)

        price = price_same_game_multi(db_session, match.id, [
            SgmLegRequest(leg_type="h2h", team_id=home.id),
            SgmLegRequest(leg_type="goals", player_id=player.id, threshold=0.5),
        ], n_simulations=20_000, seed=1)

        assert 0.0 < price.model_probability < 1.0

    def test_deterministic_given_same_seed(self, db_session):
        _seed_model_runs(db_session)
        match, home, away = _seed_match(db_session)
        player = _seed_disposal_player(db_session, match, home)
        legs = [
            SgmLegRequest(leg_type="h2h", team_id=home.id),
            SgmLegRequest(leg_type="disposals", player_id=player.id, threshold=21.5),
        ]

        p1 = price_same_game_multi(db_session, match.id, legs, n_simulations=20_000, seed=7)
        p2 = price_same_game_multi(db_session, match.id, legs, n_simulations=20_000, seed=7)

        assert p1.model_probability == p2.model_probability

    def test_dependence_validated_when_coefficient_persisted(self, db_session):
        _seed_model_runs(db_session)
        match, home, away = _seed_match(db_session)
        player = _seed_disposal_player(db_session, match, home)
        db_session.add(SgmDependenceCoefficient(
            market="disposals", slope=0.02, intercept=0.0, n_observations=1000,
            fit_cutoff_year=2023, model_version="sgm_joint_conditional_mc_v1@test", fitted_at=NOW,
        ))
        db_session.commit()

        price = price_same_game_multi(db_session, match.id, [
            SgmLegRequest(leg_type="h2h", team_id=home.id),
            SgmLegRequest(leg_type="disposals", player_id=player.id, threshold=21.5),
        ], n_simulations=20_000, seed=1)

        assert price.dependence_validated is True
        assert price.model_version == "sgm_joint_conditional_mc_v1@test"
