"""THE information boundary for NBA.

Every read of history that feeds a prediction goes through this module, and
every function here takes a `cutoff`: it returns only what this system
actually knew at that instant. Feature builders, the freeze step and the
closing-line step must not query the NBA tables directly for this purpose —
keeping the boundary in one module is what makes "no future information
leaked into this prediction" reviewable in one place rather than at every
call site.

Each kind of information has its own "known at" rule:

- Availability reports, team roster and depth-chart observations, game
  lineup and schedule observations, and prop quotes all carry `observed_at`
  (when we fetched them). Known iff observed_at <= cutoff. The source's own
  date on an injury entry is NOT used for this: a status dated 4:30pm that
  we fetched at 6pm was not knowledge we held at 5pm.
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

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.prospective import ensure_utc
from app.models.nba import (
    COMPETITIVE_SEASON_TYPES,
    NbaEvidencePoll,
    NbaGame,
    NbaGameLineupObservation,
    NbaGameScheduleObservation,
    NbaGameStatus,
    NbaPlayerAvailabilityReport,
    NbaPlayerGameLog,
    NbaPropQuote,
    NbaTeamObservation,
)
from app.models.nba.evidence import POLL_AVAILABILITY

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


@dataclass(frozen=True)
class AvailabilityEvidence:
    """What the system knew about one player's availability at a cutoff.

    - `observation` is the latest injury-feed observation for the player
      made at or before the cutoff, or None if the feed had never listed him
      while we were watching. None means "no evidence", NOT "healthy".
    - `observation.is_listed` False means the feed had stopped listing him
      by then; again no status is implied.
    - `first_observed_at` is when that state was first seen.
    - `last_confirmed_at` is the latest successful read of the feed at or
      before the cutoff - how recently the state was known to still hold.
      None means the feed had not been read at all by then.
    """

    cutoff: datetime
    observation: NbaPlayerAvailabilityReport | None
    first_observed_at: datetime | None
    last_confirmed_at: datetime | None

    @property
    def is_listed(self) -> bool:
        return self.observation is not None and self.observation.is_listed


def _last_poll_at(db: Session, kind: str, cutoff: datetime, scope: str | None = None) -> datetime | None:
    stmt = select(func.max(NbaEvidencePoll.observed_at)).where(NbaEvidencePoll.kind == kind, NbaEvidencePoll.observed_at <= cutoff)
    if scope is not None:
        stmt = stmt.where(NbaEvidencePoll.scope == scope)
    latest = db.scalar(stmt)
    return ensure_utc(latest) if latest is not None else None


def availability_known_at(db: Session, player_id: int, cutoff: datetime) -> AvailabilityEvidence:
    """What was known about a player's availability at `cutoff`: the latest
    observation made at or before it. An observation made even one second
    later is invisible, whatever the source's own date on it says."""
    cutoff = ensure_utc(cutoff)
    observation = db.scalar(
        select(NbaPlayerAvailabilityReport)
        .where(NbaPlayerAvailabilityReport.player_id == player_id, NbaPlayerAvailabilityReport.observed_at <= cutoff)
        .order_by(NbaPlayerAvailabilityReport.observed_at.desc(), NbaPlayerAvailabilityReport.id.desc())
        .limit(1)
    )
    return AvailabilityEvidence(
        cutoff=cutoff,
        observation=observation,
        first_observed_at=ensure_utc(observation.observed_at) if observation is not None else None,
        last_confirmed_at=_last_poll_at(db, POLL_AVAILABILITY, cutoff),
    )


def team_availability_known_at(db: Session, team_id: int, cutoff: datetime) -> list[NbaPlayerAvailabilityReport]:
    """Every player the injury feed was listing under this team at `cutoff`
    (each player's latest observation at or before it, kept only if he was
    still listed then and under this team). The basis for teammate-
    availability features. A teammate absent from the result was not listed
    - which is not the same as known to be available."""
    cutoff = ensure_utc(cutoff)
    latest_ids = (
        select(func.max(NbaPlayerAvailabilityReport.id))
        .where(NbaPlayerAvailabilityReport.observed_at <= cutoff)
        .group_by(NbaPlayerAvailabilityReport.player_id)
    )
    return list(
        db.scalars(
            select(NbaPlayerAvailabilityReport)
            .where(NbaPlayerAvailabilityReport.id.in_(latest_ids), NbaPlayerAvailabilityReport.team_id == team_id, NbaPlayerAvailabilityReport.is_listed.is_(True))
            .order_by(NbaPlayerAvailabilityReport.player_id)
        ).all()
    )


def team_observation_known_at(db: Session, team_id: int, kind: str, cutoff: datetime) -> NbaTeamObservation | None:
    """The team's listed roster (kind="roster") or depth chart
    (kind="depth_chart") as it was last observed at or before `cutoff`."""
    return db.scalar(
        select(NbaTeamObservation)
        .where(NbaTeamObservation.team_id == team_id, NbaTeamObservation.kind == kind, NbaTeamObservation.observed_at <= ensure_utc(cutoff))
        .order_by(NbaTeamObservation.observed_at.desc(), NbaTeamObservation.id.desc())
        .limit(1)
    )


def team_observation_confirmed_at(db: Session, obs: NbaTeamObservation, cutoff: datetime) -> datetime:
    """When the source last showed `obs`'s content, at or before `cutoff`.

    A team observation is written only when its content changes, but every
    poll is recorded with its payload hash. A later poll of the same team with
    the same hash re-confirms the observation, so the evidence is as old as
    that poll, not as old as the last change. Falls back to the observation's
    own time when no confirming poll is recorded."""
    first = db.get(NbaEvidencePoll, obs.poll_id)
    confirmed = None
    if first is not None and first.scope is not None:
        confirmed = db.scalar(
            select(func.max(NbaEvidencePoll.observed_at)).where(
                NbaEvidencePoll.kind == first.kind,
                NbaEvidencePoll.scope == first.scope,
                NbaEvidencePoll.source == first.source,
                NbaEvidencePoll.payload_sha256 == obs.content_hash,
                NbaEvidencePoll.observed_at <= ensure_utc(cutoff),
            )
        )
    observed = ensure_utc(obs.observed_at)
    return observed if confirmed is None else max(observed, ensure_utc(confirmed))


def game_lineup_known_at(db: Session, game_id: int, team_id: int, cutoff: datetime) -> NbaGameLineupObservation | None:
    """What the source listed for one team in one game, as last observed at
    or before `cutoff`. Check `has_starter_field` before reading starters:
    well before tip-off the source lists players with no starter
    information at all."""
    return db.scalar(
        select(NbaGameLineupObservation)
        .where(NbaGameLineupObservation.game_id == game_id, NbaGameLineupObservation.team_id == team_id, NbaGameLineupObservation.observed_at <= ensure_utc(cutoff))
        .order_by(NbaGameLineupObservation.observed_at.desc(), NbaGameLineupObservation.id.desc())
        .limit(1)
    )


def game_schedule_known_at(db: Session, game_id: int, cutoff: datetime) -> NbaGameScheduleObservation | None:
    """The game's status and tip-off time as last observed at or before
    `cutoff` - what we believed the schedule to be then, which can differ
    from `nba_games` now if the game was later moved or postponed. None if
    the game had not been observed by then."""
    return db.scalar(
        select(NbaGameScheduleObservation)
        .where(NbaGameScheduleObservation.game_id == game_id, NbaGameScheduleObservation.observed_at <= ensure_utc(cutoff))
        .order_by(NbaGameScheduleObservation.observed_at.desc(), NbaGameScheduleObservation.id.desc())
        .limit(1)
    )


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
