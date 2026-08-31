"""PlayerStatLine provider backed by AFL Tables (afltables.com), sourced
from each team's own season "game by game" grid page —
afl/stats/teams/{team_slug}/{year}_gbg.html — rather than one request per
match.

Why this page and not the per-match box-score page: AFL Tables publishes
full per-round, per-player stats in TWO different places — (a) each
individual match's own page (~207 requests/season for the full home-and-away
+ finals fixture list), or (b) one page per team per season, structured as
24 separate Player-x-Round grid tables (one table per stat category:
Disposals, Kicks, Marks, ..., % Played, and a "Subs" table using On/Off/-
text instead of numbers). Fetching all 18 teams' pages for a season (18
requests) covers every match that season exactly once per side, since every
match's two teams are covered by their own two team pages — an ~11x
reduction in requests versus the per-match approach, which matters given
this project's stated respect for the source (see the module docstring in
afltables.py for the same principle applied to team stats). This is *not*
"one request per player" (the pattern the project is explicitly avoiding) —
it's the season-level bulk page the project prefers when the source offers
one.

Team-name -> URL-slug mapping is NOT a simple lowercase/strip-spaces
transform (e.g. "Brisbane Lions" -> "brisbanel", "Western Bulldogs" ->
"bullldogs" [sic, a genuine three-l typo baked into AFL Tables' own URLs],
"North Melbourne" -> "kangaroos") — verified against real fetched pages for
all 18 current-era clubs, see _TEAM_SLUGS below.

Round labels (e.g. "R6", "EF", "GF") are the grid's own column headers, not
match dates or ids — normalised via app/providers/afl/round_labels.py, then
resolved against already-ingested Match/Round rows by
(season_year, round_label, team) in app/ingestion/player_stats.py, the
same "no shared id scheme" situation team stats already handles (see
TeamStatLine), just keyed by round instead of date.
"""

import re
import time
from datetime import datetime, timezone

from app.config import get_settings
from app.providers.afl.afltables import Transport, curl_transport
from app.providers.afl.round_labels import RoundLabel, parse_round_label
from app.providers.types import PlayerStatLine

AFLTABLES_BASE_URL = "https://afltables.com/afl/stats/teams/"

# Verified 2026-08-15 against real afltables.com pages (via web.archive.org,
# since direct access was temporarily blocked by the site's bot-mitigation
# at verification time — see the player-data-foundation stage report for
# details). Covers every club that has played in the 18-team era
# (2012-present); this project's backfill only needs 2016+.
_TEAM_SLUGS: dict[str, str] = {
    "Adelaide": "adelaide",
    "Brisbane Lions": "brisbanel",
    "Carlton": "carlton",
    "Collingwood": "collingwood",
    "Essendon": "essendon",
    "Fremantle": "fremantle",
    "Geelong": "geelong",
    "Gold Coast": "goldcoast",
    "Greater Western Sydney": "gws",
    "Hawthorn": "hawthorn",
    "Melbourne": "melbourne",
    "North Melbourne": "kangaroos",
    "Port Adelaide": "padelaide",
    "Richmond": "richmond",
    "St Kilda": "stkilda",
    "Sydney": "swans",
    "West Coast": "westcoast",
    "Western Bulldogs": "bullldogs",
}

# Table title -> the field name it fills in PlayerStatLine.stats, matching
# PlayerMatchStat's column names exactly. "Subs" is handled separately (see
# _parse_subs_cell), not included here.
_STAT_TABLE_FIELDS: dict[str, str] = {
    "Disposals": "disposals",
    "Kicks": "kicks",
    "Marks": "marks",
    "Handballs": "handballs",
    "Goals": "goals",
    "Behinds": "behinds",
    "Hit Outs": "hitouts",
    "Tackles": "tackles",
    "Rebounds": "rebound_50s",
    "Inside 50s": "inside_50s",
    "Clearances": "clearances",
    "Clangers": "clangers",
    "Frees": "frees_for",
    "Frees Against": "frees_against",
    "Brownlow Votes": "brownlow_votes",
    "Contested Possessions": "contested_possessions",
    "Uncontested Possessions": "uncontested_possessions",
    "Contested Marks": "contested_marks",
    "Marks Inside 50": "marks_inside_50",
    "One Percenters": "one_percenters",
    "Bounces": "bounces",
    "Goal Assists": "goal_assists",
    "% Played": "time_on_ground_pct",
}

_TABLE_TITLE_RE = re.compile(r'<th colspan="\d+">([^<]+)</th>')
_TABLE_BLOCK_RE = re.compile(r'<table class="sortable".*?</table>', re.DOTALL)
# Matches both numbered home-and-away columns ("R6") and bare finals codes
# ("EF"/"QF"/"SF"/"PF"/"GF") — real header cells for both look identical
# apart from the text (`<th width="N%">...</th>` or, on the live site as of
# 2026-08 - unlike the Wayback-archived pages this was first verified
# against - `<th width=N%>...</th>` with no quotes at all: afltables.com
# changed its markup at some point after those archived snapshots were
# taken. A real bug found live: the quoted-only version of this regex
# doesn't error on the unquoted form, it just matches nothing, so a whole
# team-season silently comes back with zero rows instead of failing
# loudly - caught while investigating why the current, in-progress 2026
# season had no player-stats coverage at all. The width attribute's quotes
# are optional here for exactly that reason.); the trailing "Tot" column
# has no width attribute, so it's naturally excluded. See round_labels.py
# for interpreting the captured text.
_ROUND_HEADER_RE = re.compile(r'<th width="?\d+%"?>([A-Z0-9]+)</th>')
_PLAYER_ROW_RE = re.compile(
    r'<tr><td><a href="([^"]*players/[^"]+\.html)">([^<]+)</a></td>(.*?)</tr>', re.DOTALL
)
_CELL_RE = re.compile(r"<td[^>]*>([^<]*)</td>")
_PLAYER_SOURCE_ID_RE = re.compile(r"(players/[^/\"]+/[^/\"]+\.html)")


def _extract_player_source_id(href: str) -> str | None:
    match = _PLAYER_SOURCE_ID_RE.search(href)
    return match.group(1) if match else None


def _parse_int_cell(text: str) -> int | None:
    text = text.strip()
    if not text or text == "&nbsp;":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _parse_table(table_html: str) -> tuple[list[RoundLabel], dict[str, tuple[str, list[str]]]] | None:
    """Returns (round_labels, {player_source_id: (player_name, cell_values)}).
    cell_values is aligned index-for-index with round_labels; the trailing
    "Tot" column is deliberately excluded (round-level cells only).

    Returns None if any column header fails to parse as a recognised round
    label — refusing to guess an alignment for the rest of the table rather
    than risk silently shifting every subsequent column."""
    raw_labels = _ROUND_HEADER_RE.findall(table_html)
    round_labels = [parse_round_label(raw) for raw in raw_labels]
    if any(label is None for label in round_labels):
        return None
    n_rounds = len(round_labels)

    players: dict[str, tuple[str, list[str]]] = {}
    for href, name, rest in _PLAYER_ROW_RE.findall(table_html):
        source_id = _extract_player_source_id(href)
        if source_id is None:
            continue
        cells = _CELL_RE.findall(rest)
        if len(cells) < n_rounds:
            continue  # malformed row for this table - skip rather than misalign
        players[source_id] = (name.strip(), cells[:n_rounds])
    return round_labels, players


def _parse_subs_cell(text: str) -> tuple[bool, bool]:
    text = text.strip()
    return (text == "On", text == "Off")


class AFLTablesPlayerStatsProvider:
    """Not a StatsProvider subclass — same rationale as AFLTablesStatsProvider:
    this source only exposes whole-team-season pages, not a per-match query
    shape. get_team_season_player_stats() is this provider's real interface.
    """

    def __init__(self, transport: Transport | None = None, request_delay_seconds: float = 1.0):
        settings = get_settings()
        self._user_agent = settings.squiggle_user_agent
        self._transport = transport or (lambda url: curl_transport(url, self._user_agent))
        self._request_delay_seconds = request_delay_seconds

    @staticmethod
    def known_team_names() -> list[str]:
        return list(_TEAM_SLUGS.keys())

    def get_team_season_player_stats(self, sport_code: str, season_year: int, team_name: str) -> list[PlayerStatLine]:
        if sport_code != "AFL":
            raise ValueError(f"AFLTablesPlayerStatsProvider only supports AFL, got {sport_code!r}")
        slug = _TEAM_SLUGS.get(team_name)
        if slug is None:
            raise ValueError(f"No known AFL Tables URL slug for team {team_name!r}")

        url = f"{AFLTABLES_BASE_URL}{slug}/{season_year}_gbg.html"
        status_code, _content_type, body = self._transport(url)
        if status_code != 200:
            raise RuntimeError(f"AFL Tables returned HTTP {status_code} for {url}")
        if self._request_delay_seconds:
            time.sleep(self._request_delay_seconds)
        return self._parse_team_season_page(body, sport_code, season_year, team_name)

    @staticmethod
    def _parse_team_season_page(html: str, sport_code: str, season_year: int, team_name: str) -> list[PlayerStatLine]:
        """Note: colspan values in the table-title header row are NOT a
        fixed page-format constant — they equal however many round columns
        that specific team-season happened to have (affected by byes and
        how far into finals that team went), so _TABLE_TITLE_RE matches any
        colspan rather than a hardcoded number (a real bug caught during
        the first live backfill run: hardcoding the one value seen in a
        single 2024 sample page silently broke every earlier season, which
        have different round-column counts and therefore different
        colspans). The "Subs" table is also not present in older seasons
        (verified absent — not merely malformed — as far back as
        2016/2017): treated as optional, with subbed_on/subbed_off simply
        left False (not tracked by the source that year) rather than the
        whole team-season's data being discarded over a table AFL Tables
        itself didn't publish yet.
        """
        recorded_at = datetime.now(timezone.utc)

        titles = _TABLE_TITLE_RE.findall(html)
        blocks = _TABLE_BLOCK_RE.findall(html)
        if len(titles) != len(blocks):
            raise RuntimeError(
                f"AFL Tables game-by-game page for {team_name} {season_year}: "
                f"{len(titles)} table titles but {len(blocks)} table blocks - page structure unrecognised, refusing to guess."
            )
        tables_by_title = dict(zip(titles, blocks))

        # Parse every known stat table; a table this page doesn't have (e.g.
        # an older/newer season with a slightly different field set), or
        # whose column headers didn't parse cleanly (_parse_table returning
        # None), is simply absent from parsed_stats - its field stays
        # unpopulated rather than guessed.
        parsed_stats: dict[str, tuple[list[RoundLabel], dict[str, tuple[str, list[str]]]]] = {}
        for title, field_name in _STAT_TABLE_FIELDS.items():
            if title in tables_by_title:
                parsed = _parse_table(tables_by_title[title])
                if parsed is not None:
                    parsed_stats[field_name] = parsed
        if not parsed_stats:
            raise RuntimeError(
                f"AFL Tables game-by-game page for {team_name} {season_year}: no recognised stat tables found."
            )

        # Reference table for "which players, which rounds" - disposals is
        # the most universally tracked stat (present in every season
        # observed) so it's preferred; falls back to whatever's available
        # if a page genuinely lacks it.
        reference_field = "disposals" if "disposals" in parsed_stats else next(iter(parsed_stats))
        reference_rounds, reference_players = parsed_stats[reference_field]

        subs_rounds: list[RoundLabel] | None = None
        subs_players: dict[str, tuple[str, list[str]]] = {}
        if "Subs" in tables_by_title:
            subs_parsed = _parse_table(tables_by_title["Subs"])
            if subs_parsed is not None:
                subs_rounds, subs_players = subs_parsed

        results: list[PlayerStatLine] = []
        for source_id, (player_name, reference_cells) in reference_players.items():
            for i, round_label in enumerate(reference_rounds):
                reference_cell = reference_cells[i].strip()
                if not reference_cell or reference_cell == "&nbsp;":
                    continue  # player did not feature this round - no row, not a guessed zero

                subbed_on = subbed_off = False
                if subs_rounds == reference_rounds:
                    subs_entry = subs_players.get(source_id)
                    if subs_entry is not None:
                        subbed_on, subbed_off = _parse_subs_cell(subs_entry[1][i])

                stats: dict[str, float] = {}
                for field_name, (round_labels, players) in parsed_stats.items():
                    if round_labels != reference_rounds:
                        continue  # this table's columns don't line up with the reference table - skip this field, don't misalign
                    entry = players.get(source_id)
                    if entry is None:
                        continue
                    value = _parse_int_cell(entry[1][i])
                    # player is confirmed to have featured this round (the
                    # reference cell was non-blank) - a blank cell in a
                    # specific stat category for a played round is a
                    # genuine zero, not an unknown, matching AFL Tables'
                    # own convention.
                    stats[field_name] = 0 if value is None else value

                results.append(
                    PlayerStatLine(
                        sport_code=sport_code,
                        season_year=season_year,
                        round_label=round_label,
                        team_name=team_name,
                        player_name=player_name,
                        player_source_id=source_id,
                        recorded_at=recorded_at,
                        stats=stats,
                        jumper_number=None,  # not published on this page format
                        subbed_on=subbed_on,
                        subbed_off=subbed_off,
                    )
                )

        return results
