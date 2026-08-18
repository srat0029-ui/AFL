"""Projection-vs-line distance (Weekly Bet Review + Decision Support
stage, Section 6) — "do not only show probability": for line/total and
player markets, shows the model's own POINT projection relative to the
offered line, in the market's own units (points, disposals, goals), not
just a probability.

Team line: the model's Poisson expected margin (already computed live for
every upcoming match via app/edges/calculator.py's compute_match_predictions
- no new model math here) reoriented to the SELECTED team's perspective,
compared against the handicap. Positive distance = model projects
clearing the line; negative = model projects falling short.

Team total: the model's expected combined score vs the total line, signed
so a positive distance always means "in the selection's favour" (exceeding
the line for an "over" selection, staying under it for "under").

Player: the projection's own predicted_mean vs the market threshold,
directly in stat units (already available on every player projection —
Section 6's own worked example, "27.8 vs 24.5 = +3.3 disposals").
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.edges.calculator import ModelContext, ModelsUnavailableError, build_model_context, compute_match_predictions
from app.models import Match, PlayerDisposalProjection, PlayerGoalProjection


@dataclass(frozen=True)
class ProjectionLineDistance:
    market_type: str
    model_projection: float
    line_value: float
    distance: float  # positive = model projects the selection clearing the line; negative = falling short
    unit: str  # "points" | "disposals" | "goals"


def team_line_distance(*, expected_margin_for_selection: float, line_value: float) -> ProjectionLineDistance:
    """expected_margin_for_selection: the model's expected margin (positive
    = wins by that much) from the SELECTED team's own perspective - the
    caller is responsible for flipping app.edges.calculator's
    poisson_expected_margin (home - away) when the selection is the away
    team. line_value follows this app's existing handicap convention
    (negative = the selection must win by more than |line_value|)."""
    distance = expected_margin_for_selection + line_value
    return ProjectionLineDistance(
        market_type="line", model_projection=expected_margin_for_selection, line_value=line_value, distance=distance, unit="points"
    )


def team_total_distance(*, expected_total_points: float, line_value: float, selection: str) -> ProjectionLineDistance:
    """selection: "over" or "under" (this app's existing total-market
    selection vocabulary - see app/api/schemas.py's MarketType)."""
    raw_distance = expected_total_points - line_value
    distance = raw_distance if selection == "over" else -raw_distance
    return ProjectionLineDistance(
        market_type="total", model_projection=expected_total_points, line_value=line_value, distance=distance, unit="points"
    )


def player_threshold_distance(*, predicted_mean: float, threshold: float, market_type: str) -> ProjectionLineDistance:
    unit = "disposals" if market_type == "player_disposals" else "goals"
    return ProjectionLineDistance(
        market_type=market_type, model_projection=predicted_mean, line_value=threshold, distance=predicted_mean - threshold, unit=unit
    )


def projection_line_distance_for_opportunity(
    db: Session, opportunity: dict, model_context: ModelContext | None = None
) -> ProjectionLineDistance | None:
    """The one entry point callers (Weekly Review comparison table,
    deep-audit report) should use - dispatches on market_type and fetches
    whatever live projection each market type needs. `model_context` may
    be passed in to reuse an already-built one across many opportunities
    in the same request rather than rebuilding Elo/Poisson state per row."""
    market_type = opportunity["market_type"]

    if market_type in ("line", "total"):
        match = db.get(Match, opportunity["match_id"])
        if match is None:
            return None
        context = model_context
        if context is None:
            try:
                context = build_model_context(db)
            except ModelsUnavailableError:
                return None
        predictions = compute_match_predictions(match, context)
        if market_type == "line":
            is_home = opportunity["selection"] == match.home_team.name
            margin_for_selection = predictions.poisson_expected_margin if is_home else -predictions.poisson_expected_margin
            return team_line_distance(expected_margin_for_selection=margin_for_selection, line_value=opportunity["line_value"])
        return team_total_distance(
            expected_total_points=predictions.poisson_expected_total_points, line_value=opportunity["line_value"], selection=opportunity["selection"]
        )

    if market_type in ("player_disposals", "player_goals"):
        if market_type == "player_disposals":
            proj = db.scalar(
                select(PlayerDisposalProjection).where(
                    PlayerDisposalProjection.match_id == opportunity["match_id"], PlayerDisposalProjection.player_id == opportunity["player_id"]
                )
            )
        else:
            proj = db.scalar(
                select(PlayerGoalProjection).where(
                    PlayerGoalProjection.match_id == opportunity["match_id"], PlayerGoalProjection.player_id == opportunity["player_id"]
                )
            )
        if proj is None:
            return None
        return player_threshold_distance(predicted_mean=proj.predicted_mean, threshold=opportunity["threshold"], market_type=market_type)

    return None  # h2h has no line - probability is the whole story there


def projection_line_distance_as_dict(d: ProjectionLineDistance) -> dict:
    return {
        "market_type": d.market_type,
        "model_projection": d.model_projection,
        "line_value": d.line_value,
        "distance": d.distance,
        "unit": d.unit,
    }
