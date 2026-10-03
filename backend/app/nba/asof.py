"""THE information boundary for NBA.

Every read of history that feeds a prediction goes through this module, and
every function here takes a `cutoff`: it returns only what this system
actually knew at that instant. Feature builders, the freeze step and the
closing-line step must not query the NBA tables directly for this purpose —
keeping the boundary in one module is what makes "no future information
leaked into this prediction" reviewable in one place rather than at every
call site.

Each kind of information has its own "known at" rule:

- Availability reports and prop quotes carry `observed_at` (when we fetched
  them). Known iff observed_at <= cutoff.
- A box score has no trustworthy "learned at" timestamp — a historical
  backfill is recorded years after the game. It is treated as known once
  the game is final AND tip-off was at least GAME_RESULT_AVAILABILITY_LAG
  before the cutoff, so a game still in progress at the cutoff can never
  contribute, in live operation or in a backtest.

The same functions serve live prediction (cutoff = now) and historical
backtesting (cutoff = a past instant), which is the point: a backtest that
uses a different, looser read path than production is not evidence about
production.
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.prospective import ensure_utc
from app.models.nba import COMPETITIVE_SEASON_TYPES, NbaGame, NbaGameStatus, NbaPlayerAvailabilityReport, NbaPlayerGameLog, NbaPropQuote

# An NBA game runs about 2.5 hours; multiple overtimes can push past 3. Four
# hours is deliberately conservative — erring late only costs a feature
# builder one very recent game, erring early leaks an outcome.
GAME_RESULT_AVAILABILITY_LAG = timedelta(hours=4)


def game_logs_known_at(
    db: Session, player_id: int, cutoff: datetime, *, season_types: tuple[str, ...] = COMPETITIVE_SEASON_TYPES
) -> list[NbaPlayerGameLog]:
    """The player's box scores known at `cutoff`, oldest first.

    Only competitive games (regular season, play-in, playoffs) by default:
    a preseason box score is exhibition basketball and is left out of the
    modelling dataset unless a caller deliberately asks for it. A row for a
    game the player did not play in IS returned (did_not_play=True) - "was
    on the roster and sat" is information, and dropping it here would hide
    it from every caller.

    `minutes` is whatever the source published; the current source gives
    whole minutes, so treat it as a number precise to the minute."""
    latest_tipoff = ensure_utc(cutoff) - GAME_RESULT_AVAILABILITY_LAG
    return list(
        db.scalars(
            select(NbaPlayerGameLog)
            .join(NbaGame, NbaGame.id == NbaPlayerGameLog.game_id)
            .where(
                NbaPlayerGameLog.player_id == player_id,
                NbaGame.status == NbaGameStatus.FINAL.value,
                NbaGame.scheduled_start <= latest_tipoff,
                NbaGame.season_type.in_(season_types),
            )
            .order_by(NbaGame.scheduled_start, NbaPlayerGameLog.id)
        ).all()
    )


def availability_known_at(db: Session, player_id: int, cutoff: datetime, *, game_id: int | None = None) -> NbaPlayerAvailabilityReport | None:
    """The most recent availability report for the player observed at or
    before `cutoff` — optionally only reports tied to one game."""
    stmt = select(NbaPlayerAvailabilityReport).where(
        NbaPlayerAvailabilityReport.player_id == player_id, NbaPlayerAvailabilityReport.observed_at <= ensure_utc(cutoff)
    )
    if game_id is not None:
        stmt = stmt.where(NbaPlayerAvailabilityReport.game_id == game_id)
    return db.scalar(stmt.order_by(NbaPlayerAvailabilityReport.observed_at.desc(), NbaPlayerAvailabilityReport.id.desc()).limit(1))


def latest_quotes_known_at(
    db: Session, *, game_id: int, player_id: int, market: str, cutoff: datetime, max_age: timedelta | None = None
) -> list[NbaPropQuote]:
    """The market as it stood at `cutoff`: for each (bookmaker, line,
    selection), the single most recent quote observed at or before the
    cutoff.

    A superseded MAIN line is dropped (see below). Beyond that, because
    ingestion only writes a row when a price changes, "most recent" alone
    cannot tell an unchanged price from an alternate line the bookmaker has
    since withdrawn. `max_age` lets a caller refuse quotes older than a
    window it trusts; None applies no limit.
    """
    cutoff = ensure_utc(cutoff)
    stmt = select(NbaPropQuote).where(
        NbaPropQuote.game_id == game_id,
        NbaPropQuote.player_id == player_id,
        NbaPropQuote.market == market,
        NbaPropQuote.observed_at <= cutoff,
    )
    if max_age is not None:
        stmt = stmt.where(NbaPropQuote.observed_at >= cutoff - max_age)
    latest: dict[tuple[int, float, str], NbaPropQuote] = {}
    current_main: dict[tuple[int, str], NbaPropQuote] = {}
    for quote in db.scalars(stmt.order_by(NbaPropQuote.observed_at, NbaPropQuote.id)).all():
        latest[(quote.bookmaker_id, quote.line, quote.selection)] = quote
        if not quote.is_alternate_line:
            current_main[(quote.bookmaker_id, quote.selection)] = quote
    # A bookmaker has ONE main line per side at a time: once it moves the
    # main line from 24.5 to 25.5, its old 24.5 main-line quote is no longer
    # on offer and must not be read back as a live price.
    return [q for q in latest.values() if q.is_alternate_line or current_main[(q.bookmaker_id, q.selection)] is q]
