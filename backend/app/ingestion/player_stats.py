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
   handles, just keyed by (season, round_number, team) here instead of
   (season, team pair, date), since this source's page publishes round
   labels, not match dates, per cell (see afltables_players.py). A team
   plays at most one match per round in a normal season; if that's ever not
   true for the data seen, or the round/team can't be found at all, the row
   is reported as unmatched rather than guessed.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, Player, PlayerMatchStat, Round, Season, Sport, Team
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

    season_matches = db.scalars(
        select(Match).where(Match.season_id == season.id)
    ).all()
    matches_by_round_team: dict[tuple[int, int], list[Match]] = {}
    for m in season_matches:
        matches_by_round_team.setdefault((m.round.round_number, m.home_team_id), []).append(m)
        matches_by_round_team.setdefault((m.round.round_number, m.away_team_id), []).append(m)

    players_by_source_id = {
        p.source_player_id: p
        for p in db.scalars(
            select(Player).where(Player.sport_id == sport.id, Player.source == source)
        ).all()
    }

    # Chronological order so, for a player appearing in multiple rounds
    # within this batch, current_team_id ends up reflecting the most recent
    # one seen — matters for mid-batch trades within the same backfill run.
    for row in sorted(rows, key=lambda r: r.round_number):
        result.rows_seen += 1

        team = teams_by_name.get(row.team_name)
        if team is None:
            result.unmatched.append(f"unknown team {row.team_name!r} (player {row.player_name!r}, round {row.round_number})")
            continue

        round_ = rounds_by_number.get(row.round_number)
        if round_ is None:
            result.unmatched.append(
                f"no round {row.round_number} in season {season_year} (player {row.player_name!r}, team {row.team_name!r})"
            )
            continue

        candidates = matches_by_round_team.get((row.round_number, team.id), [])
        if len(candidates) == 0:
            result.unmatched.append(
                f"no match found: {row.team_name!r} in round {row.round_number}, {season_year} (player {row.player_name!r})"
            )
            continue
        if len(candidates) > 1:
            result.unmatched.append(
                f"ambiguous match: {row.team_name!r} in round {row.round_number}, {season_year} "
                f"has {len(candidates)} candidate matches (player {row.player_name!r}) — refusing to guess"
            )
            continue
        match = candidates[0]
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
