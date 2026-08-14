from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.ingestion.cli import backfill_seasons, ingest_upcoming, main
from app.models import Match
from app.providers.fixtures import FixtureProvider
from app.providers.types import Fixture


class FakeFixtureProvider(FixtureProvider):
    def __init__(self, by_year: dict[int, list[Fixture]], upcoming: list[Fixture] | None = None):
        self._by_year = by_year
        self._upcoming = upcoming or []

    def get_fixtures(self, sport_code: str, season_year: int) -> list[Fixture]:
        return self._by_year.get(season_year, [])

    def get_upcoming_fixtures(self, sport_code: str) -> list[Fixture]:
        return self._upcoming


def _fixture(year: int, external_id: str) -> Fixture:
    return Fixture(
        external_id=external_id,
        sport_code="AFL",
        season_year=year,
        round_number=1,
        home_team="Carlton",
        away_team="Richmond",
        scheduled_start=datetime(year, 3, 14, tzinfo=timezone.utc),
        status="completed",
        home_score=86,
        away_score=81,
    )


def test_backfill_seasons_ingests_each_year(db_session):
    provider = FakeFixtureProvider(
        {
            2023: [_fixture(2023, "1")],
            2024: [_fixture(2024, "2")],
        }
    )

    backfill_seasons(db_session, provider, 2023, 2024)

    matches = db_session.scalars(select(Match)).all()
    assert len(matches) == 2
    assert {m.season.year for m in matches} == {2023, 2024}


def test_ingest_upcoming_writes_scheduled_matches(db_session):
    upcoming_fixture = Fixture(
        external_id="99",
        sport_code="AFL",
        season_year=2026,
        round_number=1,
        home_team="Geelong",
        away_team="Sydney",
        scheduled_start=datetime(2026, 3, 20, tzinfo=timezone.utc),
        status="scheduled",
    )
    provider = FakeFixtureProvider({}, upcoming=[upcoming_fixture])

    ingest_upcoming(db_session, provider)

    match = db_session.scalar(select(Match))
    assert match.status.value == "scheduled"


def test_backfill_seasons_continues_past_a_failed_year(db_session):
    class FlakyProvider(FixtureProvider):
        def get_fixtures(self, sport_code: str, season_year: int) -> list[Fixture]:
            if season_year == 2023:
                raise ValueError("Squiggle returned non-JSON content-type")
            return [_fixture(season_year, str(season_year))]

        def get_upcoming_fixtures(self, sport_code: str) -> list[Fixture]:
            return []

    failed_years = backfill_seasons(db_session, FlakyProvider(), 2022, 2024)

    assert failed_years == [2023]
    matches = db_session.scalars(select(Match)).all()
    assert {m.season.year for m in matches} == {2022, 2024}


def test_main_requires_at_least_one_action():
    assert main([]) == 1


def test_main_rejects_reversed_season_range(capsys):
    with pytest.raises(SystemExit):
        main(["--seasons", "2025", "2020"])
