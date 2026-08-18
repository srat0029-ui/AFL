"""Resolves a player-prop odds provider's event/team/bookmaker identities
against this project's own Match/Team/Bookmaker rows (Sections 6-7 of the
automated-odds stage brief). Never creates a Match — an event that can't be
resolved is reported unresolved, exactly like an unresolved player (Section
5's same philosophy applied to matches).

Team-name aliasing: this project's own Team.name values are Squiggle's
naming (e.g. "Greater Western Sydney", "Gold Coast" — see
app/providers/afl/squiggle.py). A different provider is not guaranteed to
use the same strings (e.g. commercial/nickname forms like "GWS Giants",
"Gold Coast Suns"). _TEAM_NAME_ALIASES below is a best-effort table built
from general knowledge of common AFL naming conventions, NOT verified
against a real The Odds API response (no API key was available during
this stage's build — see the stage report's real-provider-audit section).
An event whose team names don't match exactly OR via this alias table is
reported unresolved rather than guessed via fuzzy string similarity — if
real usage reveals additional real aliases, add them here explicitly.
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Bookmaker, Match, Team
from app.player_modelling.bookmaker_classification import classify_provider_key
from app.providers.types import ProviderEvent

# provider team-name text (lowercased) -> this project's internal Team.name
_TEAM_NAME_ALIASES: dict[str, str] = {
    "adelaide crows": "Adelaide",
    "brisbane lions": "Brisbane Lions",
    "carlton blues": "Carlton",
    "collingwood magpies": "Collingwood",
    "essendon bombers": "Essendon",
    "fremantle dockers": "Fremantle",
    "geelong cats": "Geelong",
    "gold coast suns": "Gold Coast",
    "gws giants": "Greater Western Sydney",
    "greater western sydney giants": "Greater Western Sydney",
    "hawthorn hawks": "Hawthorn",
    "melbourne demons": "Melbourne",
    "north melbourne kangaroos": "North Melbourne",
    "kangaroos": "North Melbourne",
    "port adelaide power": "Port Adelaide",
    "richmond tigers": "Richmond",
    "st kilda saints": "St Kilda",
    "sydney swans": "Sydney",
    "west coast eagles": "West Coast",
    "western bulldogs": "Western Bulldogs",
}

# A Match is only ever considered a candidate for a provider event within
# this window either side of the event's commence_time - guards against a
# team-name collision (real or aliased) resolving to the wrong ROUND's
# fixture between the same two teams (they play each other more than once
# a season). Generous enough to absorb a rescheduled match, tight enough
# that two different rounds' fixtures for the same pair can't both qualify.
_MATCH_TIME_TOLERANCE = timedelta(hours=36)


def resolve_team_name(db: Session, provider_name: str) -> Team | None:
    exact = db.scalar(select(Team).where(Team.name == provider_name))
    if exact is not None:
        return exact
    aliased_name = _TEAM_NAME_ALIASES.get(provider_name.strip().lower())
    if aliased_name is None:
        return None
    return db.scalar(select(Team).where(Team.name == aliased_name))


@dataclass(frozen=True)
class MatchResolution:
    match: Match | None
    reason: str | None = None  # set when match is None, explaining why


def resolve_event_to_match(db: Session, event: ProviderEvent) -> MatchResolution:
    """Resolves by (home_team, away_team, commence_time within tolerance) —
    the same shape used for AFL Tables/Squiggle "no shared id scheme"
    resolution elsewhere in this codebase, not a new pattern. Deterministic
    and idempotent (team names/kickoff times don't change run to run), so
    there's no need for a separate "resolve by cached provider event id"
    fast path — this just re-derives the same answer and re-writes the
    same Match.external_ids entry every time, which is cheap (a handful of
    matches per round) and simpler than maintaining a second lookup path."""
    home = resolve_team_name(db, event.home_team)
    away = resolve_team_name(db, event.away_team)
    if home is None or away is None:
        unresolved = [name for name, team in ((event.home_team, home), (event.away_team, away)) if team is None]
        return MatchResolution(match=None, reason=f"unresolved team name(s): {unresolved}")

    window_start = event.commence_time - _MATCH_TIME_TOLERANCE
    window_end = event.commence_time + _MATCH_TIME_TOLERANCE
    candidates = db.scalars(
        select(Match).where(
            Match.home_team_id == home.id,
            Match.away_team_id == away.id,
            Match.scheduled_start >= window_start,
            Match.scheduled_start <= window_end,
        )
    ).all()
    if len(candidates) == 0:
        return MatchResolution(
            match=None,
            reason=f"no Match found for {home.name} v {away.name} near {event.commence_time.isoformat()}",
        )
    if len(candidates) > 1:
        return MatchResolution(
            match=None,
            reason=f"ambiguous: {len(candidates)} Match candidates for {home.name} v {away.name} near {event.commence_time.isoformat()}",
        )

    match = candidates[0]
    match.external_ids = {**(match.external_ids or {}), event.provider: event.event_id}
    return MatchResolution(match=match)


def get_or_create_bookmaker(db: Session, key: str, title: str, region: str) -> Bookmaker:
    """Get-or-create by display name (title) - matches the existing manual
    -entry convention (see app/api/routes/player_projections.py's
    _get_or_create_bookmaker) so a bookmaker seen both manually and via a
    provider is the SAME row, not a duplicate. provider_key/region are
    filled in the first time a provider supplies them; an existing manual
    -only row is enriched in place rather than left blank forever.

    is_exchange/eligibility (Market Integrity stage, Section 4) are
    classified from the provider key the FIRST time it's known, exactly
    once - a later manual eligibility edit (PATCH /api/bookmakers/{id})
    must never be silently overwritten by a subsequent refresh."""
    bookmaker = db.scalar(select(Bookmaker).where(Bookmaker.name == title))
    if bookmaker is None:
        is_exchange, eligibility = classify_provider_key(key)
        bookmaker = Bookmaker(name=title, provider_key=key, region=region, is_exchange=is_exchange, eligibility=eligibility)
        db.add(bookmaker)
        db.flush()
        return bookmaker
    if bookmaker.provider_key is None:
        is_exchange, eligibility = classify_provider_key(key)
        bookmaker.provider_key = key
        bookmaker.is_exchange = is_exchange
        bookmaker.eligibility = eligibility
    if bookmaker.region is None:
        bookmaker.region = region
    return bookmaker
