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
   afltables_players.py). Three strategies, tried in order, each only ever
   resolving a row when EXACTLY ONE candidate match exists:

   a. Primary (home-and-away): (season, round_number, team). Fast, exact,
      covers the large majority of rows.

   b. Finals: AFL Tables labels finals EF/QF/SF/PF/GF (see
      app/providers/afl/round_labels.py) — Squiggle groups EF and QF under
      one round ("Finals Week 1"), so resolution here is by (season, team,
      Round.name) rather than any numeric round conversion, which the
      brief this implements explicitly calls unsafe. A team appears at
      most once in any finals round, so this is still exact.

   c. Fallback (only for rows strategy (a)/(b) couldn't place, always
      home-and-away rows — a genuine round-numbering discrepancy between
      the two sources, e.g. a bye Squiggle records at a different round
      number than AFL Tables' grid implies; see PART C of the stage brief
      this implements for the real example that motivated this). AFL
      Tables' player-stats page provides no match date or opponent per
      cell — only a round label — so a literal "date ±1 day" match isn't
      directly available from this source for these specific rows.
      Instead: for a team+season, after (a) and (b) have claimed every
      match they can, this resolves the REMAINING unclaimed AFL-Tables
      round labels against the REMAINING unclaimed Squiggle matches for
      that team — but ONLY when both leftover sets are exactly the same
      size, pairing them in the one order-preserving way (source round
      label ascending <-> Match.scheduled_start ascending), since both
      sequences are independently known to be chronological. If the sizes
      differ (or there's more than one plausible pairing), nothing is
      resolved — the rows are reported as unmatched, not guessed. This is
      the safe fallback the brief asks for, adapted to what this specific
      source page actually exposes (round labels, not dates); see
      app/ingestion/team_stats.py for the analogous date-based fallback
      used where the source DOES publish dates.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, Player, PlayerMatchStat, Round, Season, Sport, Team
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

    season_matches = db.scalars(select(Match).where(Match.season_id == season.id)).all()
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

    # --- pass 1: primary (round-number) and finals (round-name) resolution ---
    # Chronological-ish order (numbered rounds first by number, finals rows
    # after) so, for a player appearing in multiple rounds within this
    # batch, current_team_id ends up reflecting the most recent one seen —
    # matters for mid-batch trades within the same backfill run.
    rows_sorted = sorted(rows, key=lambda r: (r.round_label.is_final, r.round_label.round_number or 0))
    claimed_match_ids: set[int] = set()
    resolved: list[tuple[PlayerStatLine, Match]] = []
    unresolved_home_and_away: list[PlayerStatLine] = []

    for row in rows_sorted:
        result.rows_seen += 1
        team = teams_by_name.get(row.team_name)
        if team is None:
            result.unmatched.append(f"unknown team {row.team_name!r} (player {row.player_name!r}, round {row.round_label.raw})")
            continue

        if row.round_label.is_final:
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
        else:
            round_ = rounds_by_number.get(row.round_label.round_number)
            if round_ is None:
                result.unmatched.append(
                    f"no round {row.round_label.round_number} in season {season_year} (player {row.player_name!r}, team {row.team_name!r})"
                )
                continue
            candidates = matches_by_round_team.get((row.round_label.round_number, team.id), [])

        if len(candidates) == 0:
            unresolved_home_and_away.append(row)
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

    # --- pass 2: fallback for leftover home-and-away rows (see module docstring) ---
    # Grouped by (team, round_number) rather than per-row: a single
    # mismatched round produces one row per player on that team's list
    # (~20+ rows), but represents exactly ONE leftover round needing ONE
    # leftover match — comparing raw row counts to match counts would
    # almost never align even in the simple, genuinely-resolvable case.
    unresolved_by_team: dict[str, dict[int, list[PlayerStatLine]]] = defaultdict(lambda: defaultdict(list))
    for row in unresolved_home_and_away:
        unresolved_by_team[row.team_name][row.round_label.round_number].append(row)

    for team_name, rounds_map in unresolved_by_team.items():
        team = teams_by_name.get(team_name)
        if team is None:
            continue  # already reported as unknown-team in pass 1's row, if applicable
        unclaimed = [m for m in matches_by_team.get(team.id, []) if m.id not in claimed_match_ids]
        unresolved_round_numbers = sorted(rounds_map.keys())

        if len(unresolved_round_numbers) != len(unclaimed):
            for round_number in unresolved_round_numbers:
                for row in rounds_map[round_number]:
                    result.unmatched.append(
                        f"no match found: {row.team_name!r} in round {row.round_label.raw}, {season_year} "
                        f"(player {row.player_name!r}) — {len(unresolved_round_numbers)} unresolved source round(s) "
                        f"{unresolved_round_numbers} vs {len(unclaimed)} unclaimed match(es) for this team, "
                        f"refusing to guess a pairing"
                    )
            continue

        for round_number, match in zip(unresolved_round_numbers, unclaimed):
            for row in rounds_map[round_number]:
                resolved.append((row, match))
                result.fallback_resolved += 1
            claimed_match_ids.add(match.id)

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
