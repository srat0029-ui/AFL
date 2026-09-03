"""Focused tests for scripts/seed_production.py's safety guards - this
script moves real historical data into a real production database, so its
pre-flight checks (dialect, distinctness, Postgres major version, Alembic
head, emptiness) and its match-status filter are tested directly rather
than only exercised via a manual dry run.
"""

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import seed_production as sp  # noqa: E402

from app.models import Match, MatchStatus, Round, Season, Sport, Team, Venue  # noqa: E402


class _FakeUrl:
    def __init__(self, backend_name: str, url_str: str):
        self._backend_name = backend_name
        self._url_str = url_str

    def get_backend_name(self) -> str:
        return self._backend_name

    def render_as_string(self, hide_password: bool = True) -> str:
        return self._url_str


class _FakeEngine:
    """Minimal stand-in for a SQLAlchemy Engine: supports .url and the
    connect()/execute()/scalar() chain the guard functions use, returning a
    canned scalar value regardless of the statement executed."""

    def __init__(self, backend_name: str, scalar_value=None, url_str: str = "fake://"):
        self.url = _FakeUrl(backend_name, url_str)
        self._scalar_value = scalar_value

    def connect(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, _stmt):
        return self

    def scalar(self):
        return self._scalar_value


def _fake_engine_with_url(backend_name: str, scalar_value=None, url_str: str = "fake://"):
    return _FakeEngine(backend_name, scalar_value, url_str)


def test_seed_and_protected_model_lists_are_the_approved_size():
    # Regression guard: catches an entry silently dropped or duplicated.
    assert len(sp.SEED_MODELS) == 17
    assert len(sp.PROTECTED_MODELS) == 18
    assert len(set(sp.SEED_MODELS)) == 17
    assert len(set(sp.PROTECTED_MODELS)) == 18


def test_check_source_is_sqlite_accepts_sqlite():
    engine = create_engine("sqlite://")
    sp.check_source_is_sqlite(engine)  # must not raise


def test_check_source_is_sqlite_rejects_non_sqlite():
    engine = _fake_engine_with_url("postgresql")
    with pytest.raises(sp.SeedGuardError, match="must be a SQLite database"):
        sp.check_source_is_sqlite(engine)


def test_check_target_is_postgres_rejects_sqlite():
    engine = create_engine("sqlite://")
    with pytest.raises(sp.SeedGuardError, match="must be PostgreSQL"):
        sp.check_target_is_postgres(engine)


def test_check_target_is_postgres_accepts_postgres_backend_name():
    engine = _fake_engine_with_url("postgresql")
    sp.check_target_is_postgres(engine)  # must not raise


def test_check_distinct_databases_rejects_identical_urls():
    a = _fake_engine_with_url("sqlite", url_str="sqlite:///same.db")
    b = _fake_engine_with_url("sqlite", url_str="sqlite:///same.db")
    with pytest.raises(sp.SeedGuardError, match="same database"):
        sp.check_distinct_databases(a, b)


def test_check_distinct_databases_accepts_different_urls():
    a = _fake_engine_with_url("sqlite", url_str="sqlite:///one.db")
    b = _fake_engine_with_url("postgresql", url_str="postgresql://host/afl")
    sp.check_distinct_databases(a, b)  # must not raise


def test_check_target_postgres_major_accepts_18():
    engine = _fake_engine_with_url("postgresql", scalar_value="PostgreSQL 18.6 on x86_64-pc-linux-musl")
    sp.check_target_postgres_major(engine)  # must not raise


@pytest.mark.parametrize("version_string", ["PostgreSQL 16.4 on x86_64", "PostgreSQL 19.0.0-beta.3 on x86_64"])
def test_check_target_postgres_major_rejects_wrong_version(version_string):
    engine = _fake_engine_with_url("postgresql", scalar_value=version_string)
    with pytest.raises(sp.SeedGuardError, match="major version must be 18"):
        sp.check_target_postgres_major(engine)


def test_check_target_alembic_head_accepts_matching_head():
    engine = _fake_engine_with_url("postgresql", scalar_value="d633f77191dc")
    sp.check_target_alembic_head(engine, "d633f77191dc")  # must not raise


def test_check_target_alembic_head_rejects_mismatched_head():
    engine = _fake_engine_with_url("postgresql", scalar_value="1817ff671b79")
    with pytest.raises(sp.SeedGuardError, match="must be the current head"):
        sp.check_target_alembic_head(engine, "d633f77191dc")


def test_real_migration_head_matches_known_tip():
    # If this ever fails after a genuine new migration is added, that's
    # expected - update the expected value, don't relax the guard.
    assert sp._real_migration_head() == "d633f77191dc"


def test_check_tables_empty_passes_on_empty_db(db_session):
    sp.check_tables_empty(db_session, [Sport, Team], "seed")  # must not raise


def test_check_tables_empty_raises_on_nonempty_table(db_session):
    db_session.add(Sport(code="AFL", name="Australian Football League"))
    db_session.commit()
    with pytest.raises(sp.SeedGuardError, match="sports.*not empty"):
        sp.check_tables_empty(db_session, [Sport], "seed")


def test_match_filter_selects_completed_only(db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season = Season(sport_id=sport.id, year=2026)
    db_session.add(season)
    db_session.flush()
    round_ = Round(season_id=season.id, round_number=1)
    db_session.add(round_)
    venue = Venue(name="Test Oval")
    home = Team(sport_id=sport.id, name="Home Team", short_name="HOM")
    away = Team(sport_id=sport.id, name="Away Team", short_name="AWY")
    db_session.add_all([venue, home, away])
    db_session.flush()

    from datetime import datetime, timezone

    completed = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id,
        home_team_id=home.id, away_team_id=away.id, venue_id=venue.id,
        scheduled_start=datetime(2026, 3, 1, tzinfo=timezone.utc), status=MatchStatus.COMPLETED,
    )
    scheduled = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id,
        home_team_id=away.id, away_team_id=home.id, venue_id=venue.id,
        scheduled_start=datetime(2026, 9, 1, tzinfo=timezone.utc), status=MatchStatus.SCHEDULED,
    )
    db_session.add_all([completed, scheduled])
    db_session.commit()

    rows = db_session.scalars(sp._filtered_select(Match)).all()
    assert [r.id for r in rows] == [completed.id]
