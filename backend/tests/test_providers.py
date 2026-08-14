from datetime import datetime, timezone

import pytest

from app.providers import Fixture, FixtureProvider, OddsProvider, OddsQuote, StatsProvider
from app.providers.types import PlayerStatLine, TeamStatLine


def test_fixture_provider_is_abstract():
    with pytest.raises(TypeError):
        FixtureProvider()


def test_stats_provider_is_abstract():
    with pytest.raises(TypeError):
        StatsProvider()


def test_odds_provider_is_abstract():
    with pytest.raises(TypeError):
        OddsProvider()


class InMemoryFixtureProvider(FixtureProvider):
    """A minimal concrete implementation, used only to prove the interface is usable."""

    def __init__(self, fixtures: list[Fixture]):
        self._fixtures = fixtures

    def get_fixtures(self, sport_code: str, season_year: int) -> list[Fixture]:
        return [
            f for f in self._fixtures if f.sport_code == sport_code and f.season_year == season_year
        ]

    def get_upcoming_fixtures(self, sport_code: str) -> list[Fixture]:
        return [f for f in self._fixtures if f.sport_code == sport_code and f.status == "scheduled"]


def test_concrete_fixture_provider_satisfies_contract():
    fixture = Fixture(
        external_id="1",
        sport_code="AFL",
        season_year=2025,
        round_number=1,
        home_team="Collingwood",
        away_team="Carlton",
        scheduled_start=datetime(2025, 3, 21, 19, 20, tzinfo=timezone.utc),
        status="scheduled",
    )
    provider = InMemoryFixtureProvider([fixture])

    assert provider.get_fixtures("AFL", 2025) == [fixture]
    assert provider.get_fixtures("AFL", 2024) == []
    assert provider.get_upcoming_fixtures("AFL") == [fixture]


def test_stat_line_dataclasses_hold_open_ended_stats():
    team_line = TeamStatLine(
        match_external_id="1",
        sport_code="AFL",
        team_name="Collingwood",
        recorded_at=datetime.now(timezone.utc),
        stats={"disposals": 412, "clearances": 38},
    )
    assert team_line.stats["disposals"] == 412

    player_line = PlayerStatLine(
        match_external_id="1",
        sport_code="AFL",
        team_name="Collingwood",
        player_name="Nick Daicos",
        recorded_at=datetime.now(timezone.utc),
        stats={"disposals": 34},
    )
    assert player_line.stats["disposals"] == 34


def test_odds_quote_defaults():
    quote = OddsQuote(
        match_external_id="1",
        sport_code="AFL",
        bookmaker="Sportsbet",
        market_type="h2h",
        selection="Collingwood",
        price_decimal=1.85,
        recorded_at=datetime.now(timezone.utc),
    )
    assert quote.source == "manual"
    assert quote.is_closing_line is False
