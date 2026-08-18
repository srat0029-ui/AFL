"""Provider response parsing tests for TheOddsApiProvider — Section 28's
"provider response parsing" and "API quota protection" (quota header
parsing) requirements. All HTTP calls are mocked (httpx.MockTransport) -
never a real network call in an automated test."""

from datetime import datetime, timezone

import httpx
import pytest

from app.providers.afl.the_odds_api import AFL_SPORT_KEY, TheOddsApiError, TheOddsApiProvider
from app.providers.player_prop_odds import QuotaStatus


def _client_with_handler(handler) -> httpx.Client:
    transport = httpx.MockTransport(handler)
    return httpx.Client(base_url="https://api.the-odds-api.com/v4", transport=transport)


def test_is_available_false_without_key():
    provider = TheOddsApiProvider(api_key="")
    assert provider.is_available is False


def test_is_available_true_with_key():
    provider = TheOddsApiProvider(api_key="abc123")
    assert provider.is_available is True


def test_list_events_without_key_raises():
    provider = TheOddsApiProvider(api_key="")
    with pytest.raises(TheOddsApiError):
        provider.list_events("AFL")


def test_list_events_rejects_non_afl_sport():
    provider = TheOddsApiProvider(api_key="key", client=_client_with_handler(lambda r: httpx.Response(200, json=[])))
    with pytest.raises(ValueError):
        provider.list_events("NRL")


def test_list_events_parses_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v4/sports/{AFL_SPORT_KEY}/events"
        assert request.url.params["apiKey"] == "key"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "evt1", "sport_key": AFL_SPORT_KEY, "sport_title": "AFL",
                    "commence_time": "2026-08-20T09:30:00Z", "home_team": "Collingwood", "away_team": "Carlton",
                }
            ],
        )

    provider = TheOddsApiProvider(api_key="key", client=_client_with_handler(handler))
    events = provider.list_events("AFL")
    assert len(events) == 1
    assert events[0].event_id == "evt1"
    assert events[0].home_team == "Collingwood"
    assert events[0].commence_time == datetime(2026, 8, 20, 9, 30, tzinfo=timezone.utc)


def test_list_events_raises_on_non_200():
    provider = TheOddsApiProvider(api_key="key", client=_client_with_handler(lambda r: httpx.Response(401, text="unauthorized")))
    with pytest.raises(TheOddsApiError):
        provider.list_events("AFL")


def _sample_event():
    from app.providers.types import ProviderEvent

    return ProviderEvent(
        provider="the_odds_api", event_id="evt1", sport_key=AFL_SPORT_KEY,
        home_team="Collingwood", away_team="Carlton", commence_time=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )


def test_get_player_prop_quotes_parses_outcomes_and_uses_description_as_player_name():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v4/sports/{AFL_SPORT_KEY}/events/evt1/odds"
        assert request.url.params["oddsFormat"] == "decimal"
        body = {
            "id": "evt1", "sport_key": AFL_SPORT_KEY, "commence_time": "2026-08-20T09:30:00Z",
            "home_team": "Collingwood", "away_team": "Carlton",
            "bookmakers": [
                {
                    "key": "sportsbet", "title": "Sportsbet", "last_update": "2026-08-16T10:00:00Z",
                    "markets": [
                        {
                            "key": "player_disposals", "last_update": "2026-08-16T10:00:00Z",
                            "outcomes": [
                                {"name": "Over", "description": "Nick Daicos", "price": 1.9, "point": 29.5},
                                {"name": "Under", "description": "Nick Daicos", "price": 1.9, "point": 29.5},
                            ],
                        }
                    ],
                }
            ],
        }
        return httpx.Response(200, json=body, headers={"x-requests-used": "10", "x-requests-remaining": "490", "x-requests-last": "1"})

    provider = TheOddsApiProvider(api_key="key", client=_client_with_handler(handler))
    result = provider.get_player_prop_quotes("AFL", _sample_event(), ["player_disposals"])

    assert len(result.quotes) == 2
    names = {q.player_name for q in result.quotes}
    assert names == {"Nick Daicos"}
    selections = {q.selection for q in result.quotes}
    assert selections == {"Over", "Under"}
    assert result.markets_returned == ["player_disposals"]
    assert result.quota == QuotaStatus(requests_used=10, requests_remaining=490, last_request_cost=1)


def test_get_player_prop_quotes_skips_outcome_with_no_description():
    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "id": "evt1", "sport_key": AFL_SPORT_KEY, "commence_time": "2026-08-20T09:30:00Z",
            "home_team": "Collingwood", "away_team": "Carlton",
            "bookmakers": [
                {
                    "key": "sportsbet", "title": "Sportsbet", "last_update": "2026-08-16T10:00:00Z",
                    "markets": [{"key": "player_disposals", "last_update": "2026-08-16T10:00:00Z", "outcomes": [{"name": "Over", "price": 1.9, "point": 29.5}]}],
                }
            ],
        }
        return httpx.Response(200, json=body)

    provider = TheOddsApiProvider(api_key="key", client=_client_with_handler(handler))
    result = provider.get_player_prop_quotes("AFL", _sample_event(), ["player_disposals"])
    assert result.quotes == []


def test_get_player_prop_quotes_empty_market_keys_returns_empty_without_request():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    provider = TheOddsApiProvider(api_key="key", client=_client_with_handler(handler))
    result = provider.get_player_prop_quotes("AFL", _sample_event(), [])
    assert result.quotes == []
    assert calls == []


def test_get_player_prop_quotes_raises_on_error_status():
    provider = TheOddsApiProvider(api_key="key", client=_client_with_handler(lambda r: httpx.Response(500, text="server error")))
    with pytest.raises(TheOddsApiError):
        provider.get_player_prop_quotes("AFL", _sample_event(), ["player_disposals"])


def test_get_standard_match_odds_parses_h2h_spreads_totals():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v4/sports/{AFL_SPORT_KEY}/odds"
        assert request.url.params["regions"] == "au"
        assert "h2h" in request.url.params["markets"]
        body = [
            {
                "id": "evt1", "sport_key": AFL_SPORT_KEY, "commence_time": "2026-08-20T09:30:00Z",
                "home_team": "Collingwood", "away_team": "Carlton",
                "bookmakers": [
                    {
                        "key": "sportsbet", "title": "Sportsbet", "last_update": "2026-08-16T10:00:00Z",
                        "markets": [
                            {
                                "key": "h2h", "last_update": "2026-08-16T10:00:00Z",
                                "outcomes": [{"name": "Collingwood", "price": 1.85}, {"name": "Carlton", "price": 2.05}],
                            },
                            {
                                "key": "spreads", "last_update": "2026-08-16T10:00:00Z",
                                "outcomes": [{"name": "Collingwood", "price": 1.9, "point": -12.5}, {"name": "Carlton", "price": 1.9, "point": 12.5}],
                            },
                            {
                                "key": "totals", "last_update": "2026-08-16T10:00:00Z",
                                "outcomes": [{"name": "Over", "price": 1.9, "point": 165.5}, {"name": "Under", "price": 1.9, "point": 165.5}],
                            },
                        ],
                    }
                ],
            }
        ]
        return httpx.Response(200, json=body, headers={"x-requests-used": "5", "x-requests-remaining": "495", "x-requests-last": "3"})

    provider = TheOddsApiProvider(api_key="key", client=_client_with_handler(handler))
    result = provider.get_standard_match_odds("AFL")

    assert len(result.events) == 1
    assert result.events[0].home_team == "Collingwood"
    assert len(result.quotes) == 6
    market_keys = {q.market_key for q in result.quotes}
    assert market_keys == {"h2h", "spreads", "totals"}
    h2h_quote = next(q for q in result.quotes if q.market_key == "h2h" and q.selection == "Collingwood")
    assert h2h_quote.price_decimal == 1.85
    assert h2h_quote.line_value is None
    spread_quote = next(q for q in result.quotes if q.market_key == "spreads" and q.selection == "Collingwood")
    assert spread_quote.line_value == -12.5
    assert result.markets_returned == ["h2h", "spreads", "totals"]
    assert result.quota == QuotaStatus(requests_used=5, requests_remaining=495, last_request_cost=3)


def test_get_standard_match_odds_without_key_raises():
    provider = TheOddsApiProvider(api_key="")
    with pytest.raises(TheOddsApiError):
        provider.get_standard_match_odds("AFL")


def test_get_standard_match_odds_rejects_non_afl():
    provider = TheOddsApiProvider(api_key="key", client=_client_with_handler(lambda r: httpx.Response(200, json=[])))
    with pytest.raises(ValueError):
        provider.get_standard_match_odds("NRL")


def test_get_standard_match_odds_raises_on_error_status():
    provider = TheOddsApiProvider(api_key="key", client=_client_with_handler(lambda r: httpx.Response(500, text="server error")))
    with pytest.raises(TheOddsApiError):
        provider.get_standard_match_odds("AFL")


def test_quota_headers_missing_are_none_not_zero():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "evt1", "sport_key": AFL_SPORT_KEY, "commence_time": "2026-08-20T09:30:00Z", "home_team": "Collingwood", "away_team": "Carlton", "bookmakers": []})

    provider = TheOddsApiProvider(api_key="key", client=_client_with_handler(handler))
    result = provider.get_player_prop_quotes("AFL", _sample_event(), ["player_disposals"])
    assert result.quota.requests_used is None
    assert result.quota.requests_remaining is None
