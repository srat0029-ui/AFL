"""Real ROI / win rate / yield / P&L, computed only from odds actually
logged (via Stage 1.4/1.6) against matches that have since been completed.

This is deliberately separate from app/backtesting/model_report.py: that
module validates probability estimates against outcomes and needs no market
data at all; this one validates betting profitability and needs real prices,
which barely exist yet (no historical AFL odds source is available — see
the architecture notes). It reports honestly on however many resolved
selections actually exist, including zero, rather than ever fabricating
data to fill the report. It activates automatically as odds get logged on
upcoming fixtures and those fixtures play out.

h2h bets on a draw are treated as void (refunded, not counted as a loss) —
matching how bookmakers actually settle drawn head-to-head markets.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.edges.market_probability import h2h_probability, line_probability, total_probability
from app.modelling.data_loading import load_completed_matches
from app.modelling.elo import EloConfig
from app.modelling.elo_backtest import EloPrediction
from app.modelling.elo_backtest import run_walk_forward as elo_walk_forward
from app.modelling.metrics import brier_score, log_loss
from app.modelling.poisson_backtest import PoissonPrediction
from app.modelling.poisson_backtest import run_walk_forward as poisson_walk_forward
from app.modelling.poisson_model import PoissonConfig, score_distribution
from app.models import Match, MatchStatus, ModelRun, OddsQuote


class ModelsUnavailableError(Exception):
    """Raised when elo_cli.py / poisson_cli.py haven't been run yet."""


@dataclass(frozen=True)
class TrackedSelectionResult:
    match_id: int
    market_type: str
    selection: str
    line_value: float | None
    bookmaker_name: str
    price_decimal: float
    is_closing_line: bool
    model_probability: float
    won: bool | None  # None = void (e.g. h2h draw)
    pnl_units: float


@dataclass(frozen=True)
class LoggedOddsReport:
    n_total: int
    n_resolved: int
    n_void: int
    win_rate: float | None
    roi_pct: float | None
    yield_pct: float | None
    total_pnl_units: float
    brier_score: float | None
    log_loss: float | None
    selections: list[TrackedSelectionResult] = field(default_factory=list)


def actual_outcome_for_quote(match: Match, quote: OddsQuote) -> bool | None:
    """True if this selection won, False if it lost, None if void/push
    (currently: only a drawn h2h market) or the match isn't resolved yet."""
    if match.status != MatchStatus.COMPLETED or match.home_score is None or match.away_score is None:
        return None

    home_name, away_name = match.home_team.name, match.away_team.name
    actual_margin = match.home_score - match.away_score
    actual_total = match.home_score + match.away_score

    if quote.market_type == "h2h":
        if actual_margin == 0:
            return None  # drawn h2h markets are void at essentially every AU bookmaker
        if quote.selection == home_name:
            return actual_margin > 0
        if quote.selection == away_name:
            return actual_margin < 0
        return None

    if quote.market_type == "line":
        if quote.line_value is None:
            return None
        if quote.selection == home_name:
            return actual_margin > -quote.line_value
        if quote.selection == away_name:
            return actual_margin < quote.line_value
        return None

    if quote.market_type == "total":
        if quote.line_value is None:
            return None
        if quote.selection.lower() == "over":
            return actual_total > quote.line_value
        if quote.selection.lower() == "under":
            return actual_total < quote.line_value
        return None

    return None


def _model_probability_for_quote(
    quote: OddsQuote,
    match: Match,
    elo_by_match: dict[int, EloPrediction],
    poisson_by_match: dict[int, PoissonPrediction],
    poisson_config: PoissonConfig,
) -> float | None:
    home_name, away_name = match.home_team.name, match.away_team.name

    if quote.market_type == "h2h":
        elo_pred = elo_by_match.get(match.id)
        if elo_pred is None:
            return None
        try:
            return h2h_probability(elo_pred.home_win_probability, quote.selection, home_name, away_name)
        except ValueError:
            return None

    poisson_pred = poisson_by_match.get(match.id)
    if poisson_pred is None or quote.line_value is None:
        return None
    home_pmf = score_distribution(
        poisson_pred.home_expected_goals, poisson_pred.home_expected_behinds, poisson_config.max_goals, poisson_config.max_behinds
    )
    away_pmf = score_distribution(
        poisson_pred.away_expected_goals, poisson_pred.away_expected_behinds, poisson_config.max_goals, poisson_config.max_behinds
    )
    try:
        if quote.market_type == "line":
            return line_probability(home_pmf, away_pmf, quote.selection, quote.line_value, home_name, away_name)
        if quote.market_type == "total":
            return total_probability(home_pmf, away_pmf, quote.selection, quote.line_value)
    except ValueError:
        return None
    return None


def build_logged_odds_report(db: Session) -> LoggedOddsReport:
    elo_run = db.scalar(select(ModelRun).where(ModelRun.model_name == "elo"))
    poisson_run = db.scalar(select(ModelRun).where(ModelRun.model_name == "poisson"))
    if elo_run is None or poisson_run is None:
        raise ModelsUnavailableError(
            "Run `python -m app.modelling.elo_cli` and `python -m app.modelling.poisson_cli` first."
        )

    completed = load_completed_matches(db)
    elo_predictions = elo_walk_forward(completed, EloConfig(**elo_run.config_json))
    poisson_config = PoissonConfig(**poisson_run.config_json)
    poisson_predictions = poisson_walk_forward(completed, poisson_config)
    elo_by_match = {p.match_id: p for p in elo_predictions}
    poisson_by_match = {p.match_id: p for p in poisson_predictions}

    quotes = db.scalars(
        select(OddsQuote).join(Match, OddsQuote.match_id == Match.id).where(Match.status == MatchStatus.COMPLETED)
    ).all()

    selections: list[TrackedSelectionResult] = []
    for quote in quotes:
        match = quote.match
        won = actual_outcome_for_quote(match, quote)
        model_prob = _model_probability_for_quote(quote, match, elo_by_match, poisson_by_match, poisson_config)
        if model_prob is None:
            continue  # can't score this selection without a model probability (e.g. selection didn't match a team)

        if won is True:
            pnl = quote.price_decimal - 1.0
        elif won is False:
            pnl = -1.0
        else:
            pnl = 0.0

        selections.append(
            TrackedSelectionResult(
                match_id=match.id,
                market_type=quote.market_type,
                selection=quote.selection,
                line_value=quote.line_value,
                bookmaker_name=quote.bookmaker.name,
                price_decimal=quote.price_decimal,
                is_closing_line=quote.is_closing_line,
                model_probability=model_prob,
                won=won,
                pnl_units=pnl,
            )
        )

    resolved = [s for s in selections if s.won is not None]
    void_count = len(selections) - len(resolved)
    total_pnl = sum(s.pnl_units for s in selections)

    win_rate = sum(1 for s in resolved if s.won) / len(resolved) if resolved else None
    roi_pct = (total_pnl / len(resolved) * 100) if resolved else None
    yield_pct = roi_pct  # equivalent under flat (1-unit) staking, which is all that's supported so far

    resolved_probs = [s.model_probability for s in resolved]
    resolved_outcomes = [1.0 if s.won else 0.0 for s in resolved]
    brier = brier_score(resolved_probs, resolved_outcomes) if resolved else None
    logloss = log_loss(resolved_probs, resolved_outcomes) if resolved else None

    return LoggedOddsReport(
        n_total=len(selections),
        n_resolved=len(resolved),
        n_void=void_count,
        win_rate=win_rate,
        roi_pct=roi_pct,
        yield_pct=yield_pct,
        total_pnl_units=total_pnl,
        brier_score=brier,
        log_loss=logloss,
        selections=selections,
    )
