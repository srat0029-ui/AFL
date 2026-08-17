"""Orchestrates one automated player-prop odds refresh: list upcoming AFL
matches -> list provider events (free) -> resolve events to matches ->
fetch quotes for matches due a refresh (costs quota, gated by
prop_odds_quota) -> normalize markets -> resolve players -> persist
idempotent snapshots. Sections 6-9 of the automated-odds stage brief.

Deliberately provider-agnostic past the PlayerPropOddsProvider boundary —
this module never imports TheOddsApiProvider directly (see
run_prop_odds_refresh's `provider` parameter), so a second provider is a
new class satisfying that interface, not a change here.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, PlayerPropMarket
from app.player_modelling.prop_market_mapping import NormalizedProp, UnsupportedMarket, normalize_prop_quote
from app.player_modelling.prop_odds_matching import get_or_create_bookmaker, resolve_event_to_match
from app.player_modelling.prop_odds_quota import event_needs_refresh, recommended_refresh_interval
from app.player_modelling.prop_player_resolution import TRUSTED_TIERS, resolve_prop_player
from app.player_modelling.upcoming_features import UpcomingMatchTeams
from app.providers.player_prop_odds import PlayerPropOddsProvider, QuotaStatus
from app.providers.types import PlayerPropQuote


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


def _persist_normalized_quote(db: Session, match: Match, normalized: NormalizedProp, player_id: int, bookmaker_id: int) -> bool:
    """Returns True if a new row was created, False if an identical
    snapshot already existed (idempotent re-fetch — Section 9)."""
    q = normalized.quote
    latest = db.scalar(
        select(PlayerPropMarket)
        .where(
            PlayerPropMarket.match_id == match.id,
            PlayerPropMarket.player_id == player_id,
            PlayerPropMarket.bookmaker_id == bookmaker_id,
            PlayerPropMarket.market_type == normalized.market.value,
            PlayerPropMarket.line_type == normalized.line_type.value,
            PlayerPropMarket.threshold == normalized.threshold,
            PlayerPropMarket.selection == normalized.selection,
            PlayerPropMarket.source == q.provider,
        )
        .order_by(PlayerPropMarket.recorded_at.desc())
        .limit(1)
    )
    if (
        latest is not None
        and latest.bookmaker_last_update is not None
        and _same_instant(latest.bookmaker_last_update, q.bookmaker_last_update)
        and latest.price_decimal == q.price_decimal
    ):
        return False

    db.add(
        PlayerPropMarket(
            match_id=match.id,
            player_id=player_id,
            bookmaker_id=bookmaker_id,
            market_type=normalized.market.value,
            line_type=normalized.line_type.value,
            threshold=normalized.threshold,
            selection=normalized.selection,
            price_decimal=q.price_decimal,
            recorded_at=q.fetched_at,
            source=q.provider,
            provider_event_id=q.event_id,
            provider_market_key=q.market_key,
            bookmaker_last_update=q.bookmaker_last_update,
            raw_outcome=q.raw_outcome,
        )
    )
    return True


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
    events = provider.list_events("AFL")
    report.events_seen = len(events)

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

        result = provider.get_player_prop_quotes("AFL", event, market_keys)
        report.last_quota = result.quota
        report.matches_resolved += 1

        for quote in result.quotes:
            report.quotes_seen += 1
            _ingest_one_quote(db, match, quote, report)

        db.commit()

    return report


def _ingest_one_quote(db: Session, match: Match, quote: PlayerPropQuote, report: PropOddsRefreshReport) -> None:
    normalized = normalize_prop_quote(quote)
    if isinstance(normalized, UnsupportedMarket):
        report.unsupported_markets.append(f"{quote.market_key}: {normalized.reason}")
        return

    resolution = resolve_prop_player(db, match, quote.player_name, source=quote.provider)
    if resolution.tier == "ambiguous":
        report.ambiguous_players.append(f"{quote.player_name} ({match.home_team.name} v {match.away_team.name})")
        return
    if resolution.tier == "unresolved" or resolution.player is None:
        report.unresolved_players.append(f"{quote.player_name} ({match.home_team.name} v {match.away_team.name})")
        return
    if resolution.tier not in TRUSTED_TIERS:
        report.unresolved_players.append(f"{quote.player_name}: untrusted resolution tier {resolution.tier!r}")
        return

    bookmaker = get_or_create_bookmaker(db, quote.bookmaker_key, quote.bookmaker_title, quote.bookmaker_region)
    created = _persist_normalized_quote(db, match, normalized, resolution.player.id, bookmaker.id)
    if created:
        report.quotes_created += 1
    else:
        report.quotes_unchanged += 1
