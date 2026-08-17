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

from app.models import Match, Player, PlayerAlias
from app.player_modelling.team_selection_ingestion import resolve_player_identity

RESOLUTION_ALIAS = "alias"
RESOLUTION_EXACT = "exact"
RESOLUTION_NORMALIZED_EXACT = "normalized_exact"
RESOLUTION_SAFELY_RESOLVED = "safely_resolved"
RESOLUTION_AMBIGUOUS = "ambiguous"
RESOLUTION_UNRESOLVED = "unresolved"

# A resolution at this tier or better is trusted to appear in consumer Prop
# Insights (Section 5: "Ambiguous/unresolved props should not enter
# consumer Prop Insights. Report them for review.").
TRUSTED_TIERS = frozenset({RESOLUTION_ALIAS, RESOLUTION_EXACT, RESOLUTION_NORMALIZED_EXACT, RESOLUTION_SAFELY_RESOLVED})


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


def _find_by_alias(db: Session, team_id: int, raw_name: str, source: str | None) -> list[Player]:
    """Section 9 of the live-operations stage brief: an explicit, human-
    curated PlayerAlias row is a reviewed claim ("this exact provider
    string IS this exact player"), so it's tried FIRST, ahead of even an
    exact display_name match — a live provider string that happens to
    collide with a different player's name would be wrong to prefer over a
    human's explicit correction. Source-scoped aliases (source matches
    exactly) are tried before source-agnostic ones (source IS NULL);
    matching is otherwise still team-scoped, same as every other tier
    here — a stale alias pointing at a player no longer on either of this
    match's two teams simply doesn't match and falls through to the next
    tier, rather than forcing a false positive."""
    name_norm = raw_name.strip().lower()
    aliases = db.scalars(select(PlayerAlias).where(func.lower(PlayerAlias.alias_name) == name_norm)).all()
    if not aliases:
        return []
    scoped = [a for a in aliases if source is not None and a.source == source]
    candidates = scoped if scoped else [a for a in aliases if a.source is None]
    players = {a.player.id: a.player for a in candidates if a.player.current_team_id == team_id}
    return list(players.values())


def _find_by_normalized_name(db: Session, team_id: int, normalized: str) -> list[Player]:
    current = db.scalars(select(Player).where(Player.current_team_id == team_id)).all()
    return [p for p in current if _normalize_name(p.display_name) == normalized]


# Common, unambiguous given-name <-> nickname pairs — a real, evidenced
# pattern (not speculative): a live provider fetch returned "Cameron
# Rayner" and "Lachlan Schultz" where this project's own records (sourced
# from AFL Tables, which publishes players' commonly-used names) have "Cam
# Rayner" and "Lachie Schultz". Deliberately a small, explicit, hand-vetted
# table — not an algorithmic name-shortening rule — because an algorithm
# guessing at nicknames ("Cameron" -> "Cam" by prefix-truncation) would
# also produce wrong guesses for names it doesn't actually apply to. Each
# entry here is a specific, known-real English/Australian nickname
# convention. Expand only from further real evidence, not speculatively.
_GIVEN_NAME_NICKNAMES: dict[str, str] = {
    "cameron": "cam", "lachlan": "lachie", "nathaniel": "nathan", "matthew": "matt",
    "michael": "mike", "christopher": "chris", "william": "will", "alexander": "alex",
    "benjamin": "ben", "samuel": "sam", "joshua": "josh", "nicholas": "nick",
    "thomas": "tom", "daniel": "dan", "jacob": "jake", "zachary": "zac",
    "jonathan": "jon", "timothy": "tim", "anthony": "tony", "charles": "charlie",
}


def _find_by_given_name_nickname(db: Session, team_id: int, raw_name: str) -> list[Player]:
    """Handles "Cameron Rayner" (provider's full given name) matching our
    "Cam Rayner" (the nickname AFL Tables publishes), or the reverse - only
    ever matches when the surname is identical and the given name maps
    through _GIVEN_NAME_NICKNAMES in either direction; never a fuzzy or
    partial match."""
    parts = _normalize_name(raw_name).split(" ")
    if len(parts) < 2:
        return []
    given, surname = parts[0], parts[-1]
    candidates = {given, _GIVEN_NAME_NICKNAMES.get(given, given)}
    for full, nick in _GIVEN_NAME_NICKNAMES.items():
        if nick == given:
            candidates.add(full)
    current = db.scalars(select(Player).where(Player.current_team_id == team_id)).all()
    matches = []
    for p in current:
        p_parts = _normalize_name(p.display_name).split(" ")
        if len(p_parts) < 2 or p_parts[-1] != surname:
            continue
        if p_parts[0] in candidates:
            matches.append(p)
    return matches


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


def resolve_prop_player(db: Session, match: Match, raw_name: str, source: str | None = None) -> PropPlayerResolution:
    """Tries, in order, against BOTH of the match's teams (never league-wide
    — see module docstring): exact match, normalized-punctuation match,
    initial+surname match. A name matching unambiguously on exactly one of
    the two teams at any tier resolves at that tier; matching on both teams,
    or ambiguously within one team, is always RESOLUTION_AMBIGUOUS — never
    guessed even if one side "seems more likely" - UNLESS both sides
    resolved to the literal SAME Player row (a real, observed case: a
    player traded between the two teams now facing each other has genuine
    historical PlayerMatchStat rows for both, so the "current roster first,
    historical fallback second" lookup in resolve_player_identity finds
    them via their old team's history AND their new team's current roster
    — that's one unambiguous person, not two candidates, and should
    resolve using their CURRENT team, not be reported as ambiguous."""
    team_ids = [match.home_team_id, match.away_team_id]

    for tier, finder in (
        (RESOLUTION_ALIAS, lambda team_id: _find_by_alias(db, team_id, raw_name, source)),
        (RESOLUTION_EXACT, lambda team_id: _exact_finder(db, team_id, raw_name)),
        (RESOLUTION_NORMALIZED_EXACT, lambda team_id: _find_by_normalized_name(db, team_id, _normalize_name(raw_name))),
        (RESOLUTION_SAFELY_RESOLVED, lambda team_id: _find_by_given_name_nickname(db, team_id, raw_name)),
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

        distinct_players = {player.id: player for _team_id, player in found_on}
        if ambiguous_on_a_team or len(distinct_players) > 1:
            return PropPlayerResolution(player=None, tier=RESOLUTION_AMBIGUOUS)
        if len(distinct_players) == 1:
            player = next(iter(distinct_players.values()))
            # Prefer the player's actual current team over whichever side
            # of the loop happened to find them first.
            resolved_team_id = player.current_team_id if player.current_team_id in team_ids else found_on[0][0]
            return PropPlayerResolution(player=player, tier=tier, team_id=resolved_team_id)

    return PropPlayerResolution(player=None, tier=RESOLUTION_UNRESOLVED)
