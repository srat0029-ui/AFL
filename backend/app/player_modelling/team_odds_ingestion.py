"""Orchestrates one automated team-market (h2h/spreads/totals) odds
refresh (Section 23 of the Weekly Opportunity Discovery stage): fetch
standard AFL match odds in one call (see
app/providers/afl/the_odds_api.py's get_standard_match_odds) -> resolve
events to Matches -> normalize the provider's market/selection vocabulary
into this project's OddsQuote conventions -> persist idempotent
snapshots tagged source="the_odds_api", so downstream code can always
tell a real automated price apart from a manually-entered one (Section
22: "Do not use stale manual team odds as though they are current
automated prices").
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, OddsQuote
from app.player_modelling.prop_odds_matching import get_or_create_bookmaker, resolve_event_to_match, resolve_team_name
from app.providers.afl.the_odds_api import StandardMatchOddsResult

PROVIDER_NAME = "the_odds_api"

# The provider's own market vocabulary -> this project's OddsQuote.market_type.
MARKET_KEY_MAP: dict[str, str] = {"h2h": "h2h", "spreads": "line", "totals": "total"}


@dataclass
class TeamOddsRefreshReport:
    events_seen: int = 0
    matches_resolved: int = 0
    matches_unresolved: list[str] = field(default_factory=list)
    quotes_seen: int = 0
    quotes_created: int = 0
    quotes_unchanged: int = 0
    unsupported_markets: list[str] = field(default_factory=list)
    unresolved_selections: list[str] = field(default_factory=list)

    @property
    def has_activity(self) -> bool:
        return self.quotes_created > 0 or self.matches_resolved > 0


def _normalize_selection(db: Session, market_key: str, raw_selection: str) -> str | None:
    """Returns the CANONICAL selection text to store, or None if it can't
    be resolved. "totals" outcomes are "Over"/"Under" (no team identity to
    resolve); h2h/spreads outcomes are team names, which must be mapped to
    this project's own Team.name — edges/calculator.py compares
    OddsQuote.selection against Match.home_team.name/away_team.name
    directly, so storing the provider's raw (possibly differently-worded)
    team name would silently break every downstream edge computation for
    that quote."""
    if market_key == "totals":
        return raw_selection.lower()
    team = resolve_team_name(db, raw_selection)
    return team.name if team is not None else None


def ingest_team_odds(db: Session, result: StandardMatchOddsResult) -> TeamOddsRefreshReport:
    report = TeamOddsRefreshReport(events_seen=len(result.events))

    match_by_event: dict[str, Match] = {}
    for event in result.events:
        resolution = resolve_event_to_match(db, event)
        if resolution.match is None:
            report.matches_unresolved.append(f"{event.home_team} v {event.away_team}: {resolution.reason}")
            continue
        match_by_event[event.event_id] = resolution.match
        report.matches_resolved += 1
    db.commit()  # persist external_ids updates written by resolve_event_to_match

    for quote in result.quotes:
        report.quotes_seen += 1
        match = match_by_event.get(quote.event_id)
        if match is None:
            continue

        internal_market_type = MARKET_KEY_MAP.get(quote.market_key)
        if internal_market_type is None:
            if quote.market_key not in report.unsupported_markets:
                report.unsupported_markets.append(quote.market_key)
            continue

        selection = _normalize_selection(db, quote.market_key, quote.selection)
        if selection is None:
            label = f"{quote.selection} ({quote.market_key})"
            if label not in report.unresolved_selections:
                report.unresolved_selections.append(label)
            continue

        bookmaker = get_or_create_bookmaker(db, quote.bookmaker_key, quote.bookmaker_title, quote.bookmaker_region)

        latest = db.scalar(
            select(OddsQuote)
            .where(
                OddsQuote.match_id == match.id,
                OddsQuote.bookmaker_id == bookmaker.id,
                OddsQuote.market_type == internal_market_type,
                OddsQuote.selection == selection,
                OddsQuote.line_value == quote.line_value,
                OddsQuote.source == PROVIDER_NAME,
            )
            .order_by(OddsQuote.recorded_at.desc())
            .limit(1)
        )
        if latest is not None and latest.price_decimal == quote.price_decimal:
            report.quotes_unchanged += 1
            continue

        db.add(
            OddsQuote(
                match_id=match.id, bookmaker_id=bookmaker.id, market_type=internal_market_type,
                selection=selection, line_value=quote.line_value, price_decimal=quote.price_decimal,
                recorded_at=quote.fetched_at, source=PROVIDER_NAME, is_closing_line=False,
            )
        )
        report.quotes_created += 1

    db.commit()
    return report
