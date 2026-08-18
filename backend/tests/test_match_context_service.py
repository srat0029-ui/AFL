"""Tests for the current-context service (Current Context + Team News
Intelligence stage, Sections 1-3, 11-12): provenance, freshness, and
supersession via subject grouping."""

from datetime import datetime, timedelta, timezone

from app.models import Match, MatchStatus, Player, Round, Season, Sport, Team
from app.player_modelling.match_context_service import (
    FRESHNESS_AGING,
    FRESHNESS_FRESH,
    FRESHNESS_STALE,
    add_context_item,
    context_freshness,
    context_item_as_dict,
    current_context_for_match,
    list_context_for_match,
)

NOW = datetime.now(timezone.utc)


def _seed(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=1)
    db.add(round_)
    db.flush()
    home = Team(sport_id=sport.id, name="Collingwood", short_name="COL")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db.add_all([home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW + timedelta(days=2), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.flush()
    player = Player(sport_id=sport.id, display_name="Test Player", current_team_id=home.id, source="afltables", source_player_id="players/T/Test_Player.html")
    db.add(player)
    db.commit()
    return match, home, away, player


def test_add_context_item_records_provenance_fields(db_session):
    match, home, away, player = _seed(db_session)
    item = add_context_item(
        db_session, match_id=match.id, context_type="confirmed_out", source="Official team announcement",
        summary="Ruled out with a hamstring strain", confidence="official", team_id=home.id, player_id=player.id,
        source_timestamp=NOW - timedelta(hours=1), source_reference="https://example.com/announcement",
    )
    assert item.source == "Official team announcement"
    assert item.confidence == "official"
    assert item.source_reference == "https://example.com/announcement"
    assert item.recorded_at is not None
    d = context_item_as_dict(item)
    assert d["confidence_label"] == "Official announcement"
    assert d["context_type_label"] == "Confirmed out"


def test_freshness_thresholds():
    class _Item:
        def __init__(self, ts):
            self.source_timestamp = ts
            self.recorded_at = ts

    assert context_freshness(_Item(NOW - timedelta(hours=1)), now=NOW) == FRESHNESS_FRESH
    assert context_freshness(_Item(NOW - timedelta(hours=48)), now=NOW) == FRESHNESS_AGING
    assert context_freshness(_Item(NOW - timedelta(days=5)), now=NOW) == FRESHNESS_STALE


def test_freshness_falls_back_to_recorded_at_when_no_source_timestamp(db_session):
    match, home, away, player = _seed(db_session)
    item = add_context_item(
        db_session, match_id=match.id, context_type="other", source="Manual", summary="Note with no citation",
        confidence="unverified", source_timestamp=None,
    )
    assert context_freshness(item, now=NOW) == FRESHNESS_FRESH


def test_supersession_player_subject_latest_wins_regardless_of_type(db_session):
    """Section 12's own worked example: Monday 'limited game-time concern'
    (a TEST-style note), Thursday 'confirmed out' - the current state must
    be the later item even though the context_type differs."""
    match, home, away, player = _seed(db_session)
    add_context_item(
        db_session, match_id=match.id, context_type="limited_game_time_concern", source="Manual",
        summary="Managed minutes at training", confidence="unverified", player_id=player.id, team_id=home.id,
        source_timestamp=NOW - timedelta(days=3),
    )
    add_context_item(
        db_session, match_id=match.id, context_type="confirmed_out", source="Official team announcement",
        summary="Confirmed out", confidence="official", player_id=player.id, team_id=home.id,
        source_timestamp=NOW - timedelta(days=1),
    )
    current = current_context_for_match(db_session, match.id)
    assert len(current) == 1
    assert current[0].context_type == "confirmed_out"

    history = list_context_for_match(db_session, match.id)
    assert len(history) == 2  # nothing is deleted - both remain queryable


def test_supersession_team_wide_and_match_wide_items_grouped_separately(db_session):
    match, home, away, player = _seed(db_session)
    add_context_item(
        db_session, match_id=match.id, context_type="major_role_change", source="Manual",
        summary="Team-wide note", confidence="unverified", team_id=home.id,
    )
    add_context_item(
        db_session, match_id=match.id, context_type="weather", source="Manual",
        summary="Ground described as heavy", confidence="unverified",
    )
    current = current_context_for_match(db_session, match.id)
    # Two distinct subjects (team-wide vs match-wide) - neither supersedes the other.
    assert len(current) == 2


def test_unrelated_weather_note_does_not_supersede_venue_condition_note(db_session):
    match, home, away, player = _seed(db_session)
    add_context_item(db_session, match_id=match.id, context_type="weather", source="Manual", summary="Rain forecast", confidence="unverified")
    add_context_item(db_session, match_id=match.id, context_type="venue_condition", source="Manual", summary="Ground firm", confidence="unverified")
    current = current_context_for_match(db_session, match.id)
    assert len(current) == 2
    types = {c.context_type for c in current}
    assert types == {"weather", "venue_condition"}
