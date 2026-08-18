"""Model vs market DIRECTION agreement (Weekly Bet Review + Decision
Support stage, Section 5) — distinguishes two qualitatively different
situations that a single "model above market by Xpp" number collapses
together:

- Model and market AGREE on direction (both favour the selection, or both
  consider it an underdog) but differ on degree - e.g. the real
  Collingwood case: model 46.2%, market-implied ~23% - both call
  Collingwood an underdog, the model just thinks the market has made them
  too big an underdog.
- Model DISAGREES with market on direction outright - the model favours
  the selection while the market favours the opposite side, or vice versa.

A boundary probability of exactly 50% is treated as "not favoured" (a
coin-flip isn't a directional stance either way) - this only affects the
rare exact-0.5 case, never a real market price.
"""

from dataclasses import dataclass

AGREES_ON_DIRECTION = "agrees_on_direction"
DISAGREES_ON_DIRECTION = "disagrees_on_direction"


@dataclass(frozen=True)
class DirectionAgreement:
    classification: str
    model_favours_selection: bool
    market_favours_selection: bool
    description: str


def _stance(favours: bool) -> str:
    return "more likely than not to win" if favours else "an underdog"


def classify_direction_agreement(model_probability: float, market_probability: float) -> DirectionAgreement:
    model_favours = model_probability > 0.5
    market_favours = market_probability > 0.5

    if model_favours == market_favours:
        classification = AGREES_ON_DIRECTION
        description = (
            f"Model and market both consider this selection {_stance(model_favours)} — they agree on direction, "
            f"but differ on degree (model {model_probability:.1%} vs market {market_probability:.1%})."
        )
    else:
        classification = DISAGREES_ON_DIRECTION
        description = (
            f"Model and market disagree on direction: the model considers this selection {_stance(model_favours)} "
            f"({model_probability:.1%}) while the market considers it {_stance(market_favours)} ({market_probability:.1%})."
        )

    return DirectionAgreement(
        classification=classification,
        model_favours_selection=model_favours,
        market_favours_selection=market_favours,
        description=description,
    )


def direction_agreement_for_opportunity(opportunity: dict) -> DirectionAgreement:
    market_probability = opportunity["devigged_probability"] if opportunity.get("overround_removed") else opportunity["market_implied_probability"]
    return classify_direction_agreement(opportunity["model_probability"], market_probability)


def direction_agreement_as_dict(a: DirectionAgreement) -> dict:
    return {
        "classification": a.classification,
        "model_favours_selection": a.model_favours_selection,
        "market_favours_selection": a.market_favours_selection,
        "description": a.description,
    }
