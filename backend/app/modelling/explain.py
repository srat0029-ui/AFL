"""Deterministic, per-prediction explanation of the logistic model's
output — which features pushed the home-team probability up or down for
one specific match, and by how much (in log-odds). Generated directly from
the fitted model's own coefficients and that match's actual (standardized)
feature values — no LLM, no free-text generation, nothing beyond a
straightforward readout of real model internals.

Language is deliberately associational ("associated with a higher model
probability"), never causal ("caused them to win") — a logistic-regression
coefficient describes a statistical association learned from historical
data, not a mechanism, and should never be presented as one.
"""

from dataclasses import dataclass

from app.modelling.features import MatchFeatureRow
from app.modelling.logistic import feature_matrix

FEATURE_DESCRIPTIONS: dict[str, str] = {
    "form_diff_5": "recent form (last 5 games)",
    "form_diff_10": "recent form (last 10 games)",
    "margin_diff_5": "average winning margin (last 5 games)",
    "margin_diff_10": "average winning margin (last 10 games)",
    "points_for_diff_5": "recent scoring output (last 5 games)",
    "points_against_diff_5": "recent defensive scoring allowed (last 5 games)",
    "clearance_differential_diff": "clearance differential",
    "inside50_differential_diff": "inside-50 differential",
    "contested_possession_differential_diff": "contested-possession differential",
    "tackle_differential_diff": "tackle differential",
    "conversion_rate_diff": "goal conversion accuracy",
    "marks_inside_50_diff": "marks inside 50",
    "league_home_win_rate": "league-wide home-ground advantage",
    "elo_home_win_probability": "Elo rating advantage",
}


@dataclass(frozen=True)
class FeatureContribution:
    feature_name: str
    description: str
    standardized_value: float
    coefficient: float
    log_odds_contribution: float  # standardized_value * coefficient


@dataclass(frozen=True)
class MatchExplanation:
    match_id: int
    home_win_probability: float
    intercept_log_odds: float
    contributions: list[FeatureContribution]  # sorted by |log_odds_contribution| descending

    def factors_increasing(self) -> list[FeatureContribution]:
        return [c for c in self.contributions if c.log_odds_contribution > 0]

    def factors_decreasing(self) -> list[FeatureContribution]:
        return [c for c in self.contributions if c.log_odds_contribution < 0]

    def to_text(self) -> str:
        lines = [f"Home win probability: {self.home_win_probability:.1%}", ""]
        increasing = self.factors_increasing()
        if increasing:
            lines.append("Factors associated with a higher model probability for the home team:")
            lines.extend(f"  - {c.description} ({c.log_odds_contribution:+.3f} log-odds)" for c in increasing)
        decreasing = self.factors_decreasing()
        if decreasing:
            lines.append("Factors associated with a lower model probability for the home team:")
            lines.extend(f"  - {c.description} ({c.log_odds_contribution:+.3f} log-odds)" for c in decreasing)
        lines.append("")
        lines.append(
            "These are statistical associations in historical data, not causal claims — "
            "they describe what the model weighted, not what determined the result."
        )
        return "\n".join(lines)


def explain_prediction(pipeline, row: MatchFeatureRow, feature_names: tuple[str, ...]) -> MatchExplanation:
    X = feature_matrix([row], feature_names)
    X_imputed = pipeline.named_steps["impute"].transform(X)
    X_scaled = pipeline.named_steps["scale"].transform(X_imputed)
    logreg = pipeline.named_steps["logreg"]
    coefs = logreg.coef_[0]
    intercept = float(logreg.intercept_[0])

    class_index = list(pipeline.classes_).index(1.0)
    prob = float(pipeline.predict_proba(X)[:, class_index][0])

    contributions = []
    for i, name in enumerate(feature_names):
        standardized_value = float(X_scaled[0][i])
        coefficient = float(coefs[i])
        contributions.append(
            FeatureContribution(
                feature_name=name,
                description=FEATURE_DESCRIPTIONS.get(name, name),
                standardized_value=standardized_value,
                coefficient=coefficient,
                log_odds_contribution=standardized_value * coefficient,
            )
        )
    contributions.sort(key=lambda c: abs(c.log_odds_contribution), reverse=True)

    return MatchExplanation(
        match_id=row.match_id, home_win_probability=prob, intercept_log_odds=intercept, contributions=contributions
    )
