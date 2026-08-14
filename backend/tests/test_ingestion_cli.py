from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.ingestion.cli import backfill_seasons, ingest_upcoming, main
from app.ingestion.fixtures import ingest_fixtures
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


def _valid_fixture() -> Fixture:
    return Fixture(
        external_id="1",
        sport_code="AFL",
        season_year=2024,
        round_number=1,
        home_team="Carlton",
        away_team="Richmond",
        venue_name="M.C.G.",
        scheduled_start=datetime(2024, 3, 14, 19, 30, tzinfo=timezone.utc),
        status="completed",
        home_score=90,
        away_score=80,
    )


def test_main_validate_exits_zero_for_clean_dataset(db_session, monkeypatch):
    monkeypatch.setattr("app.ingestion.cli.SessionLocal", lambda: db_session)
    ingest_fixtures(db_session, [_valid_fixture()])

    assert main(["--validate"]) == 0


def test_main_validate_exits_nonzero_on_genuine_failure(db_session, monkeypatch, capsys):
    monkeypatch.setattr("app.ingestion.cli.SessionLocal", lambda: db_session)
    ingest_fixtures(db_session, [_valid_fixture()])

    # Fabricate a duplicate Squiggle match id — a genuine integrity failure
    # that ingestion itself should never produce, but the validator must
    # still catch it if it somehow occurs (e.g. a future second provider).
    match = db_session.scalar(select(Match))
    dup = Match(
        sport_id=match.sport_id,
        season_id=match.season_id,
        round_id=match.round_id,
        home_team_id=match.away_team_id,
        away_team_id=match.home_team_id,
        scheduled_start=match.scheduled_start,
        status=match.status,
        home_score=1,
        away_score=0,
        external_ids={"squiggle": "1"},
    )
    db_session.add(dup)
    db_session.commit()

    exit_code = main(["--validate"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "FAIL" in captured.out
    assert "Duplicate Squiggle match ids" in captured.out


def test_main_validate_does_not_fail_on_warnings_only(db_session, monkeypatch, capsys):
    monkeypatch.setattr("app.ingestion.cli.SessionLocal", lambda: db_session)
    # Only 2 teams (Carlton/Richmond) is a real, otherwise-clean dataset shape
    # that trips just the soft "team count outside expected range" warning.
    ingest_fixtures(db_session, [_valid_fixture()])

    exit_code = main(["--validate"])

    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert exit_code == 0
