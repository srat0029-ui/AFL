"""Same Game Multi (SGM) joint pricing — the dependence layer between the
team-level Poisson model and player-level disposal/goal marginals.

`scripts/sgm_correlation_research.py` (Spearman study, 101,521 player-match
rows) found almost every leg-pairing negligibly correlated (rho<0.03)
EXCEPT a player's own output vs. their own team's match outcome (disposals
vs. team margin, goals vs. own team's score). This module builds a joint
model around exactly that one real signal rather than a generic copula over
every pair — most of which the study already showed is noise, and fitting a
full dependence matrix on ~0 correlations is exactly the kind of unvalidated
sophistication this project's promotion-gate discipline (see
app/modelling/promotion.py) exists to catch.

Design: a single global linear "dependence coefficient" per market
(disposals, goals) — analogous in spirit to poisson_model.py's simple
ratio-strength model ("appropriate starting complexity... not a jointly-fit
MLE"), not a per-player hierarchical model. The coefficient describes how
much a player's marginal shifts, in raw units, per point of "team-outcome
surprise" (actual margin/score minus what the ALREADY-FITTED Poisson model
expected) — using the model's own expectation as the baseline means team
strength already captured in predicted_mean/Poisson isn't double-counted;
only the surprise component (game state the pre-match model didn't predict)
drives the shift.

The actual joint probability is estimated by genuine Monte Carlo simulation
over the shared latent variable that creates the dependence in the first
place — the match's own team-score draw — reusing poisson_model.py's exact
score PMFs so team legs stay perfectly consistent with the team pricing
already shown elsewhere in the product. This is deliberately NOT a copula on
player-pair correlations: teammates end up correlated automatically because
they're conditioned on the same simulated team draw, without needing a
separate teammate-pair term the correlation study found to be pseudo-
replicated and unreliable anyway.

Whether this actually beats naive independence is an empirical question
answered by scripts/sgm_joint_model_backtest.py, not assumed here.
"""

import math
from dataclasses import dataclass, field

import numpy as np

from app.modelling.poisson_model import (
    prob_margin_over,
    prob_total_over,
    win_draw_loss,
)
from app.player_modelling.disposal_distribution import (
    NegativeBinomialDistribution,
    nb_pmf,
)
from app.player_modelling.goal_distribution import (
    MAX_GOALS,
    HurdleDistribution,
    NegativeBinomialGoalDistribution,
)

MARKET_DISPOSALS = "disposals"
MARKET_GOALS = "goals"

MIN_OBSERVATIONS_TO_FIT = 30  # below this, report "no evidence of dependence" rather than a noisy slope


@dataclass(frozen=True)
class DependenceCoeff:
    """residual = intercept + slope * surprise, fit by OLS in raw units
    (residual = actual - baseline, surprise = actual_team_outcome -
    poisson_expected_team_outcome). slope=0/intercept=0 (the default when
    n_observations < MIN_OBSERVATIONS_TO_FIT) makes apply_*_shift a no-op,
    so "not enough evidence" degrades to "assume independence", never to a
    fabricated effect."""

    market: str
    slope: float
    intercept: float
    n_observations: int


def fit_dependence(surprises: list[float], residuals: list[float], market: str) -> DependenceCoeff:
    """OLS slope/intercept of residual ~ surprise, both in raw units so the
    result can be applied directly to a base mu/p_score without needing to
    carry around separate standardization constants at read time."""
    n = len(surprises)
    if n != len(residuals):
        raise ValueError("surprises and residuals must be the same length")
    if n < MIN_OBSERVATIONS_TO_FIT:
        return DependenceCoeff(market=market, slope=0.0, intercept=0.0, n_observations=n)

    x = np.asarray(surprises, dtype=float)
    y = np.asarray(residuals, dtype=float)
    x_mean, y_mean = float(x.mean()), float(y.mean())
    var_x = float(np.mean((x - x_mean) ** 2))
    if var_x < 1e-9:
        return DependenceCoeff(market=market, slope=0.0, intercept=0.0, n_observations=n)

    slope = float(np.mean((x - x_mean) * (y - y_mean)) / var_x)
    intercept = y_mean - slope * x_mean
    return DependenceCoeff(market=market, slope=slope, intercept=intercept, n_observations=n)


def apply_disposal_shift(base_mu: float, surprise: float | np.ndarray, coeff: DependenceCoeff, min_mu: float = 0.5):
    shifted = base_mu + coeff.intercept + coeff.slope * surprise
    return np.clip(shifted, min_mu, None) if isinstance(shifted, np.ndarray) else max(shifted, min_mu)


def apply_goal_p_score_shift(base_p_score: float, surprise: float | np.ndarray, coeff: DependenceCoeff, lo: float = 0.01, hi: float = 0.99):
    shifted = base_p_score + coeff.intercept + coeff.slope * surprise
    return np.clip(shifted, lo, hi) if isinstance(shifted, np.ndarray) else min(max(shifted, lo), hi)


@dataclass(frozen=True)
class TeamLegSpec:
    """market_type: "h2h" | "line" | "total". is_home_team is ignored for
    "total" (both teams share one total). line_value is the handicap/total
    line for "line"/"total"; unused for "h2h". `over` selects the over/home
    side of the line (over=True) vs under/away (over=False)."""

    market_type: str
    is_home_team: bool = True
    line_value: float | None = None
    over: bool = True

    def hit_array(self, home_draws: np.ndarray, away_draws: np.ndarray) -> np.ndarray:
        if self.market_type == "h2h":
            return (home_draws > away_draws) if self.is_home_team else (away_draws > home_draws)
        if self.market_type == "line":
            margin = (home_draws - away_draws) if self.is_home_team else (away_draws - home_draws)
            return margin > self.line_value if self.over else margin < self.line_value
        if self.market_type == "total":
            total = home_draws + away_draws
            return total > self.line_value if self.over else total < self.line_value
        raise ValueError(f"unknown team market_type {self.market_type!r}")

    def analytic_probability(self, home_pmf: np.ndarray, away_pmf: np.ndarray) -> float:
        if self.market_type == "h2h":
            home_win, _, away_win = win_draw_loss(home_pmf, away_pmf)
            return home_win if self.is_home_team else away_win
        if self.market_type == "line":
            pmf_a, pmf_b = (home_pmf, away_pmf) if self.is_home_team else (away_pmf, home_pmf)
            p_over = prob_margin_over(pmf_a, pmf_b, self.line_value)
            return p_over if self.over else max(0.0, 1.0 - p_over - _prob_margin_exact(pmf_a, pmf_b, self.line_value))
        if self.market_type == "total":
            p_over = prob_total_over(home_pmf, away_pmf, self.line_value)
            return p_over if self.over else 1.0 - p_over
        raise ValueError(f"unknown team market_type {self.market_type!r}")


def _prob_margin_exact(pmf_a: np.ndarray, pmf_b: np.ndarray, line: float) -> float:
    """P(margin == line) exactly — only non-zero when line is an achievable
    integer margin; needed so analytic under-probabilities don't silently
    double-count or drop the push case."""
    if line != math.floor(line):
        return 0.0
    conv = np.convolve(pmf_a, pmf_b[::-1])
    min_margin = -(len(pmf_b) - 1)
    idx = int(line) - min_margin
    return float(conv[idx]) if 0 <= idx < len(conv) else 0.0


@dataclass(frozen=True)
class PlayerLegSpec:
    """One player prop leg. `market` selects which of the two marginal
    shapes applies. disposals legs need base_mu/nb_alpha; goals legs need
    p_score/mu_scored/alpha_scored (hurdle) OR base_mu/nb_alpha (plain NB
    fallback, distribution_kind="nb" — see PlayerGoalProjection)."""

    market: str
    is_home_team: bool
    threshold: float
    label: str = ""
    base_mu: float | None = None
    nb_alpha: float | None = None
    p_score: float | None = None
    mu_scored: float | None = None
    alpha_scored: float | None = None

    def analytic_probability(self) -> float:
        if self.market == MARKET_DISPOSALS:
            return NegativeBinomialDistribution(mu=self.base_mu, alpha=self.nb_alpha).prob_at_least(self.threshold)
        if self.p_score is not None:
            return HurdleDistribution(p_score=self.p_score, mu_scored=self.mu_scored, alpha_scored=self.alpha_scored).prob_at_least(self.threshold)
        return NegativeBinomialGoalDistribution(mu=self.base_mu, alpha=self.nb_alpha).prob_at_least(self.threshold)


@dataclass(frozen=True)
class SimulationResult:
    model_probability: float
    naive_independence_probability: float
    mc_standard_error: float
    n_simulations: int
    per_leg_naive_probability: dict = field(default_factory=dict)

    @property
    def correlation_adjustment_pp(self) -> float:
        """Percentage-point difference: positive means the dependence model
        found the combo MORE likely than naive independence assumed (e.g.
        the player's own team winning big genuinely helps their own prop)."""
        return (self.model_probability - self.naive_independence_probability) * 100.0


def _truncated_goal_cdf_tail(mu_scored: float, alpha_scored: float) -> np.ndarray:
    """Cumulative distribution over k=1..MAX_GOALS of the zero-truncated NB
    used by HurdleDistribution's k>=1 mass — same construction as
    HurdleDistribution._pmf(), reused here (not duplicated logic) so
    sampling stays consistent with the distribution actually priced
    elsewhere in the product."""
    raw = nb_pmf(mu_scored, alpha_scored, k_max=MAX_GOALS)
    p0 = float(raw[0])
    tail = raw[1:] / max(1.0 - p0, 1e-9)
    return np.cumsum(tail)


def simulate_joint_probability(
    *,
    home_pmf: np.ndarray,
    away_pmf: np.ndarray,
    expected_margin: float,
    home_expected_score: float,
    away_expected_score: float,
    team_leg: TeamLegSpec | None,
    player_legs: list[PlayerLegSpec],
    disposal_coeff: DependenceCoeff,
    goal_coeff: DependenceCoeff,
    n_simulations: int = 100_000,
    seed: int = 42,
) -> SimulationResult:
    """Monte Carlo joint probability: draw N (home_score, away_score) pairs
    from the match's own exact Poisson PMFs (independent draws — same
    simplification poisson_model.py's team model already makes), then for
    each draw compute every leg's hit indicator, shifting player marginals
    by that draw's own team-outcome surprise. joint_p = mean(all legs hit).

    Player-stat sampling uses the standard Gamma-Poisson mixture
    representation of NB2 (Lambda ~ Gamma(shape=1/alpha, scale=mu*alpha),
    X | Lambda ~ Poisson(Lambda)) specifically because it supports a
    different mu per draw (the shifted mean) via numpy's vectorized
    Gamma/Poisson samplers, without a Python-level loop over n_simulations.
    """
    if n_simulations < 1:
        raise ValueError("n_simulations must be positive")

    rng = np.random.default_rng(seed)
    home_draws = rng.choice(len(home_pmf), size=n_simulations, p=home_pmf)
    away_draws = rng.choice(len(away_pmf), size=n_simulations, p=away_pmf)
    margin_draws = home_draws.astype(float) - away_draws.astype(float)

    hit = team_leg.hit_array(home_draws, away_draws) if team_leg is not None else np.ones(n_simulations, dtype=bool)

    per_leg_naive: dict[str, float] = {}
    naive_p = team_leg.analytic_probability(home_pmf, away_pmf) if team_leg is not None else 1.0

    for leg in player_legs:
        own_margin_draws = margin_draws if leg.is_home_team else -margin_draws
        team_surprise = own_margin_draws - (expected_margin if leg.is_home_team else -expected_margin)

        if leg.market == MARKET_DISPOSALS:
            shifted_mu = apply_disposal_shift(leg.base_mu, team_surprise, disposal_coeff)
            r = 1.0 / leg.nb_alpha
            lam = rng.gamma(shape=r, scale=shifted_mu * leg.nb_alpha)
            samples = rng.poisson(lam)
            leg_hit = samples >= math.ceil(leg.threshold)
        else:
            own_score_draws = home_draws if leg.is_home_team else away_draws
            expected_own_score = home_expected_score if leg.is_home_team else away_expected_score
            score_surprise = own_score_draws.astype(float) - expected_own_score

            if leg.p_score is not None:
                shifted_p_score = apply_goal_p_score_shift(leg.p_score, score_surprise, goal_coeff)
                scored = rng.random(n_simulations) < shifted_p_score
                cdf_tail = _truncated_goal_cdf_tail(leg.mu_scored, leg.alpha_scored)
                u = rng.random(n_simulations)
                goals_if_scored = 1 + np.searchsorted(cdf_tail, u, side="right")
                samples = np.where(scored, goals_if_scored, 0)
            else:
                shifted_mu = apply_disposal_shift(leg.base_mu, score_surprise, goal_coeff, min_mu=0.05)
                r = 1.0 / leg.nb_alpha
                lam = rng.gamma(shape=r, scale=shifted_mu * leg.nb_alpha)
                samples = rng.poisson(lam)
            leg_hit = samples >= math.ceil(leg.threshold)

        hit = hit & leg_hit
        per_leg_naive[leg.label or leg.market] = leg.analytic_probability()
        naive_p *= per_leg_naive[leg.label or leg.market]

    joint_p = float(hit.mean())
    se = math.sqrt(joint_p * (1.0 - joint_p) / n_simulations)

    return SimulationResult(
        model_probability=joint_p,
        naive_independence_probability=naive_p,
        mc_standard_error=se,
        n_simulations=n_simulations,
        per_leg_naive_probability=per_leg_naive,
    )
