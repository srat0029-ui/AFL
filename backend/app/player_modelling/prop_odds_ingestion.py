"""Orchestrates one automated player-prop odds refresh: list upcoming AFL
matches -> list provider events (free) -> resolve events to matches ->
fetch quotes for matches due a refresh (costs quota, gated by
prop_odds_quota) -> normalize markets -> resolve players -> persist
idempotent snapshots. Sections 6-9 of the automated-odds stage brief.

Deliberately provider-agnostic past the PlayerPropOddsProvider boundary —
this module never imports TheOddsApiProvider directly (see
run_prop_odds_refresh's `provider` parameter), so a second provider is a
new class satisfying that interface, not a change here.

Performance note: a real production Live Cycle run was killed by its
30-minute workflow timeout inside this function. Profiling (see
app/player_modelling/prop_player_resolution.py's docstring) traced it to
per-quote database round trips: player resolution (~10 queries/quote) plus
a duplicate-existence SELECT and a bookmaker lookup, both also issued once
per quote. A benchmark of ~2,800 realistic quotes for one match measured
~27,700 SQL queries against a real database before this fix.

The redesign, per match: (1) build ONE MatchResolutionContext up front
(fixed query count, see prop_player_resolution.py); (2) load every
existing PlayerPropMarket row for this match+source ONCE and reduce it to
"latest row per identity key" in memory; (3) resolve, classify, and
identity-check every quote purely in memory against that context and
map - zero queries per quote; (4) bulk-insert every genuinely new row in
one statement, then one commit per match (unchanged from before - commits
were already per-match, never per-quote). A within-batch duplicate quote
(the same identity key appearing twice in one provider response) is
caught the same way a same-batch duplicate always was: the in-memory
"latest key" map is updated the moment a new row is prepared, so a later
duplicate in the same loop sees it immediately, before ever reaching the
database.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Bookmaker, Match, PlayerPropMarket
from app.player_modelling.prop_market_mapping import NormalizedProp, UnsupportedMarket, normalize_prop_quote
from app.player_modelling.prop_odds_matching import get_or_create_bookmaker, resolve_event_to_match
from app.player_modelling.prop_odds_quota import event_needs_refresh, recommended_refresh_interval
from app.player_modelling.prop_player_resolution import (
    TRUSTED_TIERS,
    MatchResolutionContext,
    build_match_resolution_context,
    resolve_prop_player_with_context,
)
from app.player_modelling.upcoming_features import UpcomingMatchTeams
from app.providers.player_prop_odds import PlayerPropOddsProvider, QuotaStatus
from app.providers.types import PlayerPropQuote

logger = logging.getLogger(__name__)


@dataclass
class PropOddsRefreshReport:
    provider_available: bool = True
    events_seen: int = 0
    matches_resolved: int = 0
    matches_skipped_fresh: int = 0
    matches_unresolved: list[str] = field(default_factory=list)
    quotes_seen: int = 0
    quotes_created: int = 0
    quotes_unchanged: int = 0
    unsupported_markets: list[str] = field(default_factory=list)
    unresolved_players: list[str] = field(default_factory=list)
    ambiguous_players: list[str] = field(default_factory=list)
    last_quota: QuotaStatus | None = None

    @property
    def has_activity(self) -> bool:
        return self.quotes_created > 0 or self.matches_resolved > 0


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _same_instant(a: datetime, b: datetime) -> bool:
    """SQLite drops tzinfo on round-trip (a real, previously-hit issue in
    this codebase - see app/ingestion/player_stats.py's provenance tests):
    a datetime read back from the DB after commit/expire can come back
    naive even though it was written tz-aware, and comparing an aware and
    a naive datetime with `==` silently returns False rather than raising
    - which would make this idempotency check never actually match,
    creating a duplicate snapshot on every single refresh. Normalise both
    sides to UTC-aware before comparing."""
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return a == b


def _identity_key(match_id: int, player_id: int, bookmaker_id: int, normalized: NormalizedProp, source: str) -> tuple:
    return (
        match_id, player_id, bookmaker_id, normalized.market.value, normalized.line_type.value,
        normalized.threshold, normalized.selection, source,
    )


def _load_latest_existing_by_key(db: Session, match_id: int, source: str) -> dict[tuple, dict]:
    """ONE query per match+source (not per quote), reduced to "latest row
    per identity key" in memory - replaces what was previously a separate
    SELECT per quote. Values are plain dicts (not ORM rows) so a
    not-yet-committed prepared row can be inserted into this same map with
    the identical shape - see run_prop_odds_refresh's within-batch
    duplicate handling."""
    rows = db.scalars(
        select(PlayerPropMarket).where(PlayerPropMarket.match_id == match_id, PlayerPropMarket.source == source)
    ).all()
    latest: dict[tuple, dict] = {}
    for row in rows:
        key = (row.match_id, row.player_id, row.bookmaker_id, row.market_type, row.line_type, row.threshold, row.selection, row.source)
        current = latest.get(key)
        if current is None or row.recorded_at > current["recorded_at"]:
            latest[key] = {
                "recorded_at": row.recorded_at,
                "bookmaker_last_update": row.bookmaker_last_update,
                "price_decimal": row.price_decimal,
            }
    return latest


def _prepare_quote(
    context: MatchResolutionContext,
    match: Match,
    quote: PlayerPropQuote,
    report: PropOddsRefreshReport,
    existing_by_key: dict[tuple, dict],
    bookmaker_cache: dict[str, Bookmaker],
    db: Session,
) -> dict | None:
    """Classifies and identity-checks one quote entirely in memory (no
    queries except the one-off get_or_create_bookmaker on a cache miss,
    which happens once per distinct bookmaker per run, not per quote).
    Returns an insert-ready dict for a genuinely new row, or None (already
    reported as unsupported/unresolved/ambiguous/unchanged). On returning
    a new row, updates existing_by_key in place so a later duplicate quote
    in the SAME batch is recognized without ever reaching the database."""
    normalized = normalize_prop_quote(quote)
    if isinstance(normalized, UnsupportedMarket):
        report.unsupported_markets.append(f"{quote.market_key}: {normalized.reason}")
        return None

    resolution = resolve_prop_player_with_context(context, quote.player_name, source=quote.provider)
    if resolution.tier == "ambiguous":
        report.ambiguous_players.append(f"{quote.player_name} ({match.home_team.name} v {match.away_team.name})")
        return None
    if resolution.tier == "unresolved" or resolution.player is None:
        report.unresolved_players.append(f"{quote.player_name} ({match.home_team.name} v {match.away_team.name})")
        return None
    if resolution.tier not in TRUSTED_TIERS:
        report.unresolved_players.append(f"{quote.player_name}: untrusted resolution tier {resolution.tier!r}")
        return None

    bookmaker = bookmaker_cache.get(quote.bookmaker_title)
    if bookmaker is None:
        bookmaker = get_or_create_bookmaker(db, quote.bookmaker_key, quote.bookmaker_title, quote.bookmaker_region)
        bookmaker_cache[quote.bookmaker_title] = bookmaker

    key = _identity_key(match.id, resolution.player.id, bookmaker.id, normalized, quote.provider)
    existing = existing_by_key.get(key)
    if (
        existing is not None
        and existing["bookmaker_last_update"] is not None
        and _same_instant(existing["bookmaker_last_update"], quote.bookmaker_last_update)
        and existing["price_decimal"] == quote.price_decimal
    ):
        report.quotes_unchanged += 1
        return None

    row = {
        "match_id": match.id,
        "player_id": resolution.player.id,
        "bookmaker_id": bookmaker.id,
        "market_type": normalized.market.value,
        "line_type": normalized.line_type.value,
        "threshold": normalized.threshold,
        "selection": normalized.selection,
        "price_decimal": quote.price_decimal,
        "recorded_at": quote.fetched_at,
        "source": quote.provider,
        "provider_event_id": quote.event_id,
        "provider_market_key": quote.market_key,
        "bookmaker_last_update": quote.bookmaker_last_update,
        "raw_outcome": quote.raw_outcome,
    }
    # Visible to any later duplicate of this exact key within the same
    # batch immediately - mirrors the old per-quote code's behavior, where
    # SQLAlchemy's autoflush made an already-added-but-uncommitted row
    # visible to the next SELECT in the same transaction.
    existing_by_key[key] = {
        "recorded_at": row["recorded_at"],
        "bookmaker_last_update": row["bookmaker_last_update"],
        "price_decimal": row["price_decimal"],
    }
    report.quotes_created += 1
    return row


def run_prop_odds_refresh(
    db: Session,
    provider: PlayerPropOddsProvider,
    upcoming_matches: list[UpcomingMatchTeams],
    market_keys: list[str],
    min_refresh_interval: timedelta | None = None,
    force: bool = False,
) -> PropOddsRefreshReport:
    """`min_refresh_interval=None` (the default) uses the match-time-aware
    policy (Section 4 of the live-operations stage brief — see
    prop_odds_quota.recommended_refresh_interval): a match 6 days out is
    refreshed far less often than one kicking off soon. Passing an explicit
    timedelta overrides that with one flat interval for every match in this
    run (e.g. the CLI's --min-interval-minutes flag) - useful for testing
    or a deliberate one-off override, but loses the time-awareness."""
    report = PropOddsRefreshReport()
    if not provider.is_available:
        report.provider_available = False
        return report

    upcoming_match_ids = {m.match_id for m in upcoming_matches}
    _t0 = time.monotonic()
    events = provider.list_events("AFL")
    report.events_seen = len(events)
    logger.info("prop_odds_refresh phase=list_events duration_s=%.2f events=%d", time.monotonic() - _t0, len(events))

    # Global for the whole run (bookmakers aren't match-scoped) - a handful
    # of distinct bookmakers ever appear, so this turns what used to be one
    # query per quote into at most one query per distinct bookmaker name.
    bookmaker_cache: dict[str, Bookmaker] = {}

    for event in events:
        resolution = resolve_event_to_match(db, event)
        if resolution.match is None:
            report.matches_unresolved.append(resolution.reason or f"event {event.event_id} unresolved")
            continue
        match = resolution.match
        if match.id not in upcoming_match_ids:
            # Not one of the matches we're refreshing this run (e.g. a
            # future round beyond the current upcoming-round scope) - skip
            # quietly rather than spend quota on it.
            continue

        db.commit()  # persist the Match.external_ids cache write from resolve_event_to_match even if we skip below

        if min_refresh_interval is not None:
            effective_interval = min_refresh_interval
        else:
            hours_to_kickoff = (_aware(match.scheduled_start) - datetime.now(timezone.utc)).total_seconds() / 3600.0
            effective_interval = recommended_refresh_interval(hours_to_kickoff)

        if not force and not event_needs_refresh(db, match.id, event.provider, effective_interval):
            report.matches_skipped_fresh += 1
            continue

        _t_fetch = time.monotonic()
        result = provider.get_player_prop_quotes("AFL", event, market_keys)
        fetch_duration = time.monotonic() - _t_fetch
        report.last_quota = result.quota
        report.matches_resolved += 1

        _t_context = time.monotonic()
        context = build_match_resolution_context(db, match)
        context_duration = time.monotonic() - _t_context

        source = result.quotes[0].provider if result.quotes else event.provider
        _t_existing = time.monotonic()
        existing_by_key = _load_latest_existing_by_key(db, match.id, source)
        existing_duration = time.monotonic() - _t_existing

        _t_resolve = time.monotonic()
        new_rows: list[dict] = []
        for quote in result.quotes:
            report.quotes_seen += 1
            row = _prepare_quote(context, match, quote, report, existing_by_key, bookmaker_cache, db)
            if row is not None:
                new_rows.append(row)
        resolve_duration = time.monotonic() - _t_resolve

        _t_insert = time.monotonic()
        if new_rows:
            db.execute(PlayerPropMarket.__table__.insert(), new_rows)
        db.commit()
        insert_duration = time.monotonic() - _t_insert

        logger.info(
            "prop_odds_refresh match_id=%s quotes=%d created=%d unchanged=%d "
            "phase_fetch_s=%.2f phase_context_preload_s=%.2f phase_existing_lookup_s=%.2f "
            "phase_resolve_s=%.2f phase_insert_s=%.2f",
            match.id, len(result.quotes), len(new_rows), len(result.quotes) - len(new_rows),
            fetch_duration, context_duration, existing_duration, resolve_duration, insert_duration,
        )

    return report
