"""Deterministic "why the model likes it" explanation (Section 14 of the
best-bets stage brief) — explicitly NOT LLM-generated. Built entirely from
the SAME input_features dict already persisted on PlayerDisposalProjection
/ PlayerGoalProjection (see ProjectionTable.tsx's own feature label maps),
using only fixed, same-unit comparisons — never a claim the stored numbers
don't actually support, and never an invented football narrative.

Only two comparisons are made, because they're the only ones for which a
genuine same-unit baseline is stored for every player:

- recent form vs the player's own longer-run baseline (both are the SAME
  stat, e.g. disposals_last5_avg vs disposals_season_avg/career_avg)
- opponent context vs the player's OWN TEAM's recent output, both at team
  level (opponent_disposals_conceded_avg vs team_recent_disposals_avg;
  opponent_goals_conceded_avg vs team_recent_goals_avg) — this is the only
  pairing where both numbers are genuinely the same unit as each other.
  opponent_disposals_conceded_avg/opponent_goals_conceded_avg are TEAM-
  level aggregates (~350+ disposals or several goals per game across the
  whole side — see disposal_features.py's/goal_features.py's
  team_ctx_by_match), nowhere close to a single player's own average, so
  comparing them against the PLAYER's own baseline would be a meaningless,
  wrong-unit claim — exactly the kind of invented-looking narrative this
  section prohibits, even though both individual numbers are real.

No other stored feature (time-on-ground, marks inside 50, team Elo win
probability) has a second same-unit reference number to compare against,
so none of them are asserted as a "reason" here.
"""

from dataclasses import dataclass

from app.player_modelling.market import PlayerMarket

_MEANINGFUL_RELATIVE_DIFF = 0.05  # below this, a comparison isn't worth naming at all
_NOTABLE_RELATIVE_DIFF = 0.15

_DISPOSAL_RECENT_KEYS = ("disposals_last3_avg", "disposals_last5_avg", "disposals_ewma")
_DISPOSAL_BASELINE_KEYS = ("disposals_season_avg", "disposals_career_avg")
_GOAL_RECENT_KEYS = ("goals_last3_avg", "goals_last5_avg", "goals_ewma")
_GOAL_BASELINE_KEYS = ("goals_season_avg", "goals_career_avg")


@dataclass(frozen=True)
class ExplanationFactor:
    name: str  # "recent_form" | "opponent_context"
    direction: str  # "above" | "below"
    description: str


def _relative_diff(value: float, baseline: float) -> float:
    if baseline == 0:
        return 0.0
    return (value - baseline) / abs(baseline)


def _magnitude_word(rel: float) -> str:
    return "notably" if abs(rel) >= _NOTABLE_RELATIVE_DIFF else "slightly"


def _average_present(features: dict, keys: tuple[str, ...]) -> float | None:
    values = [features[k] for k in keys if features.get(k) is not None]
    return sum(values) / len(values) if values else None


def _recent_form_factor(features: dict, recent_keys: tuple[str, ...], baseline_keys: tuple[str, ...], stat_label: str) -> ExplanationFactor | None:
    recent = _average_present(features, recent_keys)
    baseline = _average_present(features, baseline_keys)
    if recent is None or baseline is None:
        return None
    rel = _relative_diff(recent, baseline)
    if abs(rel) < _MEANINGFUL_RELATIVE_DIFF:
        return None
    direction = "above" if rel > 0 else "below"
    return ExplanationFactor(
        name="recent_form",
        direction=direction,
        description=(
            f"recent {stat_label} form ({recent:.1f}) is {_magnitude_word(rel)} {direction} "
            f"their longer-run baseline ({baseline:.1f})"
        ),
    )


def _opponent_context_factor(features: dict, opponent_key: str, team_recent_key: str, stat_label: str) -> ExplanationFactor | None:
    """Both `opponent_key` and `team_recent_key` are TEAM-level aggregates
    (see module docstring) — the only same-unit pairing available for
    opponent context, never the player's own individual baseline."""
    opponent_value = features.get(opponent_key)
    team_value = features.get(team_recent_key)
    if opponent_value is None or team_value is None:
        return None
    rel = _relative_diff(opponent_value, team_value)
    if abs(rel) < _MEANINGFUL_RELATIVE_DIFF:
        return None
    direction = "above" if rel > 0 else "below"
    return ExplanationFactor(
        name="opponent_context",
        direction=direction,
        description=(
            f"the opponent's {stat_label} conceded per game ({opponent_value:.1f}) runs {_magnitude_word(rel)} {direction} "
            f"this player's team's own recent {stat_label} output ({team_value:.1f})"
        ),
    )


def _compute_factors(market_type: str, input_features: dict) -> list[ExplanationFactor]:
    if market_type == PlayerMarket.DISPOSALS.value:
        candidates = [
            _recent_form_factor(input_features, _DISPOSAL_RECENT_KEYS, _DISPOSAL_BASELINE_KEYS, "disposal"),
            _opponent_context_factor(input_features, "opponent_disposals_conceded_avg", "team_recent_disposals_avg", "disposals"),
        ]
    elif market_type == PlayerMarket.GOALS.value:
        candidates = [
            _recent_form_factor(input_features, _GOAL_RECENT_KEYS, _GOAL_BASELINE_KEYS, "goal-scoring"),
            _opponent_context_factor(input_features, "opponent_goals_conceded_avg", "team_recent_goals_avg", "goals"),
        ]
    else:
        candidates = []
    return [f for f in candidates if f is not None]


def why_model_likes_it(market_type: str, difference_pp: float, input_features: dict) -> str:
    """One sentence, deterministically generated. `difference_pp` is
    model_probability - market_reference_probability for this exact
    market/selection (the same figure shown elsewhere as the model-market
    difference) — its sign frames whether the model is more or less
    bullish than the market, and the listed factors are only ever the
    genuinely-computed comparisons above, never invented ones."""
    factors = _compute_factors(market_type, input_features)
    if difference_pp > 0:
        stance = "above"
    elif difference_pp < 0:
        stance = "below"
    else:
        stance = "in line with"

    if not factors:
        return (
            f"Model probability is {stance} the market's implied probability, but no single stored feature "
            f"(recent form vs baseline, opponent context vs baseline) stands out as an unusual driver right now."
        )

    joined = "; ".join(f.description for f in factors)
    return f"Model probability is {stance} the market's implied probability, primarily reflecting: {joined}."


def why_team_edge_exists(
    model_probability: float, secondary_model_probability: float | None, fair_market_probability: float
) -> str:
    """The team-market equivalent (Section 14 covers "team-level
    opportunities" via Section 17's merge, not just player props) — built
    only from the SAME numbers edges/calculator.py already computes
    (primary vs secondary model probability, fair market probability), no
    engineered feature comparison exists for team markets to draw on."""
    if model_probability > fair_market_probability:
        stance = "above"
    elif model_probability < fair_market_probability:
        stance = "below"
    else:
        stance = "in line with"

    sentence = f"Model probability ({model_probability:.0%}) is {stance} the market's fair probability ({fair_market_probability:.0%})"
    if secondary_model_probability is not None:
        delta = abs(model_probability - secondary_model_probability)
        if delta < 0.05:
            sentence += f"; the cross-check model agrees closely (within {delta:.0%})"
        elif delta > 0.15:
            sentence += f"; the cross-check model disagrees by {delta:.0%} — treat with extra caution"
    return sentence + "."
