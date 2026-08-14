"""Pure validation checks over already-loaded rows.

Each check takes plain lists of ORM objects (not a DB session) so they can
be unit tested directly against hand-built in-memory rows, without needing a
database at all — see app/validation/run.py for the one place that queries
the DB and hands rows to these functions.
"""

from collections import defaultdict
from datetime import datetime, timezone

from app.models import Match, MatchStatus, Season, Team, Venue
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
