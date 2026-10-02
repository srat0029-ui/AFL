"""The NBA prospective pipeline: record a projection, freeze a prediction
against the market before tip-off, capture the closing line once the market
has closed, settle once the game is final.

Each step writes only its own part of the record and is guarded twice —
here, by explicit checks that raise before anything is written, and at the
ORM layer by app/core/prospective.py's write-once guard, so a future caller
that skips these functions still cannot rewrite history through the ORM.

The four kinds of knowledge stay separate throughout:

    information at prediction time -> NbaPropProjection (information_cutoff)
    entry line and odds            -> NbaPropPrediction entry_* columns
    closing line and odds          -> NbaPropPrediction closing_* columns
    actual outcome                 -> NbaPropPrediction settlement columns

Nothing here decides which predictions are worth betting. A recommendation
policy does not exist yet; when it does, its verdict must be frozen on the
row at entry time like everything else, never applied retrospectively.

Single-row functions flush but do not commit (the caller owns the
transaction); the batch functions commit.
"""

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.prospective import InformationLeakError, ensure_information_available, ensure_utc
from app.core.settlement import LINE_TYPE_OVER_UNDER, RESULT_LOST, RESULT_PUSH, RESULT_UNRESOLVED, RESULT_VOID, RESULT_WON, settle_selection
from app.edges.fair_odds import expected_value, fair_odds_from_probability
from app.edges.overround import remove_overround
from app.models import Bookmaker
from app.models.bookmaker import ELIGIBILITY_INCLUDED
from app.models.nba import NbaGame, NbaGameStatus, NbaPlayerGameLog, NbaPropPrediction, NbaPropProjection, NbaPropQuote
from app.nba.asof import latest_quotes_known_at
from app.nba.markets import SELECTION_OVER, SELECTION_UNDER, SELECTIONS, actual_stat_value
from app.nba.projection import ProjectionOutput, StatDistribution, selection_probability


def _now(now: datetime | None) -> datetime:
    return ensure_utc(now) if now is not None else datetime.now(timezone.utc)


def _ensure_before_tipoff(game: NbaGame, now: datetime, *, what: str) -> None:
    if game.status != NbaGameStatus.SCHEDULED.value:
        raise InformationLeakError(f"{what} requires a scheduled game, but game {game.id} is {game.status!r}")
    if now >= ensure_utc(game.scheduled_start):
        raise InformationLeakError(f"{what} at {now.isoformat()} is not before tip-off {ensure_utc(game.scheduled_start).isoformat()}")


# --- 1. Projection ---------------------------------------------------------


def record_projection(
    db: Session,
    *,
    game: NbaGame,
    player_id: int,
    team_id: int,
    model_name: str,
    model_version: str,
    information_cutoff: datetime,
    output: ProjectionOutput,
    now: datetime | None = None,
) -> NbaPropProjection:
    """Persist one model output for a game that has not tipped off.
    Idempotent on (game, player, market, model_version, information_cutoff)."""
    now = _now(now)
    _ensure_before_tipoff(game, now, what="recording a projection")
    ensure_information_available(information_cutoff, now, what="the projection's information cutoff")
    cutoff = ensure_utc(information_cutoff)

    existing = db.scalar(
        select(NbaPropProjection).where(
            NbaPropProjection.game_id == game.id,
            NbaPropProjection.player_id == player_id,
            NbaPropProjection.market == output.market.value,
            NbaPropProjection.model_version == model_version,
            NbaPropProjection.information_cutoff == cutoff,
        )
    )
    if existing is not None:
        return existing

    projection = NbaPropProjection(
        game_id=game.id,
        player_id=player_id,
        team_id=team_id,
        market=output.market.value,
        model_name=model_name,
        model_version=model_version,
        generated_at=now,
        information_cutoff=cutoff,
        expected_minutes=output.expected_minutes,
        rate_per_minute=output.rate_per_minute,
        predicted_mean=output.distribution.mean(),
        distribution_kind=output.distribution_kind,
        distribution_params=output.distribution_params,
        games_of_history=output.games_of_history,
        inputs=output.inputs,
    )
    db.add(projection)
    db.flush()
    return projection


# --- Market view shared by the entry and closing steps ---------------------


@dataclass(frozen=True)
class MarketView:
    """The market for one side of one line at one instant, restricted to
    bookmakers eligible for best-price purposes (exchanges and excluded
    books never set the best price or enter the consensus — the same
    eligibility rule AFL applies, read from the same `bookmakers` table)."""

    best_quote: NbaPropQuote | None
    consensus_probability: float | None
    n_bookmakers: int | None


def _eligible_bookmaker_ids(db: Session, bookmaker_ids: set[int]) -> set[int]:
    if not bookmaker_ids:
        return set()
    return set(db.scalars(select(Bookmaker.id).where(Bookmaker.id.in_(bookmaker_ids), Bookmaker.eligibility == ELIGIBILITY_INCLUDED)).all())


def market_view(quotes: list[NbaPropQuote], eligible_bookmaker_ids: set[int], *, line: float, selection: str) -> MarketView:
    """`quotes` must already be the latest-per-(bookmaker, line, selection)
    set from asof.latest_quotes_known_at.

    Consensus is the unweighted mean of each bookmaker's own de-vigged
    probability, and only bookmakers quoting BOTH sides of this exact line
    contribute — a one-sided price still carries the margin, so it is left
    out rather than mixed in as if it were fair."""
    other = SELECTION_UNDER if selection == SELECTION_OVER else SELECTION_OVER
    by_book: dict[int, dict[str, NbaPropQuote]] = {}
    for quote in quotes:
        if quote.line == line and quote.bookmaker_id in eligible_bookmaker_ids:
            by_book.setdefault(quote.bookmaker_id, {})[quote.selection] = quote

    this_side = [sides[selection] for sides in by_book.values() if selection in sides]
    best = max(this_side, key=lambda q: (q.price_decimal, -q.bookmaker_id), default=None)

    devigged = [
        remove_overround({selection: sides[selection].price_decimal, other: sides[other].price_decimal})[selection]
        for sides in by_book.values()
        if selection in sides and other in sides
    ]
    return MarketView(
        best_quote=best,
        consensus_probability=statistics.fmean(devigged) if devigged else None,
        n_bookmakers=len(devigged) if devigged else None,
    )


def _market_view_at(db: Session, prediction_key: tuple[int, int, str], cutoff: datetime, *, line: float, selection: str, max_age: timedelta | None):
    game_id, player_id, market = prediction_key
    quotes = latest_quotes_known_at(db, game_id=game_id, player_id=player_id, market=market, cutoff=cutoff, max_age=max_age)
    eligible = _eligible_bookmaker_ids(db, {q.bookmaker_id for q in quotes})
    return quotes, market_view(quotes, eligible, line=line, selection=selection)


# --- 2. Entry --------------------------------------------------------------


def freeze_prediction(
    db: Session,
    *,
    projection: NbaPropProjection,
    distribution: StatDistribution,
    line: float,
    selection: str,
    now: datetime | None = None,
    max_quote_age: timedelta | None = None,
) -> NbaPropPrediction:
    """Freeze the model's probability for one side of one line together
    with the market as it stands right now. Must happen before tip-off.
    Idempotent on (projection, line, selection): a repeat call returns the
    row frozen the first time, untouched.

    `distribution` is the model's distribution for `projection` — the
    probability is computed here from it rather than accepted as a bare
    number, so the frozen probability cannot disagree with the line it is
    frozen against."""
    if selection not in SELECTIONS:
        raise ValueError(f"selection must be one of {SELECTIONS}, got {selection!r}")
    now = _now(now)
    game = projection.game
    _ensure_before_tipoff(game, now, what="freezing a prediction")
    ensure_information_available(projection.information_cutoff, now, what="the projection's information cutoff")

    existing = db.scalar(
        select(NbaPropPrediction).where(
            NbaPropPrediction.projection_id == projection.id, NbaPropPrediction.line == line, NbaPropPrediction.selection == selection
        )
    )
    if existing is not None:
        return existing

    probability = selection_probability(distribution, line, selection)
    if not (0.0 < probability < 1.0):
        raise ValueError(f"model probability must be strictly between 0 and 1 to be priced, got {probability!r}")

    _, view = _market_view_at(db, (game.id, projection.player_id, projection.market), now, line=line, selection=selection, max_age=max_quote_age)
    best = view.best_quote

    prediction = NbaPropPrediction(
        projection_id=projection.id,
        game_id=game.id,
        player_id=projection.player_id,
        market=projection.market,
        line=line,
        selection=selection,
        model_name=projection.model_name,
        model_version=projection.model_version,
        information_cutoff=projection.information_cutoff,
        model_probability=probability,
        model_fair_odds=fair_odds_from_probability(probability),
        predicted_at=now,
        tipoff_at_prediction=game.scheduled_start,
        entry_bookmaker_id=best.bookmaker_id if best else None,
        entry_quote_id=best.id if best else None,
        entry_price=best.price_decimal if best else None,
        entry_quote_observed_at=best.observed_at if best else None,
        entry_consensus_probability=view.consensus_probability,
        entry_n_bookmakers=view.n_bookmakers,
        entry_expected_value=expected_value(probability, best.price_decimal) if best else None,
    )
    db.add(prediction)
    db.flush()
    return prediction


# --- 3. Closing line -------------------------------------------------------


def capture_closing_line(db: Session, prediction: NbaPropPrediction, *, now: datetime | None = None, max_quote_age: timedelta | None = None) -> bool:
    """Record the market at tip-off for an already-frozen prediction.
    Returns False (writing nothing) when it has already been captured or the
    game is postponed/cancelled; raises if the market has not closed yet.

    - closing_price: the ENTRY bookmaker's last price for the SAME line and
      side — the like-for-like comparison price CLV needs. NULL if that
      bookmaker no longer quoted that line at the close.
    - closing_main_line: the entry bookmaker's main line at the close, so a
      moved line is visible even when closing_price is NULL.
    - closing consensus: de-vigged across eligible bookmakers, same line.
    """
    if prediction.closing_captured_at is not None:
        return False
    game = prediction.game
    if game.status in (NbaGameStatus.POSTPONED.value, NbaGameStatus.CANCELLED.value):
        return False
    now = _now(now)
    tipoff = ensure_utc(game.scheduled_start)
    if now < tipoff:
        raise InformationLeakError(f"the market for game {game.id} has not closed: tip-off is {tipoff.isoformat()}, now is {now.isoformat()}")

    quotes, view = _market_view_at(
        db, (prediction.game_id, prediction.player_id, prediction.market), tipoff, line=prediction.line, selection=prediction.selection, max_age=max_quote_age
    )
    same_line: NbaPropQuote | None = None
    main_line: NbaPropQuote | None = None
    for quote in quotes:
        if quote.bookmaker_id != prediction.entry_bookmaker_id or quote.selection != prediction.selection:
            continue
        if quote.line == prediction.line:
            same_line = quote
        if not quote.is_alternate_line:
            main_line = quote

    prediction.closing_main_line = main_line.line if main_line else None
    prediction.closing_price = same_line.price_decimal if same_line else None
    prediction.closing_quote_id = same_line.id if same_line else None
    prediction.closing_quote_observed_at = same_line.observed_at if same_line else None
    prediction.closing_consensus_probability = view.consensus_probability
    prediction.closing_n_bookmakers = view.n_bookmakers
    prediction.closing_captured_at = now
    db.flush()
    return True


# --- 4. Settlement ---------------------------------------------------------

SETTLE_SETTLED = "settled"
SETTLE_ALREADY_SETTLED = "already_settled"
SETTLE_AWAITING_RESULT = "awaiting_result"


def settle_prediction(db: Session, prediction: NbaPropPrediction, *, now: datetime | None = None) -> str:
    """Settle one prediction against the final box score, exactly once.

    Stays pending (nothing written) while the game is not final or its box
    score has not been ingested. A player with no box-score row in a game
    whose box score HAS been ingested, or a row marked did-not-play or with zero
    minutes, is VOID — the standard bookmaker rule for a prop on a player
    who does not take the court — not a loss.
    """
    if prediction.settled_at is not None:
        return SETTLE_ALREADY_SETTLED
    game = prediction.game
    now = _now(now)

    def _write(outcome: str, actual: float | None, note: str | None) -> str:
        prediction.actual_value = actual
        prediction.outcome = outcome
        prediction.settlement_note = note
        prediction.settled_at = now
        db.flush()
        return SETTLE_SETTLED

    if game.status == NbaGameStatus.CANCELLED.value:
        return _write(RESULT_VOID, None, "game cancelled")
    if game.status != NbaGameStatus.FINAL.value:
        return SETTLE_AWAITING_RESULT

    log = db.scalar(select(NbaPlayerGameLog).where(NbaPlayerGameLog.game_id == game.id, NbaPlayerGameLog.player_id == prediction.player_id))
    if log is None:
        box_score_ingested = db.scalar(select(NbaPlayerGameLog.id).where(NbaPlayerGameLog.game_id == game.id).limit(1)) is not None
        if not box_score_ingested:
            return SETTLE_AWAITING_RESULT
        return _write(RESULT_VOID, None, "player has no box-score entry for this game")
    if log.did_not_play or log.minutes == 0:
        return _write(RESULT_VOID, None, "player did not play")

    actual = actual_stat_value(log, prediction.market)
    if actual is None or actual < 0:
        return _write(RESULT_UNRESOLVED, actual, "stat missing or implausible in the box score")
    return _write(settle_selection(actual, prediction.line, LINE_TYPE_OVER_UNDER, prediction.selection), actual, None)


# --- Batch steps for a future live cycle -----------------------------------


@dataclass
class ClosingCaptureReport:
    considered: int = 0
    captured: int = 0


def capture_due_closing_lines(db: Session, *, now: datetime | None = None, max_quote_age: timedelta | None = None) -> ClosingCaptureReport:
    """Capture the closing line for every prediction whose game has tipped
    off and whose closing line has not been captured yet."""
    now = _now(now)
    report = ClosingCaptureReport()
    due = db.scalars(
        select(NbaPropPrediction)
        .join(NbaGame, NbaGame.id == NbaPropPrediction.game_id)
        .where(NbaPropPrediction.closing_captured_at.is_(None), NbaGame.scheduled_start <= now)
    ).all()
    for prediction in due:
        report.considered += 1
        if capture_closing_line(db, prediction, now=now, max_quote_age=max_quote_age):
            report.captured += 1
    db.commit()
    return report


@dataclass
class SettlementReport:
    considered: int = 0
    settled: int = 0
    awaiting_result: int = 0
    won: int = 0
    lost: int = 0
    pushed: int = 0
    voided: int = 0
    unresolved: int = 0


def settle_due_predictions(db: Session, *, now: datetime | None = None) -> SettlementReport:
    now = _now(now)
    report = SettlementReport()
    counters = {RESULT_WON: "won", RESULT_LOST: "lost", RESULT_PUSH: "pushed", RESULT_VOID: "voided", RESULT_UNRESOLVED: "unresolved"}
    for prediction in db.scalars(select(NbaPropPrediction).where(NbaPropPrediction.settled_at.is_(None))).all():
        report.considered += 1
        if settle_prediction(db, prediction, now=now) == SETTLE_SETTLED:
            report.settled += 1
            setattr(report, counters[prediction.outcome], getattr(report, counters[prediction.outcome]) + 1)
        else:
            report.awaiting_result += 1
    db.commit()
    return report
