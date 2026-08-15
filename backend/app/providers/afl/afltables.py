"""TeamStatLine provider backed by AFL Tables (afltables.com).

AFL Tables has no public API — it publishes plain HTML pages, stable in
structure since long before this project. One page per season
(afl/stats/{year}t.html) contains every team's full-season team-level box
score (two tables per team: core disposal/scoring stats, then a second
possession/pressure-stats table), each row linking to that match's own page.
That means a full-season backfill is exactly one HTTP request per season —
not one per team or per match — which is both the practical way to use this
source and a naturally light footprint on it.

No shared id scheme exists between this source and Squiggle (our fixture
source), so each row carries (opponent_name, match_date) rather than a
match_external_id ingestion can look up directly — see TeamStatLine and
app/ingestion/team_stats.py, which resolves rows against already-ingested
Match rows by (season, team pair, date) instead.

Known, documented limitation (verified against real 2016/2024 pages before
this backfill ran): AFL Tables' team-level "goals"/"behinds" are summed from
player rows and can undercount the official final score by a few points —
"rushed" (unattributed defensive) behinds have no player to attribute to.
Match.home_goals/home_behinds (from Squiggle) remain authoritative; this
source's goals/behinds are stored for provenance only. See
app/validation/checks.py for the cross-check this discrepancy is validated
against, and README/final-report notes for the worked example that caught it.

Transport note: reuses the curl-subprocess approach from squiggle.py for
consistency, though no equivalent Cloudflare quirk has been observed here —
simpler to keep one HTTP-fetching pattern in the codebase than two.
"""

import re
import subprocess
import time
from collections.abc import Callable
from datetime import date, datetime, timezone

from app.config import get_settings
from app.providers.types import TeamStatLine

AFLTABLES_BASE_URL = "https://afltables.com/afl/stats/"

# Column order exactly as published — see the module docstring's "two tables
# per team" note. Field names are the modelling-facing names stored on
# TeamMatchStat; comments show AFL Tables' own abbreviation for traceability.
_TABLE1_FIELDS = [
    "kicks", "marks", "handballs", "disposals", "goals", "behinds",  # KI MK HB DI GL BH
    "hitouts", "tackles", "rebound_50s", "inside_50s", "clearances", "clangers",  # HO TK RB IF CL CG
]
_TABLE2_FIELDS = [
    "frees_for", "frees_against", "brownlow_votes",  # FF FA BR
    "contested_possessions", "uncontested_possessions", "contested_marks", "marks_inside_50",  # CP UP CM MI
    "one_percenters", "bounces", "goal_assists",  # 1% BO GA
]

_TEAM_ANCHOR_RE = re.compile(r'<a name="(\d+)"></a>')
_TEAM_NAME_RE = re.compile(r">([A-Za-z0-9 ]+) Team Statistics")
_SORTABLE_TABLE_RE = re.compile(r'<table class="sortable".*?</table>', re.DOTALL)

# AFL Tables' HTML never actually closes <tbody> (verified: zero occurrences
# in real fetched pages — old-school markup relying on browsers to
# auto-close it). Without </table> as a third lookahead terminator, the
# *last* row in every team's table — whichever match that happens to be:
# a Grand Final for finalists, their last home-and-away game otherwise —
# had no valid stopping point and was silently dropped by findall(). Caught
# by cross-checking real backfill coverage against known completed matches;
# see tests/test_afltables_provider.py for a regression test using this
# same never-closed-tbody structure.
_ROW_RE = re.compile(
    r'<a href="(games/\d+/[^"]+\.html)">([^<]+)</a></td><td>([^<]*)</td>(.*?)(?=<a href="games/|</tbody>|</table>)',
    re.DOTALL,
)
_CELL_RE = re.compile(r"<td align=center>([^<]*)</td>")

Transport = Callable[[str], tuple[int, str, str]]


def curl_transport(url: str, user_agent: str, timeout: float = 30.0) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["curl", "-s", "-i", "-A", user_agent, "--max-time", str(timeout), url],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "AFLTablesStatsProvider requires the `curl` command-line tool "
            "(bundled with Windows 10+ and most Linux/macOS installs), but it wasn't found on PATH."
        ) from exc

    if result.returncode != 0:
        raise RuntimeError(f"curl failed (exit {result.returncode}): {result.stderr.strip()}")

    header_block, separator, body = result.stdout.partition("\r\n\r\n")
    if not separator:
        header_block, _, body = result.stdout.partition("\n\n")

    header_lines = header_block.splitlines()
    status_parts = header_lines[0].split() if header_lines else []
    status_code = int(status_parts[1]) if len(status_parts) > 1 and status_parts[1].isdigit() else 0
    return status_code, "", body


def _parse_cell(text: str) -> int | None:
    """Each cell is "self-opponent" (e.g. "220-227"), except finals' blank
    Brownlow-votes cell, which is genuinely empty — that stays None rather
    than being coerced to 0, since 0 votes and "not applicable" are different
    facts."""
    text = text.strip()
    if not text or text == "-":
        return None
    if "-" in text:
        left, _, _right = text.rpartition("-")
        try:
            return int(left)
        except ValueError:
            return None
    try:
        return int(text)
    except ValueError:
        return None


class AFLTablesStatsProvider:
    """Not a StatsProvider subclass: that ABC's per-match query shape doesn't
    fit a source that only exposes whole-season pages — see the module
    docstring. get_season_team_stats() is this provider's real interface."""

    def __init__(self, transport: Transport | None = None, request_delay_seconds: float = 1.0):
        settings = get_settings()
        self._user_agent = settings.squiggle_user_agent
        self._transport = transport or (lambda url: curl_transport(url, self._user_agent))
        self._request_delay_seconds = request_delay_seconds

    def get_season_team_stats(self, sport_code: str, season_year: int) -> list[TeamStatLine]:
        self._require_afl(sport_code)
        url = f"{AFLTABLES_BASE_URL}{season_year}t.html"
        status_code, _content_type, body = self._transport(url)
        if status_code != 200:
            raise RuntimeError(f"AFL Tables returned HTTP {status_code} for {url}")
        if self._request_delay_seconds:
            time.sleep(self._request_delay_seconds)
        return self._parse_season_page(body, season_year)

    @staticmethod
    def _require_afl(sport_code: str) -> None:
        if sport_code != "AFL":
            raise ValueError(f"AFLTablesStatsProvider only supports AFL, got {sport_code!r}")

    @staticmethod
    def _parse_season_page(html: str, season_year: int) -> list[TeamStatLine]:
        recorded_at = datetime.now(timezone.utc)
        anchors = list(_TEAM_ANCHOR_RE.finditer(html))
        results: list[TeamStatLine] = []

        for i, anchor_match in enumerate(anchors):
            start = anchor_match.end()
            end = anchors[i + 1].start() if i + 1 < len(anchors) else len(html)
            chunk = html[start:end]

            name_match = _TEAM_NAME_RE.search(chunk)
            if not name_match:
                continue
            team_name = name_match.group(1).strip()

            tables = _SORTABLE_TABLE_RE.findall(chunk)
            if len(tables) < 2:
                continue  # malformed/unexpected page section — skip this team rather than guess

            rows1 = _ROW_RE.findall(tables[0])
            rows2 = _ROW_RE.findall(tables[1])
            if len(rows1) != len(rows2):
                continue  # table pairing broken for this team — don't risk misaligned data

            for (url, _round_label, opponent, cells1_blob), (_u2, _r2, _o2, cells2_blob) in zip(rows1, rows2):
                cells1 = _CELL_RE.findall(cells1_blob)
                cells2 = _CELL_RE.findall(cells2_blob)
                if len(cells1) != len(_TABLE1_FIELDS) or len(cells2) != len(_TABLE2_FIELDS):
                    continue  # unexpected column count for this row — skip rather than misalign fields

                stats: dict[str, float] = {}
                for field_name, text in zip(_TABLE1_FIELDS, cells1):
                    value = _parse_cell(text)
                    if value is not None:
                        stats[field_name] = value
                for field_name, text in zip(_TABLE2_FIELDS, cells2):
                    value = _parse_cell(text)
                    if value is not None:
                        stats[field_name] = value

                date_str = url.rsplit("/", 1)[-1].replace(".html", "")[-8:]
                try:
                    match_date = date(int(date_str[0:4]), int(date_str[4:6]), int(date_str[6:8]))
                except (ValueError, IndexError):
                    continue  # unparseable date suffix — can't safely resolve this row to a match

                results.append(
                    TeamStatLine(
                        match_external_id=url,
                        sport_code="AFL",
                        team_name=team_name,
                        recorded_at=recorded_at,
                        stats=stats,
                        opponent_name=opponent.strip(),
                        match_date=match_date,
                    )
                )

        return results
