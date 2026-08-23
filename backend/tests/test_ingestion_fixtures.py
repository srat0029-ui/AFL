from datetime import datetime, timezone

from sqlalchemy import select

from app.ingestion.fixtures import ingest_fixtures
from app.models import Match, MatchStatus, Round, Season, Sport, Team, Venue
from app.providers.types import Fixture


def _fixture(**overrides) -> Fixture:
    defaults = dict(
        external_id="1",
        sport_code="AFL",
        season_year=2024,
        round_number=1,
        round_name="Round 1",
        home_team="Carlton",
        away_team="Richmond",
        venue_name="M.C.G.",
        scheduled_start=datetime(2024, 3, 14, 19, 30, tzinfo=timezone.utc),
        status="scheduled",
    )
    defaults.update(overrides)
    return Fixture(**defaults)


def test_ingest_creates_full_graph(db_session):
    result = ingest_fixtures(db_session, [_fixture()])

    assert result.matches_created == 1
    assert result.teams_created == 2
    assert result.venues_created == 1
    assert result.seasons_created == 1
    assert result.rounds_created == 1

    match = db_session.scalar(select(Match))
    assert match.home_team.name == "Carlton"
    assert match.home_team.short_name == "CAR"
    assert match.home_team.primary_colour == "#0E1E2D"
    assert match.away_team.name == "Richmond"
    assert match.venue.name == "M.C.G."
    assert match.round.round_number == 1
    assert match.season.year == 2024
    assert match.external_ids == {"squiggle": "1"}


def test_ingest_is_idempotent_on_rerun(db_session):
    ingest_fixtures(db_session, [_fixture()])
    result = ingest_fixtures(db_session, [_fixture()])

    assert result.matches_created == 0
    assert result.matches_updated == 0
    assert result.matches_unchanged == 1
    assert len(db_session.scalars(select(Match)).all()) == 1


def test_ingest_updates_existing_match_on_result_change(db_session):
    ingest_fixtures(db_session, [_fixture(status="scheduled")])

    completed = _fixture(status="completed", home_score=86, away_score=81)
    result = ingest_fixtures(db_session, [completed])

    assert result.matches_created == 0
    assert result.matches_updated == 1

    match = db_session.scalar(select(Match))
    assert match.status.value == "completed"
    assert match.home_score == 86
    assert match.away_score == 81
    assert len(db_session.scalars(select(Match)).all()) == 1


def test_ingest_transitions_in_progress_to_completed(db_session):
    """Regression: a match that went live must still be able to reach
    COMPLETED on a later fixture refresh — the actual bug was upstream in
    SquiggleFixtureProvider (see test_squiggle_provider.py), which stopped
    returning a match at all once it left "scheduled", so ingest_fixtures
    itself never got the chance to run this transition. This locks in that
    ingest_fixtures has always applied it correctly once given the data."""
    ingest_fixtures(db_session, [_fixture(status="scheduled")])
    match = db_session.scalar(select(Match))
    assert match.status == MatchStatus.SCHEDULED

    ingest_fixtures(db_session, [_fixture(status="in_progress", home_score=40, away_score=30)])
    db_session.refresh(match)
    assert match.status == MatchStatus.IN_PROGRESS
    assert match.home_score == 40

    result = ingest_fixtures(
        db_session,
        [
            _fixture(
                status="completed", home_score=86, away_score=81,
                home_score_breakdown={"goals": 12, "behinds": 14}, away_score_breakdown={"goals": 12, "behinds": 9},
            )
        ],
    )
    db_session.refresh(match)
    assert result.matches_updated == 1
    assert match.status == MatchStatus.COMPLETED
    assert match.home_score == 86
    assert match.away_score == 81


def test_ingest_stores_goals_and_behinds_breakdown(db_session):
    completed = _fixture(
        status="completed",
        home_score=86,
        away_score=81,
        home_score_breakdown={"goals": 12, "behinds": 14},
        away_score_breakdown={"goals": 12, "behinds": 9},
    )
    ingest_fixtures(db_session, [completed])

    match = db_session.scalar(select(Match))
    assert match.home_goals == 12
    assert match.home_behinds == 14
    assert match.away_goals == 12
    assert match.away_behinds == 9
    assert 6 * match.home_goals + match.home_behinds == match.home_score


def test_ingest_updates_goals_behinds_when_result_arrives(db_session):
    ingest_fixtures(db_session, [_fixture(status="scheduled")])
    assert db_session.scalar(select(Match)).home_goals is None

    completed = _fixture(
        status="completed",
        home_score=86,
        away_score=81,
        home_score_breakdown={"goals": 12, "behinds": 14},
        away_score_breakdown={"goals": 12, "behinds": 9},
    )
    result = ingest_fixtures(db_session, [completed])

    assert result.matches_updated == 1
    match = db_session.scalar(select(Match))
    assert match.home_goals == 12
    assert match.away_behinds == 9


def test_teams_and_venues_reused_across_fixtures(db_session):
    fixtures = [
        _fixture(external_id="1", round_number=1),
        _fixture(external_id="2", round_number=2, home_team="Richmond", away_team="Carlton"),
    ]
    result = ingest_fixtures(db_session, fixtures)

    assert result.matches_created == 2
    assert result.teams_created == 2  # not 4 — Carlton/Richmond reused across both games
    assert result.venues_created == 1
    assert len(db_session.scalars(select(Team)).all()) == 2
    assert len(db_session.scalars(select(Venue)).all()) == 1
    assert len(db_session.scalars(select(Round)).all()) == 2


def test_unknown_team_falls_back_gracefully(db_session):
    result = ingest_fixtures(db_session, [_fixture(home_team="Fitzroy", away_team="University")])

    assert result.matches_created == 1
    team = db_session.scalar(select(Team).where(Team.name == "Fitzroy"))
    assert team.short_name == "FIT"
    assert team.primary_colour is None


def test_single_sport_and_season_row_created_once(db_session):
    fixtures = [_fixture(external_id=str(i), round_number=i) for i in range(1, 4)]
    ingest_fixtures(db_session, fixtures)

    assert len(db_session.scalars(select(Sport)).all()) == 1
    assert len(db_session.scalars(select(Season)).all()) == 1


def test_team_external_ids_populated_from_fixture(db_session):
    ingest_fixtures(db_session, [_fixture(home_team_external_id="3", away_team_external_id="14")])

    carlton = db_session.scalar(select(Team).where(Team.name == "Carlton"))
    richmond = db_session.scalar(select(Team).where(Team.name == "Richmond"))
    assert carlton.external_ids == {"squiggle": "3"}
    assert richmond.external_ids == {"squiggle": "14"}


def test_team_external_ids_backfilled_on_existing_team(db_session):
    # first sighting has no team id (e.g. an older/degraded payload) — a
    # later fixture that does carry one should still enrich the existing row
    ingest_fixtures(db_session, [_fixture(external_id="1")])
    carlton = db_session.scalar(select(Team).where(Team.name == "Carlton"))
    assert carlton.external_ids is None

    ingest_fixtures(db_session, [_fixture(external_id="2", round_number=2, home_team_external_id="3")])
    db_session.refresh(carlton)
    assert carlton.external_ids == {"squiggle": "3"}
