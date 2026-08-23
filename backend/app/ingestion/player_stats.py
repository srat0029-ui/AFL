"""Resolves PlayerStatLine rows (from AFLTablesPlayerStatsProvider) against
already-ingested Player/Match rows and upserts PlayerMatchStat.

Two resolution problems, both handled explicitly rather than guessed:

1. Player identity. AFL Tables' own per-player page URL (e.g.
   "players/J/Jack_Carroll1.html" — note the disambiguating trailing digit
   AFL Tables itself adds for a name clash) IS the identity key
   (source_player_id) — not the display name. Two different real players
   who happen to share a name already get different URLs from the source
   itself, so this project doesn't need (and deliberately doesn't attempt)
   its own fuzzy name-matching; reusing the source's own disambiguation is
   simpler and safer than reinventing it. A player's team can change
   (trades, delistings, re-drafts) — Player.current_team_id is just a
   best-effort "most recently seen" pointer, refreshed as later rows are
   processed; PlayerMatchStat.team_id (per match, never overwritten by a
   later trade) is the actual historical record.

2. Match resolution. No shared id scheme between AFL Tables and Squiggle
   (this project's fixture source) — same situation team_stats.py already
   handles, just keyed by round rather than date, since this source's page
   publishes round labels, not match dates, per cell (see
   afltables_players.py). Two strategies:

   a. Finals: AFL Tables labels finals EF/QF/SF/PF/GF (see
      app/providers/afl/round_labels.py) — Squiggle groups EF and QF under
      one round ("Finals Week 1"), so resolution here is by (season, team,
      Round.name) rather than any numeric round conversion, which the
      brief this implements explicitly calls unsafe. A team appears at
      most once in any finals round, so this is still exact.

   b. Home-and-away: NOT a direct (round_number, team) lookup — a real bug
      found in production via a user report (Harry Sheezel's 54-disposal
      game, North Melbourne v Richmond, shown as v Adelaide a round late)
      proved that's unsafe. Root cause, confirmed against the real live
      pages: AFL Tables' and Squiggle's round-number sequences for a team
      can disagree — e.g. North Melbourne's and Richmond's 2025 AFL Tables
      pages both omit an "R1" column entirely (that team didn't feature in
      whatever AFL Tables considers round 1 that year), so every later
      round number on their page is shifted by one relative to Squiggle's
      (AFL Tables' "R24" cell is actually that team's 23rd game — Squiggle
      calls that same game round 23). A literal (round_number, team)
      lookup can still find exactly one candidate match in this situation
      — coincidentally, not correctly — and silently attach a real
      player's stats to the wrong game. There is no way to tell, from a
      single row in isolation, whether its round number is trustworthy.

      So resolution is done per (team, season) as a batch:
      - Compare the SET of AFL-Tables round numbers used for this team's
        home-and-away rows against the SET of Squiggle round numbers for
        this team's (still-unclaimed) matches. If the two sets are
        IDENTICAL, the two sources agree on this team's numbering for this
        season — safe to trust an exact (round_number, team) lookup per
        row (still refusing to guess if that lookup ever finds zero or
        more than one candidate).
      - If the sets DIFFER, round numbers cannot be trusted at all for
        this team this season. Instead, both sequences are treated purely
        as ORDERED lists of "this team's Nth home-and-away game" — AFL
        Tables' round labels sorted ascending, Squiggle's matches sorted
        by scheduled_start ascending — and paired position-for-position,
        which is correct regardless of what either source calls a given
        round, as long as both cover the same real games. This is only
        done when the two sequences are exactly the same length; if they
        differ, there's a genuine data gap and nothing is resolved — every
        row for that team is reported unmatched rather than guessed. See
        app/ingestion/team_stats.py for the analogous date-based fallback
        used where the source DOES publish dates.

      `PlayerStatsIngestionResult.fallback_resolved` counts rows resolved
      this second way where a shift was actually detected (the paired
      match's real round_number differs from the source's round label) —
      a coverage report can use this to see how often the shift-prone path
      is actually contributing, and for which team-seasons.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, MatchStatus, Player, PlayerMatchStat, Round, Season, Sport, Team
from app.providers.afl.round_labels import ROUND_NAME_BY_FINALS_KIND
from app.providers.types import PlayerStatLine

SOURCE_NAME = "afltables"

# Mirrors PlayerMatchStat's numeric stat columns; order matches
# afltables_players.py's _STAT_TABLE_FIELDS values.
STAT_FIELDS = [
    "kicks", "marks", "handballs", "disposals", "goals", "behinds", "hitouts", "tackles",
    "rebound_50s", "inside_50s", "clearances", "clangers", "frees_for", "frees_against",
    "brownlow_votes", "contested_possessions", "uncontested_possessions", "contested_marks",
    "marks_inside_50", "one_percenters", "bounces", "goal_assists", "time_on_ground_pct",
]


@dataclass
class PlayerStatsIngestionResult:
    rows_seen: int = 0
    players_created: int = 0
    stats_created: int = 0
    stats_updated: int = 0
    stats_unchanged: int = 0
    unmatched: list[str] = field(default_factory=list)
    # Rows resolved only via the fallback (leftover-elimination) strategy —
    # tracked separately from `matched` so a coverage report can show how
    # much the fallback is actually contributing versus the fast primary path.
    fallback_resolved: int = 0

    @property
    def matched(self) -> int:
        return self.stats_created + self.stats_updated + self.stats_unchanged


# Small and deliberately conservative - the real case this exists for
# (Opening Round inserted ahead of the traditional round-1..24 sequence)
# is a +1 shift; a little headroom is kept for an equivalent gap elsewhere
# without inviting a coincidental match at a wide offset. Never applied
# unless it's also the UNIQUE offset that produces a clean trailing-only
# gap (see _find_trailing_round_offset) - going wider here doesn't weaken
# that, it just gives a genuine larger shift more chance to be found.
_ROUND_OFFSET_SEARCH_RANGE = (-2, -1, 1, 2)


def _find_trailing_round_offset(source_round_numbers: list[int], squiggle_round_numbers: set[int]) -> int | None:
    """Looks for a single constant integer offset that reconciles this
    team's source round labels with Squiggle's round numbers when neither
    the exact-match nor the equal-length fallback above applies (see
    module docstring's "Opening Round" example - AFL Tables' round labels
    can be a whole season's worth of a competition-wide constant amount
    higher than Squiggle's for every team, not just the ones who played
    the extra round).

    Real, current-database example this was built from: every 2026 team's
    source round-number set becomes an EXACT subset of its Squiggle round
    numbers under offset -1, with the sole leftover always being that
    team's single most-recently-played round - i.e. AFL Tables simply
    hasn't published it yet, not a genuine mismatch.

    Deliberately conservative in three ways: (1) the offset must be
    unique in _ROUND_OFFSET_SEARCH_RANGE - if more than one offset
    produces a clean result, that's ambiguous and this refuses same as
    the caller's existing fallback; (2) shifted source rounds must be a
    full subset of Squiggle's rounds, never merely overlapping; (3) every
    Squiggle round NOT covered by the shift must be strictly later than
    every one that is - a leftover round earlier than (or interleaved
    with) matched ones means a real gap (e.g. a postponed/rescheduled
    match), not just "not published yet", and this returns None so the
    caller falls through to its normal refusal rather than guess through it.
    """
    if not source_round_numbers or not squiggle_round_numbers:
        return None
    candidates: list[int] = []
    for offset in _ROUND_OFFSET_SEARCH_RANGE:
        shifted = {r + offset for r in source_round_numbers}
        if not shifted.issubset(squiggle_round_numbers):
            continue
        leftover = squiggle_round_numbers - shifted
        if not leftover:
            continue  # exact bijection would already have been caught by the equal-length fallback above
        if min(leftover) <= max(shifted):
            continue  # a leftover round not strictly after every matched round is a real gap, not just unpublished-yet
        candidates.append(offset)
    return candidates[0] if len(candidates) == 1 else None


def _display_name(published_name: str) -> str:
    """"Acres, Blake" (AFL Tables' published "Last, First" form) -> "Blake Acres".
    Falls back to the raw published form unchanged if it isn't in that shape."""
    if "," not in published_name:
        return published_name
    last, _, first = published_name.partition(",")
    first, last = first.strip(), last.strip()
    return f"{first} {last}" if first and last else published_name


def ingest_player_stats(
    db: Session, rows: list[PlayerStatLine], season_year: int, source: str = SOURCE_NAME
) -> PlayerStatsIngestionResult:
    result = PlayerStatsIngestionResult()
    if not rows:
        return result

    sport = db.scalar(select(Sport).where(Sport.code == "AFL"))
    if sport is None:
        result.unmatched.append("no AFL sport row found — has fixture ingestion run?")
        return result

    season = db.scalar(select(Season).where(Season.sport_id == sport.id, Season.year == season_year))
    if season is None:
        result.unmatched.append(f"no season {season_year} found — run fixture ingestion for this year first")
        return result

    teams_by_name = {t.name: t for t in db.scalars(select(Team).where(Team.sport_id == sport.id)).all()}
    rounds_by_number = {
        r.round_number: r for r in db.scalars(select(Round).where(Round.season_id == season.id)).all()
    }
    rounds_by_name: dict[str, Round] = {r.name: r for r in rounds_by_number.values() if r.name}

    # COMPLETED only - a source stat row can only ever describe an
    # already-played game, never a scheduled future one. Matters for an
    # in-progress season backfilled ahead of time (e.g. the full fixture
    # list including not-yet-played rounds): including those would inflate
    # a team's Squiggle-side round count relative to AFL Tables' (which can
    # only ever have played games), tripping the round-number-sets-differ
    # safety check for every team, every time, even when nothing is
    # actually wrong.
    season_matches = db.scalars(
        select(Match).where(Match.season_id == season.id, Match.status == MatchStatus.COMPLETED)
    ).all()
    matches_by_round_team: dict[tuple[int, int], list[Match]] = {}
    matches_by_team: dict[int, list[Match]] = defaultdict(list)
    for m in season_matches:
        matches_by_round_team.setdefault((m.round.round_number, m.home_team_id), []).append(m)
        matches_by_round_team.setdefault((m.round.round_number, m.away_team_id), []).append(m)
        matches_by_team[m.home_team_id].append(m)
        matches_by_team[m.away_team_id].append(m)
    for team_matches in matches_by_team.values():
        team_matches.sort(key=lambda m: (m.scheduled_start, m.id))

    players_by_source_id = {
        p.source_player_id: p
        for p in db.scalars(
            select(Player).where(Player.sport_id == sport.id, Player.source == source)
        ).all()
    }

    # --- split input rows into finals vs home-and-away, grouped for batch resolution ---
    claimed_match_ids: set[int] = set()
    resolved: list[tuple[PlayerStatLine, Match]] = []
    finals_rows: list[PlayerStatLine] = []
    home_and_away_by_team: dict[str, dict[int, list[PlayerStatLine]]] = defaultdict(lambda: defaultdict(list))

    for row in rows:
        result.rows_seen += 1
        if row.round_label.is_final:
            finals_rows.append(row)
        elif row.round_label.round_number not in rounds_by_number:
            # Not team-specific - this round number doesn't exist in the
            # season at all, so it can never be a legitimate position-pairing
            # candidate for any team; catch it here rather than letting a
            # bogus number accidentally satisfy a length/set comparison below.
            result.unmatched.append(
                f"no round {row.round_label.round_number} in season {season_year} (player {row.player_name!r}, team {row.team_name!r})"
            )
        else:
            home_and_away_by_team[row.team_name][row.round_label.round_number].append(row)

    # --- finals resolution: exact (season, team, Round.name) lookup ---
    for row in sorted(finals_rows, key=lambda r: r.round_label.raw):
        team = teams_by_name.get(row.team_name)
        if team is None:
            result.unmatched.append(f"unknown team {row.team_name!r} (player {row.player_name!r}, round {row.round_label.raw})")
            continue
        round_name = ROUND_NAME_BY_FINALS_KIND[row.round_label.kind]
        round_ = rounds_by_name.get(round_name)
        if round_ is None:
            result.unmatched.append(
                f"no {round_name!r} round in season {season_year} (player {row.player_name!r}, team {row.team_name!r})"
            )
            continue
        candidates = [
            m for m in season_matches
            if m.round_id == round_.id and (m.home_team_id == team.id or m.away_team_id == team.id)
        ]
        if len(candidates) == 0:
            result.unmatched.append(
                f"no match found: {row.team_name!r} in round {row.round_label.raw}, {season_year} (player {row.player_name!r})"
            )
            continue
        if len(candidates) > 1:
            result.unmatched.append(
                f"ambiguous match: {row.team_name!r} in round {row.round_label.raw}, {season_year} "
                f"has {len(candidates)} candidate matches (player {row.player_name!r}) — refusing to guess"
            )
            continue
        match = candidates[0]
        resolved.append((row, match))
        claimed_match_ids.add(match.id)

    # --- home-and-away resolution, per (team, season) batch (see module docstring) ---
    for team_name, rounds_map in home_and_away_by_team.items():
        team = teams_by_name.get(team_name)
        if team is None:
            for team_rows in rounds_map.values():
                for row in team_rows:
                    result.unmatched.append(f"unknown team {row.team_name!r} (player {row.player_name!r}, round {row.round_label.raw})")
            continue

        source_round_numbers = sorted(rounds_map.keys())
        team_matches = [m for m in matches_by_team.get(team.id, []) if m.id not in claimed_match_ids]
        squiggle_round_numbers = {m.round.round_number for m in team_matches}

        if set(source_round_numbers) == squiggle_round_numbers:
            # The two sources agree on this team's round numbering this
            # season - safe to trust an exact (round_number, team) lookup.
            for round_number in source_round_numbers:
                candidates = [m for m in matches_by_round_team.get((round_number, team.id), []) if m.id not in claimed_match_ids]
                if len(candidates) == 0:
                    for row in rounds_map[round_number]:
                        result.unmatched.append(
                            f"no match found: {row.team_name!r} in round {row.round_label.raw}, {season_year} (player {row.player_name!r})"
                        )
                    continue
                if len(candidates) > 1:
                    for row in rounds_map[round_number]:
                        result.unmatched.append(
                            f"ambiguous match: {row.team_name!r} in round {row.round_label.raw}, {season_year} "
                            f"has {len(candidates)} candidate matches (player {row.player_name!r}) — refusing to guess"
                        )
                    continue
                match = candidates[0]
                for row in rounds_map[round_number]:
                    resolved.append((row, match))
                claimed_match_ids.add(match.id)
        elif len(source_round_numbers) == len(team_matches):
            # Round numbers disagree between the two sources for this team
            # this season - not safe to trust as labels. Pair the two
            # sequences purely by position (both independently
            # chronological), the only way to resolve them without
            # guessing which literal numbers "really" correspond.
            for round_number, match in zip(source_round_numbers, team_matches):
                for row in rounds_map[round_number]:
                    resolved.append((row, match))
                    if match.round.round_number != round_number:
                        result.fallback_resolved += 1
                claimed_match_ids.add(match.id)
        elif (offset := _find_trailing_round_offset(source_round_numbers, squiggle_round_numbers)) is not None:
            # Neither of the above applies, but shifting every source round
            # number by one CONSTANT amount makes it line up exactly with a
            # PREFIX of this team's Squiggle rounds - i.e. the source's
            # round-number sequence is trustworthy (same competition-wide
            # labelling gap as the equal-length case above, e.g. Opening
            # Round pushing every later label up by one - see module
            # docstring), it's just missing its most-recently-played
            # round(s) because the source hasn't published them yet. Only
            # ever accepted when every unmatched Squiggle round is strictly
            # AFTER every round the offset does match (see
            # _find_trailing_round_offset) - a genuine gap in the middle of
            # the season still falls through to the refusal below, same as
            # today. Reuses the exact (round, team) lookup and its
            # zero/multiple-candidates refusal, just keyed by the shifted
            # round number instead of the raw source label.
            for round_number in source_round_numbers:
                effective_round = round_number + offset
                candidates = [m for m in matches_by_round_team.get((effective_round, team.id), []) if m.id not in claimed_match_ids]
                if len(candidates) == 0:
                    for row in rounds_map[round_number]:
                        result.unmatched.append(
                            f"no match found: {row.team_name!r} in round {row.round_label.raw}, {season_year} "
                            f"(player {row.player_name!r}) — tried shifted round {effective_round} (offset {offset:+d})"
                        )
                    continue
                if len(candidates) > 1:
                    for row in rounds_map[round_number]:
                        result.unmatched.append(
                            f"ambiguous match: {row.team_name!r} in shifted round {effective_round} (offset {offset:+d}), "
                            f"{season_year} has {len(candidates)} candidate matches (player {row.player_name!r}) — refusing to guess"
                        )
                    continue
                match = candidates[0]
                for row in rounds_map[round_number]:
                    resolved.append((row, match))
                    result.fallback_resolved += 1
                claimed_match_ids.add(match.id)
        else:
            for round_number in source_round_numbers:
                for row in rounds_map[round_number]:
                    result.unmatched.append(
                        f"no match found: {row.team_name!r} in round {row.round_label.raw}, {season_year} "
                        f"(player {row.player_name!r}) — this team's source/Squiggle round numbering disagrees "
                        f"({len(source_round_numbers)} source round(s) vs {len(team_matches)} Squiggle match(es) "
                        f"this season), refusing to guess a pairing"
                    )

    # Process the upsert in true chronological (resolved-match) order, not
    # source round-label order - a player who changed teams mid-batch must
    # end up with current_team_id reflecting whichever game was actually
    # played last, and round labels are exactly what this module can't
    # always trust (see above).
    resolved.sort(key=lambda pair: pair[1].scheduled_start)

    # --- upsert every resolved (row, match) pair ---
    for row, match in resolved:
        team = teams_by_name[row.team_name]
        opponent = match.away_team if match.home_team_id == team.id else match.home_team

        player = players_by_source_id.get(row.player_source_id)
        if player is None:
            player = Player(
                sport_id=sport.id,
                display_name=_display_name(row.player_name),
                current_team_id=team.id,
                source=source,
                source_player_id=row.player_source_id,
                source_metadata={"afltables_name_variants": [row.player_name]},
            )
            db.add(player)
            db.flush()
            players_by_source_id[row.player_source_id] = player
            result.players_created += 1
        else:
            if player.display_name != _display_name(row.player_name):
                player.display_name = _display_name(row.player_name)
            if player.current_team_id != team.id:
                player.current_team_id = team.id
            variants = list((player.source_metadata or {}).get("afltables_name_variants", []))
            if row.player_name not in variants:
                variants.append(row.player_name)
                player.source_metadata = {"afltables_name_variants": variants}

        outcome = _upsert_stat(db, player, match, team, opponent, row, source)
        if outcome == "created":
            result.stats_created += 1
        elif outcome == "updated":
            result.stats_updated += 1
        else:
            result.stats_unchanged += 1

    db.commit()
    return result


def _upsert_stat(
    db: Session, player: Player, match: Match, team: Team, opponent: Team, row: PlayerStatLine, source: str
) -> str:
    existing = db.scalar(
        select(PlayerMatchStat).where(
            PlayerMatchStat.player_id == player.id,
            PlayerMatchStat.match_id == match.id,
            PlayerMatchStat.source == source,
        )
    )
    field_values = {field_name: row.stats.get(field_name) for field_name in STAT_FIELDS}

    if existing is None:
        stat = PlayerMatchStat(
            player_id=player.id,
            match_id=match.id,
            team_id=team.id,
            opponent_team_id=opponent.id,
            source=source,
            source_player_id=row.player_source_id,
            recorded_at=row.recorded_at,
            jumper_number=row.jumper_number,
            subbed_on=row.subbed_on,
            subbed_off=row.subbed_off,
            **field_values,
        )
        db.add(stat)
        db.flush()
        return "created"

    changed = False
    for field_name, new_value in field_values.items():
        if getattr(existing, field_name) != new_value:
            setattr(existing, field_name, new_value)
            changed = True
    for attr, new_value in (
        ("team_id", team.id),
        ("opponent_team_id", opponent.id),
        ("jumper_number", row.jumper_number),
        ("subbed_on", row.subbed_on),
        ("subbed_off", row.subbed_off),
        ("source_player_id", row.player_source_id),
    ):
        if getattr(existing, attr) != new_value:
            setattr(existing, attr, new_value)
            changed = True
    if changed:
        existing.recorded_at = row.recorded_at
        return "updated"
    return "unchanged"
