"""NBA schedule, results and box scores from ESPN's public site API
(site.api.espn.com) — the JSON endpoints behind espn.com's own scoreboard
and box-score pages. No key.

Stated plainly: this API is public but UNDOCUMENTED and unofficial. ESPN
publishes no contract, rate limit or terms for it; it can change or
disappear without notice. It was chosen because, of the sources tested on
2026-10-02, it was the only free one that answered at all and it carries
everything the model needs — see docs/NBA_DATA_SOURCES.md for the full
comparison (the NBA's own cdn.nba.com returned 403 Access Denied and
stats.nba.com did not respond; neither block was worked around).

Everything below was verified against real responses on 2026-10-02
(trimmed copies are in tests/fixtures/nba/):

- GET /scoreboard?dates=YYYYMMDD — every game listed under that date. The
  date is the league's own (US Eastern) calendar date, so it is used as the
  game's local `game_date`. Works for past seasons (checked back to
  2015-16) and for the not-yet-played 2026-27 schedule. A date RANGE
  (dates=A-B) returned HTTP 400, so this provider asks one date at a time.
- GET /summary?event=<id> — the box score. Player stats are a list of
  strings positioned by a parallel `keys` list; this parser reads by key
  name, never by position, so a reordered or extended stat list cannot
  silently shift columns.
- `season.year` is the year the season ENDS (2027 for 2026-27).
  `season.type`: 1 preseason, 2 regular season, 3 playoffs, 5 play-in.
- All-Star games are listed as regular-season events between teams that
  are not NBA franchises. They are filtered out by the caller passing the
  set of real team ids (see get_games) rather than by trusting a label.
- For some older games the box score is an EMPTY SHELL: every player is
  listed, but with "--" minutes and 0 for every stat. Discovered on a real
  2015 game (event 400827889: every player on both teams at 0 points in a
  97-95 game); ESPN's other endpoints had no stats for it either. Taken at
  face value this would record a star as scoring zero. The provider
  reports what it was given; ingestion rejects any box score whose player
  points do not add up to the team's score (see app/nba/ingestion.py).
- Occasionally a player row is published with NO athlete id (43 rows in
  the 2015-2026 backfill). The row cannot be attached to anyone, so it is
  dropped; any points it carries are passed on separately so the rest of
  the box score can still be checked. In the cases seen so far the row had
  no stats either: where that player had scored (twelve Chicago games in
  2025-26) the team's points do not reconcile and the game is rejected.
- ESPN sometimes lists the same person under two athlete ids (ten names in
  2015-2026 data, e.g. a long-standing id plus a second one used for a
  handful of games). Nothing here merges them: the ids are the identity.
- Minutes are published as WHOLE minutes ("33"), not minutes:seconds. That
  is a real precision limit for a minutes model.
- Per-player flags: `starter` and `didNotPlay` are reliable. `active` is
  not (it is false for bench players who played) and `reason` is populated
  with "COACH'S DECISION" even for players who played, so the reason is
  only kept when didNotPlay is true.

No request here is retried or parallelised, and a pause is taken between
requests: this is someone else's undocumented endpoint and should be
treated gently.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone

import httpx

from app.models.nba import NbaGameStatus, NbaSeasonType
from app.providers.nba.types import NbaBoxScore, NbaGameRecord, NbaPlayerBoxLine, NbaTeamRecord

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
PROVIDER_NAME = "espn"
DEFAULT_USER_AGENT = "sports-analytics-research/0.1 (personal, non-commercial project)"
DEFAULT_REQUEST_INTERVAL_SECONDS = 0.5

_SEASON_TYPES = {
    1: NbaSeasonType.PRESEASON.value,
    2: NbaSeasonType.REGULAR.value,
    3: NbaSeasonType.PLAYOFFS.value,
    5: NbaSeasonType.PLAY_IN.value,
}

# keys in ESPN's per-team `keys` list -> NbaPlayerBoxLine field(s). A
# "made-attempted" key carries both numbers in one "6-12" string.
_SINGLE_STATS = {
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
    "offensiveRebounds": "offensive_rebounds",
    "defensiveRebounds": "defensive_rebounds",
    "steals": "steals",
    "blocks": "blocks",
    "turnovers": "turnovers",
    "fouls": "personal_fouls",
    "plusMinus": "plus_minus",
}
_MADE_ATTEMPTED_STATS = {
    "fieldGoalsMade-fieldGoalsAttempted": ("field_goals_made", "field_goals_attempted"),
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("three_pointers_made", "three_pointers_attempted"),
    "freeThrowsMade-freeThrowsAttempted": ("free_throws_made", "free_throws_attempted"),
}


class EspnNbaError(Exception):
    """Any request failure (network, non-200, body that is not the expected
    shape). Callers catch this per game so one bad game does not abort a
    whole backfill."""


def _parse_time(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)


def _status(status_type: dict) -> str:
    """ESPN's status -> NbaGameStatus value. Postponed/cancelled are read
    from the status name; everything else from its state, so a status name
    this code has never seen (ESPN has many in-game ones) still lands in the
    right bucket."""
    name = (status_type.get("name") or "").upper()
    if "POSTPONED" in name or "SUSPENDED" in name:
        return NbaGameStatus.POSTPONED.value
    if "CANCEL" in name or "FORFEIT" in name:
        return NbaGameStatus.CANCELLED.value
    state = status_type.get("state")
    if state == "post" and status_type.get("completed"):
        return NbaGameStatus.FINAL.value
    if state == "pre":
        return NbaGameStatus.SCHEDULED.value
    return NbaGameStatus.IN_PROGRESS.value


def _int(raw: str | None) -> int | None:
    if raw is None or raw in ("", "--", "-"):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _float(raw: str | None) -> float | None:
    if raw is None or raw in ("", "--", "-"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _made_attempted(raw: str | None) -> tuple[int | None, int | None]:
    if not raw or "-" not in raw:
        return None, None
    made, _, attempted = raw.partition("-")
    return _int(made), _int(attempted)


class EspnNbaProvider:
    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        request_interval_seconds: float = DEFAULT_REQUEST_INTERVAL_SECONDS,
        timeout_seconds: float = 30.0,
    ):
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=timeout_seconds, headers={"User-Agent": user_agent})
        self._interval = request_interval_seconds
        self._last_request_at: float | None = None

    def _get(self, path: str, params: dict | None = None) -> dict:
        if self._last_request_at is not None and self._interval > 0:
            wait = self._interval - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
        try:
            response = self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise EspnNbaError(f"request to {path} failed: {exc}") from exc
        finally:
            self._last_request_at = time.monotonic()
        if response.status_code != 200:
            raise EspnNbaError(f"{path} {params or ''} returned HTTP {response.status_code}: {response.text[:200]}")
        try:
            return response.json()
        except ValueError as exc:
            raise EspnNbaError(f"{path} did not return JSON") from exc

    def get_teams(self) -> list[NbaTeamRecord]:
        body = self._get("/teams")
        try:
            rows = body["sports"][0]["leagues"][0]["teams"]
            return [
                NbaTeamRecord(source=PROVIDER_NAME, source_team_id=str(row["team"]["id"]), name=row["team"]["displayName"], abbreviation=row["team"]["abbreviation"])
                for row in rows
            ]
        except (KeyError, IndexError, TypeError) as exc:
            raise EspnNbaError(f"/teams response was not in the expected shape: {exc!r}") from exc

    def get_games(self, on: date, *, team_ids: set[str] | None = None) -> list[NbaGameRecord]:
        """Every game ESPN lists under one league calendar date.

        `team_ids`: when given, a game is returned only if BOTH teams are in
        it. Pass the real NBA franchises' ids to drop All-Star games and
        preseason exhibitions against non-NBA opponents. A game in a season
        type this project does not model is dropped too."""
        body = self._get("/scoreboard", {"dates": on.strftime("%Y%m%d")})
        games: list[NbaGameRecord] = []
        try:
            for event in body.get("events", []):
                season_type = _SEASON_TYPES.get(event["season"]["type"])
                if season_type is None:
                    continue
                competition = event["competitions"][0]
                sides = {c["homeAway"]: c for c in competition["competitors"]}
                home, away = sides["home"], sides["away"]
                home_id, away_id = str(home["team"]["id"]), str(away["team"]["id"])
                if team_ids is not None and not {home_id, away_id} <= team_ids:
                    continue
                status = _status(event["status"]["type"])
                season_slug = event["season"].get("slug")
                has_score = status in (NbaGameStatus.FINAL.value, NbaGameStatus.IN_PROGRESS.value)
                games.append(
                    NbaGameRecord(
                        source=PROVIDER_NAME,
                        source_game_id=str(event["id"]),
                        season_start_year=int(event["season"]["year"]) - 1,
                        season_type=season_type,
                        game_date=on,
                        scheduled_start=_parse_time(event["date"]),
                        status=status,
                        home_source_team_id=home_id,
                        away_source_team_id=away_id,
                        home_team_name=home["team"]["displayName"],
                        away_team_name=away["team"]["displayName"],
                        home_score=_int(home.get("score")) if has_score else None,
                        away_score=_int(away.get("score")) if has_score else None,
                        source_status=event["status"]["type"].get("name"),
                        source_season_type=f"{event['season']['type']}:{season_slug}" if season_slug else str(event["season"]["type"]),
                    )
                )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise EspnNbaError(f"/scoreboard response for {on} was not in the expected shape: {exc!r}") from exc
        return games

    def get_box_score(self, source_game_id: str) -> NbaBoxScore:
        body = self._get("/summary", {"event": source_game_id})
        fetched_at = datetime.now(timezone.utc)
        try:
            competition = body["header"]["competitions"][0]
            status_type = competition["status"]["type"]
            team_scores = {str(c["team"]["id"]): _int(c.get("score")) for c in competition.get("competitors", [])}
            status = _status(status_type)
            lines: list[NbaPlayerBoxLine] = []
            malformed = 0
            unattributed_points: dict[str, int] = {}
            for team_block in body.get("boxscore", {}).get("players", []):
                team_id = str(team_block["team"]["id"])
                for group in team_block.get("statistics", []):
                    keys = group.get("keys", [])
                    for row in group.get("athletes", []):
                        athlete = row.get("athlete") or {}
                        if not athlete.get("id") or not athlete.get("displayName"):
                            # No stable player id: the row cannot be attached
                            # to anyone safely, so it is counted, not guessed at.
                            malformed += 1
                            stats = row.get("stats") or []
                            if not row.get("didNotPlay") and stats:
                                unattributed_points[team_id] = unattributed_points.get(team_id, 0) + (_int(dict(zip(keys, stats)).get("points")) or 0)
                            continue
                        lines.append(self._box_line(row, keys, team_id))
        except (KeyError, IndexError, TypeError) as exc:
            raise EspnNbaError(f"/summary response for event {source_game_id} was not in the expected shape: {exc!r}") from exc
        return NbaBoxScore(
            source=PROVIDER_NAME, source_game_id=str(source_game_id), status=status, fetched_at=fetched_at, lines=lines,
            source_status=status_type.get("name"), malformed_rows=malformed,
            team_scores={team: score for team, score in team_scores.items() if score is not None},
            unattributed_points=unattributed_points,
        )

    @staticmethod
    def _box_line(row: dict, keys: list[str], team_id: str) -> NbaPlayerBoxLine:
        athlete = row["athlete"]
        identity = {
            "source_player_id": str(athlete["id"]),
            "player_name": athlete["displayName"],
            "position": (athlete.get("position") or {}).get("abbreviation"),
            "source_team_id": team_id,
        }
        stats = row.get("stats") or []
        if row.get("didNotPlay") or not stats:
            # No stat line means the player did not take the court. The
            # reason is only trustworthy when ESPN itself flags didNotPlay.
            return NbaPlayerBoxLine(**identity, did_not_play=True, did_not_play_reason=row.get("reason") if row.get("didNotPlay") else None)

        by_key = dict(zip(keys, stats))
        values: dict[str, int | None] = {field: _int(by_key.get(key)) for key, field in _SINGLE_STATS.items()}
        for key, (made_field, attempted_field) in _MADE_ATTEMPTED_STATS.items():
            values[made_field], values[attempted_field] = _made_attempted(by_key.get(key))
        return NbaPlayerBoxLine(**identity, did_not_play=False, started=bool(row.get("starter")), minutes=_float(by_key.get("minutes")), **values)
