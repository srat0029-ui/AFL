"""Same Game Multi (SGM) joint pricing — the live read path for
app/player_modelling/sgm_dependence.py's conditional Monte Carlo engine.

Same "never refit per request" discipline as player_pricing.py: this module
reads already-persisted PlayerDisposalProjection/PlayerGoalProjection rows
and the already-fitted SgmDependenceCoefficient row(s) (see
scripts/sgm_joint_model_backtest.py — the offline fit-and-validate step),
and reuses the live-computed (not persisted, cheap) Poisson team-score PMFs
the rest of the product already prices team markets from (see
app/edges/calculator.py's module docstring for why team predictions are
computed live rather than cached).

If no SgmDependenceCoefficient row exists for a market yet (the backtest
hasn't been run, or it ran and did NOT validate — see that script's
docstring), pricing still works: a zero-coefficient placeholder makes the
conditional model degenerate to plain independence, and the response says
so via `dependence_validated=False` rather than silently pretending a
dependency was modelled.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.edges.calculator import build_model_context, compute_match_predictions
from app.edges.fair_odds import fair_odds_from_probability
from app.models import Match, PlayerDisposalProjection, PlayerGoalProjection, SgmDependenceCoefficient
from app.modelling.poisson_model import expected_value as poisson_expected_value
from app.player_modelling.market_correlation import CORRELATION_STRENGTH, _pair_correlation
from app.player_modelling.sgm_dependence import (
    DependenceCoeff,
    MARKET_DISPOSALS,
    MARKET_GOALS,
    PlayerLegSpec,
    TeamLegSpec,
    simulate_joint_probability,
)

DEFAULT_N_SIMULATIONS = 100_000


class SgmValidationError(ValueError):
    """A requested leg combo can't be priced by this engine — bad input or
    a hard-rejected (strongly correlated / contradictory) pairing."""


@dataclass(frozen=True)
class SgmLegRequest:
    """One leg of a requested Same Game Multi.

    leg_type: "h2h" | "line" | "total" | "disposals" | "goals".
    team_id: required for h2h/line (which team the leg is about); ignored for total.
    is_over: for line/total, True = over/home-perspective side.
    line_value: the handicap/total line, for line/total legs.
    player_id / threshold: required for disposals/goals legs.
    """

    leg_type: str
    team_id: int | None = None
    is_over: bool = True
    line_value: float | None = None
    player_id: int | None = None
    threshold: float | None = None


@dataclass(frozen=True)
class SgmLegPriced:
    leg_type: str
    label: str
    naive_probability: float


@dataclass(frozen=True)
class SameGameMultiPrice:
    match_id: int
    model_probability: float
    model_fair_odds: float
    naive_independence_probability: float
    naive_independence_fair_odds: float
    correlation_adjustment_pp: float
    mc_standard_error: float
    n_simulations: int
    model_version: str
    dependence_validated: bool
    generated_at: datetime
    data_cutoff: datetime | None
    legs: list[SgmLegPriced] = field(default_factory=list)


_ZERO_COEFF = {
    "disposals": DependenceCoeff(market="disposals", slope=0.0, intercept=0.0, n_observations=0),
    "goals": DependenceCoeff(market="goals", slope=0.0, intercept=0.0, n_observations=0),
}


def _load_coefficient(db: Session, market: str) -> tuple[DependenceCoeff, str | None]:
    row = db.scalar(select(SgmDependenceCoefficient).where(SgmDependenceCoefficient.market == market))
    if row is None:
        return _ZERO_COEFF[market], None
    return DependenceCoeff(market=market, slope=row.slope, intercept=row.intercept, n_observations=row.n_observations), row.model_version


def _check_no_strong_correlation(legs: list[SgmLegRequest], match_id: int) -> None:
    descriptors = []
    for leg in legs:
        if leg.leg_type in ("h2h", "line", "total"):
            descriptors.append({
                "opportunity_type": "team", "match_id": match_id, "market_type": leg.leg_type, "team_id": leg.team_id,
            })
        else:
            descriptors.append({
                "opportunity_type": "player", "match_id": match_id, "market_type": leg.leg_type,
                "player_id": leg.player_id, "team_id": None,
            })
    for i in range(len(descriptors)):
        for j in range(i + 1, len(descriptors)):
            category = _pair_correlation(descriptors[i], descriptors[j])
            if category is not None and CORRELATION_STRENGTH[category] == "strong":
                raise SgmValidationError(
                    f"legs {i} and {j} are strongly correlated ({category}) - this is a redundant/contradictory "
                    "pairing, not a probabilistic dependency question this engine models."
                )


def price_same_game_multi(
    db: Session, match_id: int, legs: list[SgmLegRequest], n_simulations: int = DEFAULT_N_SIMULATIONS, seed: int = 42,
) -> SameGameMultiPrice:
    if len(legs) < 2:
        raise SgmValidationError("a Same Game Multi needs at least 2 legs")

    team_legs = [leg for leg in legs if leg.leg_type in ("h2h", "line", "total")]
    player_leg_requests = [leg for leg in legs if leg.leg_type in (MARKET_DISPOSALS, MARKET_GOALS)]
    if len(team_legs) > 1:
        raise SgmValidationError("at most one team-market leg is supported per combo in this version")
    if not player_leg_requests:
        raise SgmValidationError("at least one player-prop leg is required (a pure team-market combo has no dependence to model)")

    match = db.get(Match, match_id)
    if match is None:
        raise SgmValidationError(f"match {match_id} not found")

    _check_no_strong_correlation(legs, match_id)

    context = build_model_context(db)
    predictions = compute_match_predictions(match, context)
    home_pmf, away_pmf = context.poisson_state.predict(match.home_team_id, match.away_team_id)

    team_leg_spec: TeamLegSpec | None = None
    if team_legs:
        raw = team_legs[0]
        is_home = raw.team_id == match.home_team_id
        team_leg_spec = TeamLegSpec(market_type=raw.leg_type, is_home_team=is_home, line_value=raw.line_value, over=raw.is_over)

    disposal_coeff, disposal_version = _load_coefficient(db, "disposals")
    goal_coeff, goal_version = _load_coefficient(db, "goals")

    player_specs: list[PlayerLegSpec] = []
    data_cutoffs: list[datetime] = []
    used_versions: set[str] = set()

    for raw in player_leg_requests:
        if raw.player_id is None or raw.threshold is None:
            raise SgmValidationError("player-prop legs require player_id and threshold")

        if raw.leg_type == MARKET_DISPOSALS:
            proj = db.scalar(
                select(PlayerDisposalProjection).where(
                    PlayerDisposalProjection.match_id == match_id, PlayerDisposalProjection.player_id == raw.player_id
                )
            )
            if proj is None:
                raise SgmValidationError(f"no disposal projection for player {raw.player_id} in match {match_id}")
            is_home = proj.team_id == match.home_team_id
            player_specs.append(PlayerLegSpec(
                market=MARKET_DISPOSALS, is_home_team=is_home, threshold=raw.threshold,
                base_mu=proj.predicted_mean, nb_alpha=proj.nb_alpha, label=f"disposals:{raw.player_id}",
            ))
            data_cutoffs.append(proj.data_cutoff)
            if disposal_version:
                used_versions.add(disposal_version)
        else:
            proj = db.scalar(
                select(PlayerGoalProjection).where(
                    PlayerGoalProjection.match_id == match_id, PlayerGoalProjection.player_id == raw.player_id
                )
            )
            if proj is None:
                raise SgmValidationError(f"no goal projection for player {raw.player_id} in match {match_id}")
            is_home = proj.team_id == match.home_team_id
            player_specs.append(PlayerLegSpec(
                market=MARKET_GOALS, is_home_team=is_home, threshold=raw.threshold,
                base_mu=proj.predicted_mean if proj.distribution_kind != "hurdle" else None,
                nb_alpha=proj.nb_alpha if proj.distribution_kind != "hurdle" else None,
                p_score=proj.p_score, mu_scored=proj.mu_scored, alpha_scored=proj.alpha_scored,
                label=f"goals:{raw.player_id}",
            ))
            data_cutoffs.append(proj.data_cutoff)
            if goal_version:
                used_versions.add(goal_version)

    result = simulate_joint_probability(
        home_pmf=home_pmf, away_pmf=away_pmf, expected_margin=predictions.poisson_expected_margin,
        home_expected_score=poisson_expected_value(home_pmf), away_expected_score=poisson_expected_value(away_pmf),
        team_leg=team_leg_spec, player_legs=player_specs,
        disposal_coeff=disposal_coeff, goal_coeff=goal_coeff,
        n_simulations=n_simulations, seed=seed,
    )

    dependence_validated = bool(used_versions)
    model_version = " + ".join(sorted(used_versions)) if used_versions else "independence_fallback (no validated dependence model persisted)"

    leg_labels = ([f"{team_legs[0].leg_type}:{team_legs[0].team_id}"] if team_legs else []) + [spec.label for spec in player_specs]
    all_naive = [team_leg_spec.analytic_probability(home_pmf, away_pmf)] if team_leg_spec else []
    all_naive += [result.per_leg_naive_probability[spec.label] for spec in player_specs]

    return SameGameMultiPrice(
        match_id=match_id,
        model_probability=result.model_probability,
        model_fair_odds=fair_odds_from_probability(result.model_probability) if result.model_probability > 0 else float("inf"),
        naive_independence_probability=result.naive_independence_probability,
        naive_independence_fair_odds=fair_odds_from_probability(result.naive_independence_probability) if result.naive_independence_probability > 0 else float("inf"),
        correlation_adjustment_pp=result.correlation_adjustment_pp,
        mc_standard_error=result.mc_standard_error,
        n_simulations=result.n_simulations,
        model_version=model_version,
        dependence_validated=dependence_validated,
        generated_at=datetime.now(timezone.utc),
        data_cutoff=max(data_cutoffs) if data_cutoffs else None,
        legs=[SgmLegPriced(leg_type=lt, label=lb, naive_probability=np_) for lt, lb, np_ in zip(
            [team_legs[0].leg_type] * len(team_legs) + [s.market for s in player_specs], leg_labels, all_naive
        )],
    )
