"""Pure validation checks over already-loaded rows.

Each check takes plain lists of ORM objects (not a DB session) so they can
be unit tested directly against hand-built in-memory rows, without needing a
database at all — see app/validation/run.py for the one place that queries
the DB and hands rows to these functions.
"""

from collections import defaultdict
from datetime import datetime, timezone

from app.models import Match, MatchStatus, Player, PlayerMatchStat, Season, Team, TeamMatchStat, Venue
from app.validation.report import Level, ValidationReport

# AFL has fielded 18 teams since the 2012 Gold Coast/GWS expansion. Bounded
# loosely rather than hardcoded to 18 so a genuine future expansion/contraction
# warns instead of hard-failing.
_EXPECTED_TEAM_COUNT_RANGE = (16, 20)
# VFL/AFL's first season was 1897.
_EARLIEST_SENSIBLE_SEASON = 1897
# A completed home-and-away + finals AFL season is typically ~200-216 matches;
# used only as a soft warning threshold, never a hard failure, since byes,
# expansion years, and finals formats have genuinely varied.
_EXPECTED_SEASON_MATCH_RANGE = (150, 230)


def check_teams(teams: list[Team], report: ValidationReport) -> None:
    if not teams:
        report.add(Level.FAIL, "teams", "No teams found in database")
        return

    n = len(teams)
    low, high = _EXPECTED_TEAM_COUNT_RANGE
    if low <= n <= high:
        report.add(Level.PASS, "teams", f"Team count ({n}) is within the expected AFL range ({low}-{high})")
    else:
        report.add(Level.WARNING, "teams", f"Team count ({n}) is outside the expected AFL range ({low}-{high})")

    seen: dict[tuple[int, str], list[int]] = defaultdict(list)
    for t in teams:
        seen[(t.sport_id, t.name)].append(t.id)
    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    if dupes:
        report.add(Level.FAIL, "teams", f"Duplicate team names found: {dupes}")
    else:
        report.add(Level.PASS, "teams", "No duplicate team names")

    missing = [t.id for t in teams if not t.name or not t.short_name]
    if missing:
        report.add(Level.FAIL, "teams", f"Teams missing a required field (name/short_name): {missing}")
    else:
        report.add(Level.PASS, "teams", "All teams have required fields populated")


def check_venues(venues: list[Venue], report: ValidationReport) -> None:
    if not venues:
        report.add(Level.WARNING, "venues", "No venues found in database")
        return

    seen: dict[str, list[int]] = defaultdict(list)
    for v in venues:
        seen[v.name].append(v.id)
    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    if dupes:
        report.add(Level.FAIL, "venues", f"Duplicate venue names found: {dupes}")
    else:
        report.add(Level.PASS, "venues", "No duplicate venue names")

    missing = [v.id for v in venues if not v.name or not v.name.strip()]
    if missing:
        report.add(Level.FAIL, "venues", f"Venues missing a name: {missing}")
    else:
        report.add(Level.PASS, "venues", "All venues have a name populated")


def check_seasons(seasons: list[Season], report: ValidationReport) -> None:
    if not seasons:
        report.add(Level.FAIL, "seasons", "No seasons found in database")
        return

    current_year = datetime.now(timezone.utc).year
    bad_years = [s.year for s in seasons if s.year < _EARLIEST_SENSIBLE_SEASON or s.year > current_year + 1]
    if bad_years:
        report.add(Level.FAIL, "seasons", f"Season years outside a sensible AFL range: {bad_years}")
    else:
        report.add(Level.PASS, "seasons", "All season years are sensible")

    seen: dict[tuple[int, int], list[int]] = defaultdict(list)
    for s in seasons:
        seen[(s.sport_id, s.year)].append(s.id)
    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    if dupes:
        report.add(Level.FAIL, "seasons", f"Duplicate seasons found: {dupes}")
    else:
        report.add(Level.PASS, "seasons", "No duplicate seasons")

    years = sorted(s.year for s in seasons)
    report.add(Level.PASS, "seasons", f"Loaded season range: {years[0]}-{years[-1]} ({len(years)} seasons)")


def check_matches(
    matches: list[Match],
    season_ids: set[int],
    round_ids: set[int],
    team_ids: set[int],
    report: ValidationReport,
) -> None:
    if not matches:
        report.add(Level.WARNING, "matches", "No matches found in database")
        return

    same_team = [m.id for m in matches if m.home_team_id == m.away_team_id]
    _report_ids(report, "matches", same_team, "Matches with the same team as home and away", "No match has the same team as both home and away")

    orphan_season = [m.id for m in matches if m.season_id not in season_ids]
    _report_ids(report, "matches", orphan_season, "Matches referencing a missing season", "Every match belongs to a valid season")

    orphan_round = [m.id for m in matches if m.round_id not in round_ids]
    _report_ids(report, "matches", orphan_round, "Matches referencing a missing round", "Every match belongs to a valid round")

    orphan_team = [m.id for m in matches if m.home_team_id not in team_ids or m.away_team_id not in team_ids]
    _report_ids(report, "matches", orphan_team, "Matches referencing a missing team", "Every match has valid home/away team references")

    completed_missing_scores = [
        m.id for m in matches if m.status == MatchStatus.COMPLETED and (m.home_score is None or m.away_score is None)
    ]
    _report_ids(report, "matches", completed_missing_scores, "Completed matches missing scores", "All completed matches have scores")

    upcoming_with_scores = [
        m.id for m in matches if m.status == MatchStatus.SCHEDULED and (m.home_score is not None or m.away_score is not None)
    ]
    _report_ids(report, "matches", upcoming_with_scores, "Scheduled matches unexpectedly carry a final score", "No scheduled match has a final score recorded")

    negative_scores = [
        m.id for m in matches
        if (m.home_score is not None and m.home_score < 0) or (m.away_score is not None and m.away_score < 0)
    ]
    _report_ids(report, "matches", negative_scores, "Matches with a negative score", "No match has a negative score")

    missing_start = [m.id for m in matches if m.scheduled_start is None]
    _report_ids(report, "matches", missing_start, "Matches missing scheduled_start", "All matches have a scheduled_start")

    # Only completed-without-scores and scheduled-with-scores (above) are
    # unambiguous status/score mismatches. In-progress with no score yet is
    # common early in a match, so it's a soft warning, not a failure.
    in_progress_no_score = [
        m.id for m in matches if m.status == MatchStatus.IN_PROGRESS and m.home_score is None and m.away_score is None
    ]
    if in_progress_no_score:
        report.add(
            Level.WARNING,
            "matches",
            f"In-progress matches with no score recorded yet (may be normal early in play): {in_progress_no_score}",
        )

    seen_ext: dict[str, list[int]] = defaultdict(list)
    for m in matches:
        ext_id = (m.external_ids or {}).get("squiggle")
        if ext_id is not None:
            seen_ext[str(ext_id)].append(m.id)
    dupes = {k: v for k, v in seen_ext.items() if len(v) > 1}
    if dupes:
        report.add(Level.FAIL, "matches", f"Duplicate Squiggle match ids found: {dupes}")
    else:
        report.add(Level.PASS, "matches", "No duplicate Squiggle match ids")


def _report_ids(report: ValidationReport, category: str, bad_ids: list[int], fail_message: str, pass_message: str) -> None:
    if bad_ids:
        report.add(Level.FAIL, category, f"{fail_message}: {bad_ids}")
    else:
        report.add(Level.PASS, category, pass_message)


def build_season_summary(matches: list[Match], season_year_by_id: dict[int, int]) -> list[str]:
    completed_by_year: dict[int, int] = defaultdict(int)
    upcoming_by_year: dict[int, int] = defaultdict(int)

    for m in matches:
        year = season_year_by_id.get(m.season_id)
        if year is None:
            continue
        if m.status == MatchStatus.COMPLETED:
            completed_by_year[year] += 1
        elif m.status == MatchStatus.SCHEDULED:
            upcoming_by_year[year] += 1

    lines: list[str] = []
    low, high = _EXPECTED_SEASON_MATCH_RANGE
    for year in sorted(set(completed_by_year) | set(upcoming_by_year)):
        completed = completed_by_year.get(year, 0)
        upcoming = upcoming_by_year.get(year, 0)
        if completed and upcoming:
            lines.append(f"{year}: {completed} completed / {upcoming} upcoming")
        elif upcoming:
            lines.append(f"{year}: {upcoming} upcoming")
        else:
            flag = "" if low <= completed <= high else "  <- unusual match count, worth checking"
            lines.append(f"{year}: {completed} matches{flag}")
    return lines


# Rushed (unattributed defensive) behinds are a genuine, expected AFL Tables
# vs official-score discrepancy — verified against real 2016 data before the
# backfill ran (see app/providers/afl/afltables.py's module docstring).
# Typically 0-4 per team per game; flagged only if it looks larger than that
# plausibly-real range, which would suggest an actual parsing/matching bug.
_BEHINDS_TOLERANCE = 6


def check_team_match_stats(
    stats: list[TeamMatchStat], match_scores: dict[tuple[int, int], tuple[int | None, int | None]], report: ValidationReport
) -> None:
    """match_scores: {(match_id, team_id): (official_goals, official_behinds)},
    built from Match.home/away_goals/behinds — the Squiggle-sourced figures
    that remain authoritative regardless of what this check finds."""
    if not stats:
        report.add(Level.WARNING, "team_stats", "No advanced team statistics found in database")
        return

    goal_mismatches = []
    large_behind_mismatches = []
    for s in stats:
        official = match_scores.get((s.match_id, s.team_id))
        if official is None or s.goals is None:
            continue
        official_goals, official_behinds = official
        if official_goals is not None and s.goals != official_goals:
            goal_mismatches.append(s.id)
        if official_behinds is not None and s.behinds is not None and abs(s.behinds - official_behinds) > _BEHINDS_TOLERANCE:
            large_behind_mismatches.append(s.id)

    _report_ids(
        report, "team_stats", goal_mismatches,
        "Team-stat goals disagree with the official match score (unexpected — goals are always individually attributed)",
        "All team-stat goal totals match the official match score",
    )
    if large_behind_mismatches:
        report.add(
            Level.WARNING, "team_stats",
            f"Team-stat behinds differ from the official score by more than {_BEHINDS_TOLERANCE} "
            f"(a few points of difference is expected — AFL Tables' team totals omit unattributed "
            f"'rushed' behinds — but this is larger than that normally explains): {large_behind_mismatches}",
        )
    else:
        report.add(Level.PASS, "team_stats", "Team-stat behinds are within the expected tolerance of the official score")

    seen: dict[tuple[int, int, str], list[int]] = defaultdict(list)
    for s in stats:
        seen[(s.match_id, s.team_id, s.source)].append(s.id)
    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    if dupes:
        report.add(Level.FAIL, "team_stats", f"Duplicate team-match-stat rows found: {dupes}")
    else:
        report.add(Level.PASS, "team_stats", "No duplicate team-match-stat rows")


def build_team_stats_coverage(
    completed_match_ids_by_season: dict[int, set[int]], stat_match_ids_by_season: dict[int, set[int]]
) -> list[str]:
    lines = []
    for year in sorted(completed_match_ids_by_season):
        total = len(completed_match_ids_by_season[year])
        covered = len(stat_match_ids_by_season.get(year, set()) & completed_match_ids_by_season[year])
        pct = (covered / total * 100) if total else 0.0
        lines.append(f"{year}: {covered}/{total} matches ({pct:.0f}%)")
    return lines


# --- Player data foundation ---

# Counting stats that must be non-negative — every field on PlayerMatchStat
# that is a raw count rather than a percentage.
_NON_NEGATIVE_STAT_FIELDS = [
    "kicks", "marks", "handballs", "disposals", "goals", "behinds", "hitouts", "tackles",
    "rebound_50s", "inside_50s", "clearances", "clangers", "frees_for", "frees_against",
    "brownlow_votes", "contested_possessions", "uncontested_possessions", "contested_marks",
    "marks_inside_50", "one_percenters", "bounces", "goal_assists",
]

# Every additive counting stat except behinds — team total = sum of that
# team's player totals, exactly, since both are derived from the same
# underlying scorer data (verified directly against a real match page: the
# player-stats table's own <tfoot> "Totals" row IS the sum of its player
# rows). A mismatch here is a real integrity signal (a parsing or match/team
# resolution bug), not a source-convention quirk — unlike behinds, see
# _BEHINDS_TOLERANCE above, this is the one field genuinely expected to
# differ (rushed/unattributed defensive behinds have no player to credit).
_EXACT_RECONCILE_FIELDS = [
    "kicks", "marks", "handballs", "disposals", "goals", "hitouts", "tackles",
    "rebound_50s", "inside_50s", "clearances", "clangers", "frees_for", "frees_against",
    "brownlow_votes", "contested_possessions", "uncontested_possessions", "contested_marks",
    "marks_inside_50", "one_percenters", "bounces", "goal_assists",
]


def check_players(players: list[Player], team_ids: set[int], report: ValidationReport) -> None:
    if not players:
        report.add(Level.WARNING, "players", "No players found in database")
        return

    missing_name = [p.id for p in players if not p.display_name or not p.display_name.strip()]
    _report_ids(report, "players", missing_name, "Players missing a display name", "All players have a display name")

    seen: dict[tuple[int, str, str], list[int]] = defaultdict(list)
    for p in players:
        seen[(p.sport_id, p.source, p.source_player_id)].append(p.id)
    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    if dupes:
        report.add(Level.FAIL, "players", f"Duplicate (sport, source, source_player_id) found: {dupes}")
    else:
        report.add(Level.PASS, "players", "No duplicate player source ids")

    invalid_team = [p.id for p in players if p.current_team_id is not None and p.current_team_id not in team_ids]
    _report_ids(
        report, "players", invalid_team,
        "Players with an invalid current_team_id", "All players' current_team_id references a valid team",
    )


def check_player_match_stats(
    stats: list[PlayerMatchStat],
    match_ids: set[int],
    player_ids: set[int],
    team_ids: set[int],
    match_team_ids: dict[int, tuple[int, int]],  # match_id -> (home_team_id, away_team_id)
    report: ValidationReport,
) -> None:
    if not stats:
        report.add(Level.WARNING, "player_stats", "No player-match statistics found in database")
        return

    orphan_match = [s.id for s in stats if s.match_id not in match_ids]
    _report_ids(report, "player_stats", orphan_match, "Player-match stats referencing a missing match", "Every player-match stat belongs to a valid match")

    orphan_player = [s.id for s in stats if s.player_id not in player_ids]
    _report_ids(report, "player_stats", orphan_player, "Player-match stats referencing a missing player", "Every player-match stat belongs to a valid player")

    orphan_team = [s.id for s in stats if s.team_id not in team_ids]
    _report_ids(report, "player_stats", orphan_team, "Player-match stats referencing a missing team", "Every player-match stat has a valid team reference")

    team_did_not_play = [
        s.id for s in stats
        if (pair := match_team_ids.get(s.match_id)) is not None and s.team_id not in pair
    ]
    _report_ids(
        report, "player_stats", team_did_not_play,
        "Player-match stats where the recorded team did not actually play in that match",
        "Every player-match stat's team actually participated in that match",
    )

    seen: dict[tuple[int, int, str], list[int]] = defaultdict(list)
    for s in stats:
        seen[(s.player_id, s.match_id, s.source)].append(s.id)
    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    if dupes:
        report.add(Level.FAIL, "player_stats", f"Duplicate player-match-stat rows found: {dupes}")
    else:
        report.add(Level.PASS, "player_stats", "No duplicate player-match-stat rows")

    negative = [
        s.id for s in stats
        if any((v := getattr(s, f)) is not None and v < 0 for f in _NON_NEGATIVE_STAT_FIELDS)
    ]
    _report_ids(report, "player_stats", negative, "Player-match stats with a negative counting stat", "No player-match stat has a negative counting stat")

    disposal_mismatches = [
        s.id for s in stats
        if s.kicks is not None and s.handballs is not None and s.disposals is not None and s.kicks + s.handballs != s.disposals
    ]
    _report_ids(
        report, "player_stats", disposal_mismatches,
        "Player-match stats where kicks + handballs != disposals",
        "kicks + handballs == disposals for every player-match stat with both present",
    )

    bad_tog = [s.id for s in stats if s.time_on_ground_pct is not None and not (0 <= s.time_on_ground_pct <= 100)]
    _report_ids(report, "player_stats", bad_tog, "Player-match stats with time_on_ground_pct outside 0-100", "All time_on_ground_pct values are within 0-100")


def check_player_team_reconciliation(
    player_stats: list[PlayerMatchStat],
    team_stats_by_match_team: dict[tuple[int, int], TeamMatchStat],
    report: ValidationReport,
) -> None:
    """Sums player-level stats per (match, team) and compares against the
    independently-scraped team-level totals — a cross-check between two
    different AFL Tables pages that should agree, since both are ultimately
    derived from the same underlying per-player data. Nothing here modifies
    either table; TeamMatchStat remains authoritative for team-level
    reporting exactly as before."""
    if not player_stats or not team_stats_by_match_team:
        report.add(Level.WARNING, "player_team_reconciliation", "Insufficient data to reconcile player stats against team stats")
        return

    agg: dict[tuple[int, int], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for s in player_stats:
        key = (s.match_id, s.team_id)
        for field_name in _EXACT_RECONCILE_FIELDS + ["behinds"]:
            value = getattr(s, field_name)
            if value is not None:
                agg[key][field_name] += value

    n_checked = 0
    mismatches_by_field: dict[str, list] = defaultdict(list)
    behind_mismatches = []
    for key, sums in agg.items():
        team_stat = team_stats_by_match_team.get(key)
        if team_stat is None:
            continue
        n_checked += 1
        for field_name in _EXACT_RECONCILE_FIELDS:
            team_value = getattr(team_stat, field_name)
            if team_value is not None and sums.get(field_name, 0) != team_value:
                mismatches_by_field[field_name].append(key)
        if team_stat.behinds is not None and abs(sums.get("behinds", 0) - team_stat.behinds) > _BEHINDS_TOLERANCE:
            behind_mismatches.append(key)

    if n_checked == 0:
        report.add(Level.WARNING, "player_team_reconciliation", "No (match, team) pairs had both player-level and team-level stats to compare")
        return

    any_exact_mismatch = False
    for field_name in _EXACT_RECONCILE_FIELDS:
        mismatches = mismatches_by_field.get(field_name, [])
        if mismatches:
            any_exact_mismatch = True
            report.add(
                Level.FAIL, "player_team_reconciliation",
                f"Sum of player {field_name} disagrees with team {field_name} for {len(mismatches)}/{n_checked} team-matches: {mismatches[:10]}",
            )
    if not any_exact_mismatch:
        report.add(
            Level.PASS, "player_team_reconciliation",
            f"Sum of player stats matches team stats exactly across all {n_checked} team-matches checked, for every field except behinds",
        )

    if behind_mismatches:
        report.add(
            Level.WARNING, "player_team_reconciliation",
            f"Sum of player behinds differs from team behinds by more than {_BEHINDS_TOLERANCE} for "
            f"{len(behind_mismatches)}/{n_checked} team-matches (expected — rushed/unattributed defensive "
            f"behinds have no player to credit): {behind_mismatches[:10]}",
        )
    else:
        report.add(Level.PASS, "player_team_reconciliation", f"Sum of player behinds is within tolerance of team behinds for all {n_checked} team-matches checked")


def build_player_stats_coverage(
    completed_match_ids_by_season: dict[int, set[int]], player_stat_match_ids_by_season: dict[int, set[int]]
) -> list[str]:
    lines = []
    for year in sorted(completed_match_ids_by_season):
        total = len(completed_match_ids_by_season[year])
        covered = len(player_stat_match_ids_by_season.get(year, set()) & completed_match_ids_by_season[year])
        pct = (covered / total * 100) if total else 0.0
        lines.append(f"{year}: {covered}/{total} matches ({pct:.0f}%)")
    return lines
