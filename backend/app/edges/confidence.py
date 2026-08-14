"""Edge tier and confidence tier — two distinct axes.

Edge tier answers "how big does the model think the mispricing is" (a
function of the probability gap alone). Confidence tier answers "how much
should you trust that number" — and is deliberately not just a bigger-gap-
looks-more-confident score. Per the architecture brief: sample size, model
calibration/validation quality, data completeness, and model agreement all
factor in, and a big edge from a model with no demonstrated skill in this
market is *lower* confidence, not higher — "do not manufacture an insight
just because a match exists."
"""

from dataclasses import dataclass, field

EDGE_TIERS = ("none", "weak", "moderate", "strong")
CONFIDENCE_TIERS = ("insufficient_data", "lower", "moderate", "higher")

_ADJUSTABLE_TIERS = ("lower", "moderate", "higher")  # insufficient_data is a hard gate, not reached by +/-1 steps


def edge_tier(model_probability: float, fair_market_probability: float) -> str:
    """Based on the signed gap (model - market). A model that likes a
    selection LESS than the market does gets "none" — that's not a
    mispricing in this selection's favour, whatever the raw magnitude.
    """
    edge = model_probability - fair_market_probability
    if edge >= 0.08:
        return "strong"
    if edge >= 0.04:
        return "moderate"
    if edge >= 0.02:
        return "weak"
    return "none"


@dataclass(frozen=True)
class ConfidenceInputs:
    has_model_edge_over_naive: bool
    overround_removed: bool
    primary_model_probability: float | None = None
    secondary_model_probability: float | None = None  # e.g. Poisson's h2h prob, cross-checked against Elo
    min_team_games: int | None = None
    min_games_threshold: int = 6


@dataclass(frozen=True)
class ConfidenceResult:
    tier: str
    reasons: list[str] = field(default_factory=list)


def compute_confidence(inputs: ConfidenceInputs) -> ConfidenceResult:
    if not inputs.has_model_edge_over_naive:
        return ConfidenceResult(
            tier="insufficient_data",
            reasons=[
                "The underlying model has no demonstrated predictive edge over a naive "
                "baseline for this market in backtesting — see model validation results."
            ],
        )

    tier = "moderate"
    reasons: list[str] = []

    if not inputs.overround_removed:
        reasons.append(
            "Only one side of this market was quoted, so bookmaker overround could not be "
            "removed — the market probability may still include the vig."
        )
        tier = _downgrade(tier)

    if inputs.min_team_games is not None and inputs.min_team_games < inputs.min_games_threshold:
        reasons.append(
            f"Limited recent-form sample for at least one team ({inputs.min_team_games} games) "
            "— early-career or small-sample estimates are noisier."
        )
        tier = _downgrade(tier)

    if inputs.primary_model_probability is not None and inputs.secondary_model_probability is not None:
        delta = abs(inputs.primary_model_probability - inputs.secondary_model_probability)
        if delta > 0.15:
            reasons.append(f"The two models disagree by {delta:.0%} on this outcome — treat with caution.")
            tier = _downgrade(tier)
        elif delta < 0.05:
            reasons.append(f"The two models agree closely (difference of {delta:.0%}).")
            tier = _upgrade(tier)

    if not reasons:
        reasons.append("No specific data-quality concerns identified.")

    return ConfidenceResult(tier=tier, reasons=reasons)


def _downgrade(tier: str) -> str:
    idx = _ADJUSTABLE_TIERS.index(tier)
    return _ADJUSTABLE_TIERS[max(idx - 1, 0)]


def _upgrade(tier: str) -> str:
    idx = _ADJUSTABLE_TIERS.index(tier)
    return _ADJUSTABLE_TIERS[min(idx + 1, len(_ADJUSTABLE_TIERS) - 1)]
