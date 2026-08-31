import numpy as np
import pytest

from app.modelling.poisson_model import score_distribution
from app.player_modelling.sgm_dependence import (
    DependenceCoeff,
    PlayerLegSpec,
    TeamLegSpec,
    apply_disposal_shift,
    apply_goal_p_score_shift,
    fit_dependence,
    simulate_joint_probability,
)

NO_DEPENDENCE_DISPOSALS = DependenceCoeff(market="disposals", slope=0.0, intercept=0.0, n_observations=0)
NO_DEPENDENCE_GOALS = DependenceCoeff(market="goals", slope=0.0, intercept=0.0, n_observations=0)


def _home_away_pmfs():
    home_pmf = score_distribution(lambda_goals=13.0, lambda_behinds=13.0, max_goals=25, max_behinds=25)
    away_pmf = score_distribution(lambda_goals=11.0, lambda_behinds=13.0, max_goals=25, max_behinds=25)
    return home_pmf, away_pmf


class TestFitDependence:
    def test_recovers_known_positive_slope(self):
        rng = np.random.default_rng(0)
        surprises = rng.normal(0, 10, size=2000)
        true_slope, true_intercept = 0.15, 0.5
        residuals = true_intercept + true_slope * surprises + rng.normal(0, 1, size=2000)

        coeff = fit_dependence(list(surprises), list(residuals), market="disposals")

        assert coeff.slope == pytest.approx(true_slope, abs=0.02)
        assert coeff.intercept == pytest.approx(true_intercept, abs=0.1)
        assert coeff.n_observations == 2000

    def test_uncorrelated_data_gives_near_zero_slope(self):
        rng = np.random.default_rng(1)
        surprises = rng.normal(0, 10, size=2000)
        residuals = rng.normal(0, 1, size=2000)  # no relationship to surprises

        coeff = fit_dependence(list(surprises), list(residuals), market="disposals")

        assert coeff.slope == pytest.approx(0.0, abs=0.02)

    def test_below_minimum_observations_returns_zero_coefficient(self):
        coeff = fit_dependence([1.0] * 10, [2.0] * 10, market="goals")
        assert coeff.slope == 0.0
        assert coeff.intercept == 0.0
        assert coeff.n_observations == 10

    def test_raises_on_mismatched_lengths(self):
        with pytest.raises(ValueError):
            fit_dependence([1.0, 2.0], [1.0], market="disposals")


class TestApplyShift:
    def test_disposal_shift_clips_to_minimum(self):
        coeff = DependenceCoeff(market="disposals", slope=1.0, intercept=0.0, n_observations=100)
        assert apply_disposal_shift(base_mu=5.0, surprise=-100.0, coeff=coeff, min_mu=0.5) == 0.5

    def test_disposal_shift_vectorized(self):
        coeff = DependenceCoeff(market="disposals", slope=0.2, intercept=1.0, n_observations=100)
        surprises = np.array([-5.0, 0.0, 5.0])
        shifted = apply_disposal_shift(base_mu=20.0, surprise=surprises, coeff=coeff)
        assert list(shifted) == pytest.approx([20.0, 21.0, 22.0])

    def test_goal_p_score_shift_clips_to_bounds(self):
        coeff = DependenceCoeff(market="goals", slope=1.0, intercept=0.0, n_observations=100)
        assert apply_goal_p_score_shift(base_p_score=0.5, surprise=100.0, coeff=coeff, hi=0.99) == 0.99
        assert apply_goal_p_score_shift(base_p_score=0.5, surprise=-100.0, coeff=coeff, lo=0.01) == 0.01


class TestTeamLegSpec:
    def test_h2h_probabilities_are_complementary(self):
        home_pmf, away_pmf = _home_away_pmfs()
        home_leg = TeamLegSpec(market_type="h2h", is_home_team=True)
        away_leg = TeamLegSpec(market_type="h2h", is_home_team=False)

        home_p = home_leg.analytic_probability(home_pmf, away_pmf)
        away_p = away_leg.analytic_probability(home_pmf, away_pmf)

        assert 0.0 < home_p < 1.0
        assert home_p + away_p == pytest.approx(1.0, abs=0.02)  # remainder is the draw probability

    def test_hit_array_matches_analytic_probability(self):
        home_pmf, away_pmf = _home_away_pmfs()
        leg = TeamLegSpec(market_type="h2h", is_home_team=True)
        rng = np.random.default_rng(7)
        n = 200_000
        home_draws = rng.choice(len(home_pmf), size=n, p=home_pmf)
        away_draws = rng.choice(len(away_pmf), size=n, p=away_pmf)

        empirical = leg.hit_array(home_draws, away_draws).mean()
        analytic = leg.analytic_probability(home_pmf, away_pmf)

        assert empirical == pytest.approx(analytic, abs=0.01)


class TestSimulateJointProbability:
    def test_deterministic_given_same_seed(self):
        home_pmf, away_pmf = _home_away_pmfs()
        team_leg = TeamLegSpec(market_type="h2h", is_home_team=True)
        legs = [PlayerLegSpec(market="disposals", is_home_team=True, threshold=25, base_mu=22.0, nb_alpha=0.15, label="disp")]

        r1 = simulate_joint_probability(
            home_pmf=home_pmf, away_pmf=away_pmf, expected_margin=8.0,
            home_expected_score=90.0, away_expected_score=82.0,
            team_leg=team_leg, player_legs=legs,
            disposal_coeff=NO_DEPENDENCE_DISPOSALS, goal_coeff=NO_DEPENDENCE_GOALS,
            n_simulations=20_000, seed=42,
        )
        r2 = simulate_joint_probability(
            home_pmf=home_pmf, away_pmf=away_pmf, expected_margin=8.0,
            home_expected_score=90.0, away_expected_score=82.0,
            team_leg=team_leg, player_legs=legs,
            disposal_coeff=NO_DEPENDENCE_DISPOSALS, goal_coeff=NO_DEPENDENCE_GOALS,
            n_simulations=20_000, seed=42,
        )
        assert r1 == r2

    def test_single_leg_matches_its_own_analytic_probability(self):
        home_pmf, away_pmf = _home_away_pmfs()
        leg = PlayerLegSpec(market="disposals", is_home_team=True, threshold=25, base_mu=22.0, nb_alpha=0.15, label="disp")

        result = simulate_joint_probability(
            home_pmf=home_pmf, away_pmf=away_pmf, expected_margin=8.0,
            home_expected_score=90.0, away_expected_score=82.0,
            team_leg=None, player_legs=[leg],
            disposal_coeff=NO_DEPENDENCE_DISPOSALS, goal_coeff=NO_DEPENDENCE_GOALS,
            n_simulations=200_000, seed=1,
        )

        assert result.model_probability == pytest.approx(leg.analytic_probability(), abs=0.01)
        assert result.naive_independence_probability == pytest.approx(leg.analytic_probability(), abs=1e-9)

    def test_zero_dependence_reduces_to_independence(self):
        home_pmf, away_pmf = _home_away_pmfs()
        team_leg = TeamLegSpec(market_type="h2h", is_home_team=True)
        legs = [PlayerLegSpec(market="disposals", is_home_team=True, threshold=25, base_mu=22.0, nb_alpha=0.15, label="disp")]

        result = simulate_joint_probability(
            home_pmf=home_pmf, away_pmf=away_pmf, expected_margin=8.0,
            home_expected_score=90.0, away_expected_score=82.0,
            team_leg=team_leg, player_legs=legs,
            disposal_coeff=NO_DEPENDENCE_DISPOSALS, goal_coeff=NO_DEPENDENCE_GOALS,
            n_simulations=300_000, seed=2,
        )

        # with slope=0 there is no mechanism left to create dependence, so
        # the MC joint estimate should land within a few standard errors of
        # the exact naive product.
        assert abs(result.model_probability - result.naive_independence_probability) < 4 * result.mc_standard_error

    def test_positive_dependence_increases_joint_probability_for_aligned_legs(self):
        home_pmf, away_pmf = _home_away_pmfs()
        team_leg = TeamLegSpec(market_type="h2h", is_home_team=True)
        legs = [PlayerLegSpec(market="disposals", is_home_team=True, threshold=25, base_mu=20.0, nb_alpha=0.15, label="disp")]
        positive_coeff = DependenceCoeff(market="disposals", slope=0.3, intercept=0.0, n_observations=1000)

        result = simulate_joint_probability(
            home_pmf=home_pmf, away_pmf=away_pmf, expected_margin=8.0,
            home_expected_score=90.0, away_expected_score=82.0,
            team_leg=team_leg, player_legs=legs,
            disposal_coeff=positive_coeff, goal_coeff=NO_DEPENDENCE_GOALS,
            n_simulations=300_000, seed=3,
        )

        # home winning implies positive margin surprise (relative to an
        # expected home win by 8), which with a positive slope raises
        # disposals exactly when the team leg is already true -> the AND
        # probability should exceed the naive independent product.
        assert result.correlation_adjustment_pp > 0

    def test_n_simulations_must_be_positive(self):
        home_pmf, away_pmf = _home_away_pmfs()
        with pytest.raises(ValueError):
            simulate_joint_probability(
                home_pmf=home_pmf, away_pmf=away_pmf, expected_margin=0.0,
                home_expected_score=90.0, away_expected_score=82.0,
                team_leg=None, player_legs=[],
                disposal_coeff=NO_DEPENDENCE_DISPOSALS, goal_coeff=NO_DEPENDENCE_GOALS,
                n_simulations=0,
            )
