"""FixtureProvider implementation backed by the free Squiggle API.

Squiggle (api.squiggle.com.au) is a hobby-run, free-to-use API providing AFL
fixtures, results, and ladders back to ~2000. Its usage policy asks for a
descriptive User-Agent with a contact email, and against constant polling —
this client makes one request per call (retries aside) and lets the caller
decide cadence via request_delay_seconds.

Transport note: requests are made via the `curl` binary rather than an
in-process Python HTTP client. In testing, this Cloudflare-fronted endpoint
reliably served correct JSON to curl but intermittently served its HTML
homepage instead to httpx (same headers, same User-Agent, same query) —
most likely Cloudflare's bot-management scoring Python's OpenSSL-based TLS
fingerprint differently from curl's. curl ships with Windows 10+ and
virtually every Linux/macOS install, so this doesn't cost portability.
"""

import json
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timezone

from app.config import get_settings
from app.providers.fixtures import FixtureProvider
from app.providers.types import Fixture

SQUIGGLE_BASE_URL = "https://api.squiggle.com.au/"

# (status_code, content_type, body)
Transport = Callable[[str], tuple[int, str, str]]


def curl_transport(url: str, user_agent: str, timeout: float = 15.0) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["curl", "-s", "-i", "-A", user_agent, "--max-time", str(timeout), url],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "SquiggleFixtureProvider requires the `curl` command-line tool "
            "(bundled with Windows 10+ and most Linux/macOS installs), but it "
            "wasn't found on PATH."
        ) from exc

    if result.returncode != 0:
        raise RuntimeError(f"curl failed (exit {result.returncode}): {result.stderr.strip()}")

    header_block, separator, body = result.stdout.partition("\r\n\r\n")
    if not separator:
        header_block, _, body = result.stdout.partition("\n\n")

    header_lines = header_block.splitlines()
    status_parts = header_lines[0].split() if header_lines else []
    status_code = int(status_parts[1]) if len(status_parts) > 1 and status_parts[1].isdigit() else 0

    content_type = ""
    for line in header_lines[1:]:
        if line.lower().startswith("content-type:"):
            content_type = line.split(":", 1)[1].strip()

    return status_code, content_type, body


class SquiggleFixtureProvider(FixtureProvider):
    def __init__(self, transport: Transport | None = None, request_delay_seconds: float = 0.5):
        settings = get_settings()
        self._user_agent = settings.squiggle_user_agent
        self._transport = transport or (lambda url: curl_transport(url, self._user_agent))
        self._request_delay_seconds = request_delay_seconds

    def get_fixtures(self, sport_code: str, season_year: int) -> list[Fixture]:
        self._require_afl(sport_code)
        games = self._query("games", year=season_year)
        return [f for g in games if (f := self._to_fixture(g)) is not None]

    def get_upcoming_fixtures(self, sport_code: str) -> list[Fixture]:
        self._require_afl(sport_code)
        current_year = datetime.now(timezone.utc).year
        games = self._query("games", year=current_year, complete=0)
        return [f for g in games if (f := self._to_fixture(g)) is not None]

    def _query(self, query_type: str, **params: object) -> list[dict]:
        q = ";".join([query_type] + [f"{key}={value}" for key, value in params.items()])
        url = f"{SQUIGGLE_BASE_URL}?q={q}"

        # Squiggle occasionally serves its HTML homepage instead of JSON —
        # observed directly, undocumented, and not consistently tied to any
        # one cause. It clears up within a few seconds, so retry with
        # backoff rather than failing a whole season pull over one request.
        last_error: Exception | None = None
        for attempt in range(5):
            status_code, content_type, body = self._transport(url)
            if status_code != 200:
                last_error = ValueError(f"Squiggle returned HTTP {status_code} for query {q!r}")
            elif "json" in content_type:
                if self._request_delay_seconds:
                    time.sleep(self._request_delay_seconds)
                payload = json.loads(body)
                return payload.get(query_type, [])
            else:
                last_error = ValueError(f"Squiggle returned non-JSON content-type {content_type!r} for query {q!r}")
            time.sleep(1.5 * (attempt + 1))

        raise last_error

    @staticmethod
    def _require_afl(sport_code: str) -> None:
        if sport_code != "AFL":
            raise ValueError(f"SquiggleFixtureProvider only supports AFL, got {sport_code!r}")

    @staticmethod
    def _to_fixture(game: dict) -> Fixture | None:
        # Defensive: skip malformed rows rather than let one bad game break a whole season pull.
        if not game.get("hteam") or not game.get("ateam") or not game.get("unixtime"):
            return None

        complete = game.get("complete") or 0
        if complete >= 100:
            status = "completed"
        elif complete > 0:
            status = "in_progress"
        else:
            status = "scheduled"

        home_breakdown = None
        away_breakdown = None
        if status != "scheduled" and game.get("hgoals") is not None and game.get("hbehinds") is not None:
            home_breakdown = {"goals": game["hgoals"], "behinds": game["hbehinds"]}
        if status != "scheduled" and game.get("agoals") is not None and game.get("abehinds") is not None:
            away_breakdown = {"goals": game["agoals"], "behinds": game["abehinds"]}

        home_team_id = game.get("hteamid")
        away_team_id = game.get("ateamid")

        return Fixture(
            external_id=str(game["id"]),
            sport_code="AFL",
            season_year=game["year"],
            round_number=game["round"],
            round_name=game.get("roundname"),
            home_team=game["hteam"],
            away_team=game["ateam"],
            venue_name=game.get("venue") or None,
            home_team_external_id=str(home_team_id) if home_team_id is not None else None,
            away_team_external_id=str(away_team_id) if away_team_id is not None else None,
            scheduled_start=datetime.fromtimestamp(game["unixtime"], tz=timezone.utc),
            status=status,
            home_score=game.get("hscore") if status != "scheduled" else None,
            away_score=game.get("ascore") if status != "scheduled" else None,
            home_score_breakdown=home_breakdown,
            away_score_breakdown=away_breakdown,
        )
