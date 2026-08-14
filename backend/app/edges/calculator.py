"""Ties everything in app/edges/ together: for a match's recorded odds
quotes, derive model probability, de-vig against a pairing quote when
possible, and compute edge/confidence.

Predictions are computed live (walk-forward is cheap — well under a second
over the whole history), not persisted: this avoids a staleness problem
where a cached "model probability" for an upcoming match would silently go
stale the moment a new round of results comes in. See PoissonModelState /
rating_for_upcoming_match for the pieces this reuses rather than
re-deriving.

h2h uses Elo as the primary model (the dedicated, best-validated
match-winner model) with Poisson's h2h probability as a secondary
cross-check feeding the "model agreement" confidence factor — not blended
into a single number. Line and total use Poisson, the only model that
covers them.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.edges.confidence import ConfidenceInputs, compute_confidence, edge_tier as compute_edge_tier
from app.edges.fair_odds import expected_value as compute_expected_value, fair_odds_from_probability
from app.edges.market_probability import h2h_probability, line_probability, total_probability
from app.edges.overround import implied_probability, remove_overround
from app.modelling.data_loading import load_completed_matches
from app.modelling.elo import EloConfig, EloEngine
from app.modelling.elo_backtest import current_ratings, rating_for_upcoming_match
from app.modelling.elo_backtest import run_walk_forward as elo_walk_forward
from app.modelling.poisson_backtest import PoissonModelState
from app.modelling.poisson_backtest import run_walk_forward as poisson_walk_forward
from app.modelling.poisson_model import PoissonConfig, expected_value as poisson_expected_value, win_draw_loss
from app.models import Match, ModelRun, OddsQuote


class ModelsUnavailableError(Exception):
    """Raised when elo_cli.py / poisson_cli.py haven't been run yet, so
    there's no persisted config or validation data to compute edges from."""


@dataclass(frozen=True)
class MatchPredictions:
    """Both models' view of a match, independent of whether any odds have
    been entered for it yet — what the match detail page's "model
    probabilities" section shows, and what compute_match_edges builds on."""

    match_id: int
    elo_home_win_probability: float
    poisson_home_win_probability: float
    poisson_draw_probability: float
    poisson_away_win_probability: float
    poisson_home_expected_score: float
    poisson_away_expected_score: float
    poisson_expected_total_points: float
    poisson_expected_margin: float


@dataclass(frozen=True)
class MarketEdge:
    match_id: int
    odds_quote_id: int
    market_type: str
    selection: str
    line_value: float | None
    bookmaker_name: str
    price_decimal: float
    model_probability: float
    secondary_model_probability: float | None
    market_implied_probability: float
    fair_market_probability: float
    overround_removed: bool
    fair_odds: float
    model_edge: float
    expected_value: float
    edge_tier: str
    confidence_tier: str
    confidence_reasons: list[str]


@dataclass
class ModelContext:
    """Live-computed model state, built once and reused across every match
    being evaluated in a single request/CLI run."""

    elo_engine: EloEngine
    elo_ratings: dict[int, tuple[float, int]]
    poisson_state: PoissonModelState
    has_edge: dict[tuple[str, str], bool]  # (model_name, market_type) -> has_edge_over_naive


def build_model_context(db: Session) -> ModelContext:
    elo_run = db.scalar(select(ModelRun).where(ModelRun.model_name == "elo"))
    poisson_run = db.scalar(select(ModelRun).where(ModelRun.model_name == "poisson"))
    if elo_run is None or poisson_run is None:
        raise ModelsUnavailableError(
            "No model run found — run `python -m app.modelling.elo_cli` and "
            "`python -m app.modelling.poisson_cli` first."
        )

    matches = load_completed_matches(db)

    elo_config = EloConfig(**elo_run.config_json)
    elo_predictions = elo_walk_forward(matches, elo_config)
    elo_engine = EloEngine(elo_config)
    elo_ratings = current_ratings(elo_predictions)

    poisson_config = PoissonConfig(**poisson_run.config_json)
    _, poisson_state = poisson_walk_forward(matches, poisson_config, return_state=True)

    has_edge: dict[tuple[str, str], bool] = {}
    for run in (elo_run, poisson_run):
        for metric in run.metrics:
            has_edge[(run.model_name, metric.market_type)] = metric.has_edge_over_naive

    return ModelContext(elo_engine=elo_engine, elo_ratings=elo_ratings, poisson_state=poisson_state, has_edge=has_edge)


def compute_match_predictions(match: Match, context: ModelContext) -> MatchPredictions:
    season_year = match.season.year

    home_rating = rating_for_upcoming_match(context.elo_ratings, context.elo_engine, match.home_team_id, season_year)
    away_rating = rating_for_upcoming_match(context.elo_ratings, context.elo_engine, match.away_team_id, season_year)
    elo_home_win_prob = context.elo_engine.expected_home_win_prob(home_rating, away_rating)

    home_pmf, away_pmf = context.poisson_state.predict(match.home_team_id, match.away_team_id)
    poisson_home_win, poisson_draw, poisson_away_win = win_draw_loss(home_pmf, away_pmf)
    home_expected = poisson_expected_value(home_pmf)
    away_expected = poisson_expected_value(away_pmf)

    return MatchPredictions(
        match_id=match.id,
        elo_home_win_probability=elo_home_win_prob,
        poisson_home_win_probability=poisson_home_win,
        poisson_draw_probability=poisson_draw,
        poisson_away_win_probability=poisson_away_win,
        poisson_home_expected_score=home_expected,
        poisson_away_expected_score=away_expected,
        poisson_expected_total_points=home_expected + away_expected,
        poisson_expected_margin=home_expected - away_expected,
    )


def compute_match_edges(db: Session, match: Match, context: ModelContext) -> list[MarketEdge]:
    quotes = db.scalars(select(OddsQuote).where(OddsQuote.match_id == match.id)).all()
    if not quotes:
        return []

    home_name = match.home_team.name
    away_name = match.away_team.name

    predictions = compute_match_predictions(match, context)
    elo_home_win_prob = predictions.elo_home_win_probability
    poisson_home_win_prob = predictions.poisson_home_win_probability
    home_pmf, away_pmf = context.poisson_state.predict(match.home_team_id, match.away_team_id)

    min_games = min(
        context.poisson_state.games_played(match.home_team_id),
        context.poisson_state.games_played(match.away_team_id),
    )

    pairing_index = _index_pairings(quotes, home_name, away_name)

    edges: list[MarketEdge] = []
    for quote in quotes:
        primary_prob, secondary_prob, model_name = _model_probabilities(
            quote, home_name, away_name, elo_home_win_prob, poisson_home_win_prob, home_pmf, away_pmf
        )
        if primary_prob is None:
            continue  # e.g. selection didn't match either team — shouldn't happen given Stage 1.4's own validation, but don't crash on bad data

        fair_market_prob, overround_removed = _fair_market_probability(quote, quotes, pairing_index)
        market_implied_prob = implied_probability(quote.price_decimal)

        has_edge_for_market = context.has_edge.get((model_name, quote.market_type), False)
        confidence = compute_confidence(
            ConfidenceInputs(
                has_model_edge_over_naive=has_edge_for_market,
                overround_removed=overround_removed,
                primary_model_probability=primary_prob,
                secondary_model_probability=secondary_prob,
                min_team_games=min_games,
            )
        )

        edges.append(
            MarketEdge(
                match_id=match.id,
                odds_quote_id=quote.id,
                market_type=quote.market_type,
                selection=quote.selection,
                line_value=quote.line_value,
                bookmaker_name=quote.bookmaker.name,
                price_decimal=quote.price_decimal,
                model_probability=primary_prob,
                secondary_model_probability=secondary_prob,
                market_implied_probability=market_implied_prob,
                fair_market_probability=fair_market_prob,
                overround_removed=overround_removed,
                fair_odds=fair_odds_from_probability(primary_prob),
                model_edge=primary_prob - fair_market_prob,
                expected_value=compute_expected_value(primary_prob, quote.price_decimal),
                edge_tier=compute_edge_tier(primary_prob, fair_market_prob),
                confidence_tier=confidence.tier,
                confidence_reasons=confidence.reasons,
            )
        )

    return edges


def _model_probabilities(quote, home_name, away_name, elo_home_prob, poisson_home_prob, home_pmf, away_pmf):
    """Returns (primary_probability, secondary_probability, primary_model_name)."""
    if quote.market_type == "h2h":
        try:
            primary = h2h_probability(elo_home_prob, quote.selection, home_name, away_name)
            secondary = h2h_probability(poisson_home_prob, quote.selection, home_name, away_name)
        except ValueError:
            return None, None, "elo"
        return primary, secondary, "elo"

    if quote.market_type == "line":
        if quote.line_value is None:
            return None, None, "poisson"
        try:
            primary = line_probability(home_pmf, away_pmf, quote.selection, quote.line_value, home_name, away_name)
        except ValueError:
            return None, None, "poisson"
        return primary, None, "poisson"

    if quote.market_type == "total":
        if quote.line_value is None:
            return None, None, "poisson"
        try:
            primary = total_probability(home_pmf, away_pmf, quote.selection, quote.line_value)
        except ValueError:
            return None, None, "poisson"
        return primary, None, "poisson"

    return None, None, "unknown"


def _index_pairings(quotes: list[OddsQuote], home_name: str, away_name: str) -> dict[int, OddsQuote]:
    """Maps each quote's id to its de-vig "pairing" quote — same bookmaker,
    same market, the OTHER side of the same book — if one was entered.
    """
    by_bookmaker_market: dict[tuple[int, str], list[OddsQuote]] = {}
    for q in quotes:
        by_bookmaker_market.setdefault((q.bookmaker_id, q.market_type), []).append(q)

    pairing: dict[int, OddsQuote] = {}
    for (_bookmaker_id, market_type), group in by_bookmaker_market.items():
        if market_type in ("h2h", "line"):
            for q in group:
                other_name = away_name if q.selection == home_name else home_name if q.selection == away_name else None
                if other_name is None:
                    continue
                match_candidates = [c for c in group if c.selection == other_name]
                if match_candidates:
                    pairing[q.id] = match_candidates[0]
        elif market_type == "total":
            for q in group:
                other_side = "under" if q.selection.lower() == "over" else "over" if q.selection.lower() == "under" else None
                if other_side is None:
                    continue
                match_candidates = [c for c in group if c.selection.lower() == other_side and c.line_value == q.line_value]
                if match_candidates:
                    pairing[q.id] = match_candidates[0]

    return pairing


def _fair_market_probability(quote: OddsQuote, quotes: list[OddsQuote], pairing_index: dict[int, OddsQuote]) -> tuple[float, bool]:
    pairing = pairing_index.get(quote.id)
    if pairing is None:
        return implied_probability(quote.price_decimal), False

    fair = remove_overround({"this": quote.price_decimal, "other": pairing.price_decimal})
    return fair["this"], True


_CONFIDENCE_RANK = {"insufficient_data": 0, "lower": 1, "moderate": 2, "higher": 3}
_EDGE_RANK = {"none": 0, "weak": 1, "moderate": 2, "strong": 3}


def best_edge(edges: list[MarketEdge]) -> MarketEdge | None:
    """The single edge worth surfacing on a dashboard summary, if any.

    Ranked by confidence first, edge size second: a low-confidence "strong"
    edge is exactly the kind of thing that looks compelling in isolation but
    shouldn't be the headline number — see app/edges/confidence.py. Callers
    that want the full picture should still fetch all edges for a match
    (e.g. via GET /api/matches/{id}/edges), not just this one.
    """
    if not edges:
        return None
    return max(edges, key=lambda e: (_CONFIDENCE_RANK[e.confidence_tier], _EDGE_RANK[e.edge_tier], e.expected_value))
