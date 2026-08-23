"""Team-market pricing — pure model belief, independent of any bookmaker.

Reuses the exact same Elo/Poisson engine already validated in
app/edges/calculator.py; nothing here re-fits or re-derives a team model.
H2H/expected margin/expected total come straight from
compute_match_predictions(); line/total probability at an ARBITRARY
requested handicap/line reuses app/edges/market_probability.py's existing
arbitrary-line math, fed by the same Poisson team-strength state
calculator.py already builds once per request (see build_model_context —
"walk-forward is cheap, well under a second over the whole history",
cached per-Session so pricing a whole round only pays this once).
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.edges.calculator import MatchPredictions, ModelContext, compute_match_predictions
from app.edges.fair_odds import fair_odds_from_probability
from app.edges.market_probability import line_probability, total_probability
from app.models import Match, MatchStatus

# Team pricing model identity for the prospective evaluation dataset
# (app/models/pricing_snapshot.py) - bump only when the underlying Elo/
# Poisson ModelRun config actually changes, not on every request.
TEAM_MODEL_NAME = "elo_poisson"
TEAM_MODEL_VERSION = "elo_poisson-v1"


@dataclass(frozen=True)
class LinePrice:
    line_value: float
    home_team: str
    away_team: str
    home_probability: float
    away_probability: float
    home_fair_odds: float
    away_fair_odds: float


@dataclass(frozen=True)
class TotalPrice:
    line_value: float
    over_probability: float
    under_probability: float
    over_fair_odds: float
    under_fair_odds: float


@dataclass(frozen=True)
class TeamMarketPrice:
    match_id: int
    home_team: str
    away_team: str
    model_name: str
    model_version: str
    generated_at: datetime
    data_cutoff: datetime
    confidence_tier: str

    home_win_probability: float
    draw_probability: float
    away_win_probability: float
    home_fair_odds: float
    draw_fair_odds: float
    away_fair_odds: float

    expected_margin: float
    expected_total_points: float
    home_expected_score: float
    away_expected_score: float

    lines: list[LinePrice] = field(default_factory=list)
    totals: list[TotalPrice] = field(default_factory=list)


def latest_completed_match_timestamp(db: Session) -> datetime | None:
    """The team model's own "data cutoff" - the most recent completed
    match that could have fed the current Elo ratings/Poisson team-
    strength state. Computed once per request/round by the caller, not
    once per match."""
    return db.scalar(select(func.max(Match.scheduled_start)).where(Match.status == MatchStatus.COMPLETED))


def _confidence_tier(context: ModelContext) -> str:
    """A team price's "confidence" (item 1's requirement) is grounded in
    whether the underlying model actually demonstrated an edge over a
    naive baseline in its own holdout backtest (ModelRun.metrics,
    surfaced here via context.has_edge - already computed by
    build_model_context, nothing new derived) - not a subjective label."""
    if context.has_edge.get(("elo", "h2h")):
        return "validated_edge_over_naive"
    return "no_demonstrated_edge_over_naive"


def price_team_market(
    match: Match,
    context: ModelContext,
    generated_at: datetime,
    data_cutoff: datetime,
    line_values: list[float] | None = None,
    total_lines: list[float] | None = None,
) -> TeamMarketPrice:
    """Prices h2h always; line/total prices only for the requested handicap/
    line values (the engine can price ANY handicap or total line on demand —
    see module docstring — so a caller not interested in lines simply
    requests none, rather than the engine guessing a default set).
    `generated_at`/`data_cutoff` are supplied by the caller (computed once
    per request/round, not per match) rather than each match re-deriving
    "now" and re-querying the latest completed match independently."""
    predictions: MatchPredictions = compute_match_predictions(match, context)
    home_name, away_name = match.home_team.name, match.away_team.name

    # A coherent 3-way price (home/draw/away summing to exactly 1) needs a
    # draw probability, which Elo has no concept of - Poisson is the only
    # one of the two models that produces one. Elo is still the PRIMARY
    # win/loss signal (the better-validated h2h model - see
    # app/edges/calculator.py's module docstring and the earlier audit's
    # backtest finding), so rather than switching wholesale to Poisson's
    # own (less-validated) win/draw/loss split, Elo's win/loss split is
    # rescaled to leave room for Poisson's draw probability. A deliberate,
    # documented construction - not an undisclosed blend of the two models.
    draw_p = predictions.poisson_draw_probability
    home_p = predictions.elo_home_win_probability * (1.0 - draw_p)
    away_p = (1.0 - predictions.elo_home_win_probability) * (1.0 - draw_p)

    home_pmf, away_pmf = context.poisson_state.predict(match.home_team_id, match.away_team_id)

    lines: list[LinePrice] = []
    for lv in line_values or []:
        home_p_line = line_probability(home_pmf, away_pmf, home_name, lv, home_name, away_name)
        away_p_line = line_probability(home_pmf, away_pmf, away_name, -lv, home_name, away_name)
        lines.append(LinePrice(
            line_value=lv, home_team=home_name, away_team=away_name,
            home_probability=home_p_line, away_probability=away_p_line,
            home_fair_odds=fair_odds_from_probability(home_p_line), away_fair_odds=fair_odds_from_probability(away_p_line),
        ))

    totals: list[TotalPrice] = []
    for tl in total_lines or []:
        over_p = total_probability(home_pmf, away_pmf, "over", tl)
        under_p = 1.0 - over_p
        totals.append(TotalPrice(
            line_value=tl, over_probability=over_p, under_probability=under_p,
            over_fair_odds=fair_odds_from_probability(over_p), under_fair_odds=fair_odds_from_probability(under_p),
        ))

    return TeamMarketPrice(
        match_id=match.id, home_team=home_name, away_team=away_name,
        model_name=TEAM_MODEL_NAME, model_version=TEAM_MODEL_VERSION,
        generated_at=generated_at, data_cutoff=data_cutoff, confidence_tier=_confidence_tier(context),
        home_win_probability=home_p,
        draw_probability=draw_p,
        away_win_probability=away_p,
        home_fair_odds=fair_odds_from_probability(home_p),
        draw_fair_odds=fair_odds_from_probability(draw_p) if draw_p > 0 else float("inf"),
        away_fair_odds=fair_odds_from_probability(away_p),
        expected_margin=predictions.poisson_expected_margin,
        expected_total_points=predictions.poisson_expected_total_points,
        home_expected_score=predictions.poisson_home_expected_score,
        away_expected_score=predictions.poisson_away_expected_score,
        lines=lines, totals=totals,
    )
