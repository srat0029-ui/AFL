"""Tests for SquiggleFixtureProvider using a fake transport function.

No real curl subprocess calls or network access here — a fake transport lets
tests be fast, deterministic, and independent of Squiggle's actual uptime.
"""

import json

import pytest

from app.providers.afl.squiggle import SquiggleFixtureProvider

SAMPLE_GAME_COMPLETE = {
    "id": 35704,
    "hscore": 86,
    "complete": 100,
    "unixtime": 1710405000,
    "ateamid": 14,
    "hbehinds": 14,
    "ateam": "Richmond",
    "venue": "M.C.G.",
    "agoals": 12,
    "roundname": "Round 1",
    "hteamid": 3,
    "abehinds": 9,
    "winner": "Carlton",
    "hteam": "Carlton",
    "hgoals": 12,
    "round": 1,
    "year": 2024,
    "ascore": 81,
}

SAMPLE_GAME_SCHEDULED = {
    "id": 99001,
    "hscore": 0,
    "complete": 0,
    "unixtime": 1999999999,
    "ateam": "Sydney",
    "venue": "S.C.G.",
    "roundname": "Round 5",
    "hteam": "Geelong",
    "round": 5,
    "year": 2026,
    "ascore": 0,
}

SAMPLE_GAME_MALFORMED = {"id": 1, "round": 1, "year": 2024}  # missing hteam/ateam/unixtime


def _json_response(payload: dict) -> tuple[int, str, str]:
    return 200, "application/json; charset=UTF-8", json.dumps(payload)


def _html_response() -> tuple[int, str, str]:
    return 200, "text/html; charset=UTF-8", "<html><body>Squiggle API</body></html>"


def _make_provider(transport) -> SquiggleFixtureProvider:
    return SquiggleFixtureProvider(transport=transport, request_delay_seconds=0)


def test_get_fixtures_parses_completed_game():
    def transport(url: str) -> tuple[int, str, str]:
        assert "games;year=2024" in url
        return _json_response({"games": [SAMPLE_GAME_COMPLETE]})

    provider = _make_provider(transport)
    fixtures = provider.get_fixtures("AFL", 2024)

    assert len(fixtures) == 1
    fixture = fixtures[0]
    assert fixture.external_id == "35704"
    assert fixture.home_team == "Carlton"
    assert fixture.away_team == "Richmond"
    assert fixture.status == "completed"
    assert fixture.home_score == 86
    assert fixture.away_score == 81
    assert fixture.round_number == 1
    assert fixture.round_name == "Round 1"
    assert fixture.venue_name == "M.C.G."
    assert fixture.scheduled_start.year == 2024


def test_get_fixtures_scheduled_game_has_no_score():
    provider = _make_provider(lambda url: _json_response({"games": [SAMPLE_GAME_SCHEDULED]}))
    fixtures = provider.get_fixtures("AFL", 2026)

    assert fixtures[0].status == "scheduled"
    assert fixtures[0].home_score is None
    assert fixtures[0].away_score is None


def test_malformed_game_is_skipped_not_raised():
    provider = _make_provider(
        lambda url: _json_response({"games": [SAMPLE_GAME_MALFORMED, SAMPLE_GAME_COMPLETE]})
    )
    fixtures = provider.get_fixtures("AFL", 2024)

    assert len(fixtures) == 1
    assert fixtures[0].external_id == "35704"


def test_get_upcoming_fixtures_queries_complete_zero():
    captured = {}

    def transport(url: str) -> tuple[int, str, str]:
        captured["url"] = url
        return _json_response({"games": []})

    provider = _make_provider(transport)
    provider.get_upcoming_fixtures("AFL")

    assert "complete=0" in captured["url"]


def test_rejects_non_afl_sport():
    provider = _make_provider(lambda url: _json_response({"games": []}))
    with pytest.raises(ValueError, match="only supports AFL"):
        provider.get_fixtures("NRL", 2024)


def test_raises_on_http_error(monkeypatch):
    monkeypatch.setattr("app.providers.afl.squiggle.time.sleep", lambda _: None)
    provider = _make_provider(lambda url: (500, "text/html", ""))
    with pytest.raises(ValueError, match="HTTP 500"):
        provider.get_fixtures("AFL", 2024)


def test_retries_past_transient_html_response(monkeypatch):
    """Squiggle occasionally serves its HTML homepage instead of JSON; the
    provider should retry rather than fail immediately."""
    monkeypatch.setattr("app.providers.afl.squiggle.time.sleep", lambda _: None)
    calls = {"n": 0}

    def transport(url: str) -> tuple[int, str, str]:
        calls["n"] += 1
        if calls["n"] < 2:
            return _html_response()
        return _json_response({"games": [SAMPLE_GAME_COMPLETE]})

    provider = _make_provider(transport)
    fixtures = provider.get_fixtures("AFL", 2024)

    assert calls["n"] == 2
    assert len(fixtures) == 1


def test_raises_after_repeated_html_responses(monkeypatch):
    monkeypatch.setattr("app.providers.afl.squiggle.time.sleep", lambda _: None)
    provider = _make_provider(lambda url: _html_response())
    with pytest.raises(ValueError, match="non-JSON"):
        provider.get_fixtures("AFL", 2024)
