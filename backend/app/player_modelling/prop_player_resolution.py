"""Resolves a bookmaker-provider's player name text to an internal Player
row (Section 5 of the automated-odds stage brief) — "This is critical."

Deliberately layered, most-confident-first, and classified rather than a
single boolean match/no-match: a caller (ingestion, the audit report) needs
to know not just WHETHER a name resolved but HOW confidently, because
Section 5 is explicit that ambiguous/unresolved props must never enter
consumer Prop Insights even though they're still worth reporting for
review. Reuses app/player_modelling/team_selection_ingestion.py's
resolve_player_identity (exact, case-insensitive display_name match,
current roster first then historical) rather than reinventing name
matching — that function already embodies this project's "never invent a
player" rule; this module adds provider-name normalization on top of it
and the match-scoping (a prop's player must belong to one of the two teams
actually playing that match — never searched league-wide).
"""

import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Match, Player
from app.player_modelling.team_selection_ingestion import resolve_player_identity

RESOLUTION_EXACT = "exact"
RESOLUTION_NORMALIZED_EXACT = "normalized_exact"
RESOLUTION_SAFELY_RESOLVED = "safely_resolved"
RESOLUTION_AMBIGUOUS = "ambiguous"
RESOLUTION_UNRESOLVED = "unresolved"

# A resolution at this tier or better is trusted to appear in consumer Prop
# Insights (Section 5: "Ambiguous/unresolved props should not enter
# consumer Prop Insights. Report them for review.").
TRUSTED_TIERS = frozenset({RESOLUTION_EXACT, RESOLUTION_NORMALIZED_EXACT, RESOLUTION_SAFELY_RESOLVED})


@dataclass(frozen=True)
class PropPlayerResolution:
    player: Player | None
    tier: str
    team_id: int | None = None  # which of the match's two teams the player was matched on


def _normalize_name(raw: str) -> str:
    """Strips punctuation this project has actually observed differ between
    sources (periods, apostrophes, hyphens-as-spaces) and collapses
    whitespace — a "safe formatting difference" per Section 5, not a fuzzy
    match: this still requires every letter to line up once punctuation is
    stripped, nothing is guessed."""
    text = raw.strip().lower()
    text = re.sub(r"[.’']", "", text)
    text = re.sub(r"[-\s]+", " ", text)
    return text.strip()


def _find_by_normalized_name(db: Session, team_id: int, normalized: str) -> list[Player]:
    current = db.scalars(select(Player).where(Player.current_team_id == team_id)).all()
    return [p for p in current if _normalize_name(p.display_name) == normalized]


def _find_by_initial_and_surname(db: Session, team_id: int, raw_name: str) -> list[Player]:
    """Handles "N. Daicos" / "N Daicos" style abbreviation — only ever
    returns a match when exactly one current-roster player on this team
    has that surname AND that first initial; two players sharing a surname
    (or the initial not matching) correctly yields zero/ambiguous rather
    than a guess."""
    m = re.match(r"^([A-Za-z])\.?\s+([A-Za-z][A-Za-z'\-]*)$", raw_name.strip())
    if not m:
        return []
    initial, surname = m.group(1).lower(), _normalize_name(m.group(2))
    current = db.scalars(select(Player).where(Player.current_team_id == team_id)).all()
    matches = []
    for p in current:
        parts = _normalize_name(p.display_name).split(" ")
        if len(parts) < 2:
            continue
        if parts[-1] == surname and parts[0][:1] == initial:
            matches.append(p)
    return matches


def _exact_finder(db: Session, team_id: int, raw_name: str) -> list[Player]:
    resolution = resolve_player_identity(db, team_id, raw_name)
    if resolution.is_ambiguous:
        # resolve_player_identity collapses "found >1" into is_ambiguous
        # rather than returning the list - callers here only need to
        # distinguish 0 / 1 / many, so two placeholder ids (never
        # dereferenced past their count) carry that signal without a
        # second return shape just for this one case.
        return [-1, -2]  # type: ignore[list-item]
    return [resolution.player] if resolution.player is not None else []


def resolve_prop_player(db: Session, match: Match, raw_name: str) -> PropPlayerResolution:
    """Tries, in order, against BOTH of the match's teams (never league-wide
    — see module docstring): exact match, normalized-punctuation match,
    initial+surname match. A name matching unambiguously on exactly one of
    the two teams at any tier resolves at that tier; matching on both teams,
    or ambiguously within one team, is always RESOLUTION_AMBIGUOUS — never
    guessed even if one side "seems more likely"."""
    team_ids = [match.home_team_id, match.away_team_id]

    for tier, finder in (
        (RESOLUTION_EXACT, lambda team_id: _exact_finder(db, team_id, raw_name)),
        (RESOLUTION_NORMALIZED_EXACT, lambda team_id: _find_by_normalized_name(db, team_id, _normalize_name(raw_name))),
        (RESOLUTION_SAFELY_RESOLVED, lambda team_id: _find_by_initial_and_surname(db, team_id, raw_name)),
    ):
        found_on: list[tuple[int, Player]] = []
        ambiguous_on_a_team = False
        for team_id in team_ids:
            matches = finder(team_id)
            if len(matches) > 1:
                ambiguous_on_a_team = True
            elif len(matches) == 1:
                found_on.append((team_id, matches[0]))

        if ambiguous_on_a_team or len(found_on) > 1:
            return PropPlayerResolution(player=None, tier=RESOLUTION_AMBIGUOUS)
        if len(found_on) == 1:
            team_id, player = found_on[0]
            return PropPlayerResolution(player=player, tier=tier, team_id=team_id)

    return PropPlayerResolution(player=None, tier=RESOLUTION_UNRESOLVED)
