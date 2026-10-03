"""Present-tense NBA evidence from ESPN's public APIs: the league injury
feed, team rosters, team depth charts, and a game's listed lineup. Same
standing as espn.py: public, undocumented, unofficial, no key.

Two hosts are used: site.api.espn.com (injuries, team roster) and
sports.core.api.espn.com (depth chart, per-game roster and game flags).

Verified against real responses on 2026-10-02, during the 2026-27 preseason
(trimmed copies are in tests/fixtures/nba/):

Injury feed  GET /injuries
- One block per team THAT HAS ENTRIES: 24 of 30 teams were present. A team
  or a player being absent says nothing about health.
- Each entry has an id, `status` ("Out", "Day-To-Day" were the only two
  seen), `date` (the source's own timestamp for the entry), a `type`
  (id/name/abbreviation), `details` (type, location, side, detail,
  returnDate, fantasyStatus), `shortComment` and `longComment`.
- The feed itself carries a `timestamp`, which is simply when the response
  was generated.
- The athlete object has NO id field. The player's id only appears inside
  the athlete's link URLs (".../id/5105571/henri-veesaar"). It is taken
  from there - it is ESPN's own id, not a guess - and an entry with no such
  link is counted as unresolvable rather than matched by name.
- The team the entry is listed under and the team on the athlete record
  disagreed for 8 of 59 entries. Both are passed through.
- The game summary also carries an injury list per team, but it omitted a
  player the league feed and the team roster both listed, so the league
  feed is the one used.

Team roster  GET /teams/{id}/roster
- The listed roster with a roster status per player ("Active" for all 20
  Toronto players checked) and its own response `timestamp`.

Depth chart  GET core /seasons/{year}/teams/{id}/depthcharts
- Positions (pg, sg, sf, pf, c) each with players in rank order. This is
  ESPN's editorial depth chart. It is NOT an announced starting lineup.

Game lineup  GET core /events/{id}/competitions/{id}/competitors/{team}/roster
- About 33 hours before tip-off each entry held ONLY a player id: no
  starter, active or did-not-play field existed at all, and the game's
  `lineupAvailable` flag was false.
- For a finished game the same endpoint carries starter / active /
  didNotPlay / reason per entry.
- Whether, and how long before tip-off, the starter field appears has NOT
  been observed - there was no game close to tip-off to test against. This
  provider reports exactly which shape it was given (`has_starter_field`)
  so that question is answered by collected observations, not assumed.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date, datetime, timezone

import httpx

from app.providers.nba.espn import DEFAULT_REQUEST_INTERVAL_SECONDS, DEFAULT_USER_AGENT, PROVIDER_NAME, EspnNbaError, _parse_time
from app.providers.nba.espn import BASE_URL as SITE_BASE_URL
from app.providers.nba.evidence_types import NbaDepthChart, NbaGameLineup, NbaInjuryFeed, NbaInjuryItem, NbaLineupEntry, NbaRosterPlayer, NbaTeamRoster

CORE_BASE_URL = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"

_PLAYER_ID_IN_LINK = re.compile(r"/id/(\d+)(?:/|$)")
_ATHLETE_ID_IN_REF = re.compile(r"/athletes/(\d+)")
# Kept from the athlete object in the stored raw entry: enough to see who
# the source meant, without headshots and link lists.
_RAW_ATHLETE_KEYS = ("firstName", "lastName", "displayName", "shortName")


def _time_or_none(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return _parse_time(raw)
    except ValueError:
        return None


def _date_or_none(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _player_id_from_links(athlete: dict) -> str | None:
    for link in athlete.get("links") or []:
        match = _PLAYER_ID_IN_LINK.search(link.get("href") or "")
        if match:
            return match.group(1)
    return None


class EspnNbaEvidenceProvider:
    def __init__(
        self,
        site_client: httpx.Client | None = None,
        core_client: httpx.Client | None = None,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        request_interval_seconds: float = DEFAULT_REQUEST_INTERVAL_SECONDS,
        timeout_seconds: float = 30.0,
    ):
        headers = {"User-Agent": user_agent}
        self._site = site_client or httpx.Client(base_url=SITE_BASE_URL, timeout=timeout_seconds, headers=headers)
        self._core = core_client or httpx.Client(base_url=CORE_BASE_URL, timeout=timeout_seconds, headers=headers)
        self._interval = request_interval_seconds
        self._last_request_at: float | None = None

    def _get(self, client: httpx.Client, path: str, params: dict | None = None) -> tuple[dict, bytes]:
        if self._last_request_at is not None and self._interval > 0:
            wait = self._interval - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
        try:
            response = client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise EspnNbaError(f"request to {path} failed: {exc}") from exc
        finally:
            self._last_request_at = time.monotonic()
        if response.status_code != 200:
            raise EspnNbaError(f"{path} returned HTTP {response.status_code}: {response.text[:200]}")
        try:
            return response.json(), response.content
        except ValueError as exc:
            raise EspnNbaError(f"{path} did not return JSON") from exc

    # --- injuries -----------------------------------------------------------

    def get_injuries(self) -> NbaInjuryFeed:
        body, _ = self._get(self._site, "/injuries")
        fetched_at = datetime.now(timezone.utc)
        if body.get("status") not in (None, "success") or not isinstance(body.get("injuries"), list):
            raise EspnNbaError(f"/injuries did not report success (status={body.get('status')!r})")
        items: list[NbaInjuryItem] = []
        unresolvable = 0
        try:
            for block in body["injuries"]:
                team_id = str(block["id"])
                for entry in block.get("injuries") or []:
                    item = self._injury_item(entry, team_id)
                    if item is None:
                        unresolvable += 1
                    else:
                        items.append(item)
        except (KeyError, TypeError) as exc:
            raise EspnNbaError(f"/injuries response was not in the expected shape: {exc!r}") from exc
        # Hashed over the entries only: the feed's own timestamp changes on
        # every request and would make every poll look different.
        digest = hashlib.sha256(json.dumps([item.raw for item in items], sort_keys=True).encode("utf-8")).hexdigest()
        return NbaInjuryFeed(
            source=PROVIDER_NAME, fetched_at=fetched_at, source_timestamp=_time_or_none(body.get("timestamp")), payload_sha256=digest,
            items=items, teams_listed=len(body["injuries"]), unresolvable_items=unresolvable,
        )

    @staticmethod
    def _injury_item(entry: dict, team_id: str) -> NbaInjuryItem | None:
        athlete = entry.get("athlete") or {}
        player_id = _player_id_from_links(athlete)
        status = entry.get("status")
        if player_id is None or not athlete.get("displayName") or not status:
            return None
        details = entry.get("details") or {}
        athlete_team_id = (athlete.get("team") or {}).get("id")
        raw = {key: value for key, value in entry.items() if key != "athlete"}
        raw["athlete"] = {
            **{key: athlete.get(key) for key in _RAW_ATHLETE_KEYS},
            "id_from_link": player_id,
            "position": (athlete.get("position") or {}).get("abbreviation"),
            "team_id": athlete_team_id,
        }
        raw["listed_under_team_id"] = team_id
        return NbaInjuryItem(
            source_player_id=player_id,
            player_name=athlete["displayName"],
            position=(athlete.get("position") or {}).get("abbreviation"),
            source_team_id=team_id,
            source_athlete_team_id=str(athlete_team_id) if athlete_team_id is not None else None,
            source_report_id=str(entry["id"]) if entry.get("id") is not None else None,
            source_status=status,
            source_status_type=(entry.get("type") or {}).get("name"),
            source_published_at=_time_or_none(entry.get("date")),
            injury_type=details.get("type"),
            injury_location=details.get("location"),
            injury_side=details.get("side"),
            injury_detail=details.get("detail"),
            fantasy_status=(details.get("fantasyStatus") or {}).get("abbreviation"),
            expected_return_date=_date_or_none(details.get("returnDate")),
            short_comment=entry.get("shortComment"),
            long_comment=entry.get("longComment"),
            raw=raw,
        )

    # --- team roster and depth chart ---------------------------------------

    def get_team_roster(self, source_team_id: str) -> NbaTeamRoster:
        body, _ = self._get(self._site, f"/teams/{source_team_id}/roster")
        fetched_at = datetime.now(timezone.utc)
        try:
            players = [
                NbaRosterPlayer(
                    source_player_id=str(athlete["id"]),
                    name=athlete["displayName"],
                    position=(athlete.get("position") or {}).get("abbreviation"),
                    jersey=athlete.get("jersey"),
                    status=(athlete.get("status") or {}).get("name"),
                )
                for athlete in body["athletes"]
            ]
        except (KeyError, TypeError) as exc:
            raise EspnNbaError(f"roster response for team {source_team_id} was not in the expected shape: {exc!r}") from exc
        return NbaTeamRoster(
            source=PROVIDER_NAME, source_team_id=str(source_team_id), fetched_at=fetched_at, source_timestamp=_time_or_none(body.get("timestamp")), players=players
        )

    def get_team_depth_chart(self, source_team_id: str, season_start_year: int) -> NbaDepthChart:
        # ESPN names a season by the year it ends.
        body, _ = self._get(self._core, f"/seasons/{season_start_year + 1}/teams/{source_team_id}/depthcharts", {"lang": "en"})
        fetched_at = datetime.now(timezone.utc)
        positions: dict[str, list[str]] = {}
        try:
            for chart in body.get("items") or []:
                for key, position in (chart.get("positions") or {}).items():
                    ranked = sorted(position.get("athletes") or [], key=lambda a: a.get("rank") or 0)
                    ids = [m.group(1) for a in ranked if (m := _ATHLETE_ID_IN_REF.search((a.get("athlete") or {}).get("$ref") or ""))]
                    positions[key] = ids
        except (AttributeError, TypeError) as exc:
            raise EspnNbaError(f"depth chart response for team {source_team_id} was not in the expected shape: {exc!r}") from exc
        return NbaDepthChart(source=PROVIDER_NAME, source_team_id=str(source_team_id), fetched_at=fetched_at, positions=positions)

    # --- game lineup --------------------------------------------------------

    def get_game_lineup(self, source_game_id: str, source_team_id: str) -> NbaGameLineup:
        return self.get_game_lineups(source_game_id, [source_team_id])[0]

    def get_game_lineups(self, source_game_id: str, source_team_ids: list[str]) -> list[NbaGameLineup]:
        """Both teams' lineups for one game, sharing one request for the
        game's own flags."""
        prefix = f"/events/{source_game_id}/competitions/{source_game_id}"
        competition, _ = self._get(self._core, prefix, {"lang": "en"})
        return [self._team_lineup(prefix, competition, source_game_id, team_id) for team_id in source_team_ids]

    def _team_lineup(self, prefix: str, competition: dict, source_game_id: str, source_team_id: str) -> NbaGameLineup:
        roster, _ = self._get(self._core, f"{prefix}/competitors/{source_team_id}/roster", {"lang": "en"})
        fetched_at = datetime.now(timezone.utc)
        entries: list[NbaLineupEntry] = []
        has_starter_field = False
        try:
            for entry in roster.get("entries") or []:
                if entry.get("playerId") is None:
                    continue
                has_starter_field = has_starter_field or "starter" in entry
                entries.append(
                    NbaLineupEntry(
                        source_player_id=str(entry["playerId"]),
                        starter=entry.get("starter"),
                        active=entry.get("active"),
                        did_not_play=entry.get("didNotPlay"),
                        reason=entry.get("reason"),
                    )
                )
        except (AttributeError, TypeError) as exc:
            raise EspnNbaError(f"lineup response for game {source_game_id} team {source_team_id} was not in the expected shape: {exc!r}") from exc
        return NbaGameLineup(
            source=PROVIDER_NAME, source_game_id=str(source_game_id), source_team_id=str(source_team_id), fetched_at=fetched_at,
            lineup_available=competition.get("lineupAvailable"), has_starter_field=has_starter_field, entries=entries,
        )
