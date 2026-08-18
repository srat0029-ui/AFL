"""Price sensitivity (Weekly Bet Review + Decision Support stage, Section
7) — how the model-estimated EV changes across the ELIGIBLE prices
actually on offer for a market, plus the theoretical break-even price
implied by the model's own probability. Reuses app/edges/fair_odds.py's
existing EV/fair-odds math exactly as-is (Section 13's "never build a
second prop model" applies here too — this is presentation, not a new
probability calculation).

The break-even number is deliberately labelled "Model fair price", never
"recommended minimum bet price" — it is the price at which the model's
own estimate implies zero expected value, not a recommendation to bet at
or above it.
"""

from dataclasses import dataclass

from app.edges.fair_odds import expected_value, fair_odds_from_probability
from app.models.bookmaker import ELIGIBILITY_INCLUDED


@dataclass(frozen=True)
class PricePoint:
    bookmaker_name: str | None  # None for the two always-included reference points (fair price, offered price without an attached book)
    price_decimal: float
    model_estimated_ev: float


@dataclass(frozen=True)
class PriceSensitivity:
    model_fair_price: float  # 1 / model_probability - a break-even reference point, NOT a recommendation
    price_points: list[PricePoint]  # every ELIGIBLE bookmaker's price, each with its own model-estimated EV, sorted best-price-first


def compute_price_sensitivity(model_probability: float, bookmakers: list[dict]) -> PriceSensitivity:
    """`bookmakers` should already be annotated with eligibility (see
    bookmaker_classification.annotate_price_entries) - exchange/excluded
    prices are omitted from the sensitivity table entirely, matching how
    this app already treats them everywhere else (Market Integrity
    stage)."""
    eligible = [b for b in bookmakers if b.get("eligibility", ELIGIBILITY_INCLUDED) == ELIGIBILITY_INCLUDED]
    points = [
        PricePoint(bookmaker_name=b["bookmaker_name"], price_decimal=b["price_decimal"], model_estimated_ev=expected_value(model_probability, b["price_decimal"]))
        for b in eligible
    ]
    points.sort(key=lambda p: p.price_decimal, reverse=True)
    return PriceSensitivity(model_fair_price=fair_odds_from_probability(model_probability), price_points=points)


def price_sensitivity_for_opportunity(opportunity: dict) -> PriceSensitivity:
    return compute_price_sensitivity(opportunity["model_probability"], opportunity["bookmakers"])


def price_sensitivity_as_dict(s: PriceSensitivity) -> dict:
    return {
        "model_fair_price": s.model_fair_price,
        "price_points": [{"bookmaker_name": p.bookmaker_name, "price_decimal": p.price_decimal, "model_estimated_ev": p.model_estimated_ev} for p in s.price_points],
    }
