"""Tests for model-context conflict detection (Current Context + Team
News Intelligence stage, Sections 6, 13-14) — MODEL_PREDATES_CONTEXT,
substitute/confirmed-out flags, and the team-vs-player detection paths."""

from datetime import datetime, timedelta, timezone

from app.models import (
    Match,
    MatchStatus,
    Player,
    PlayerDisposalProjection,
    Round,
    Season,
    Sport,
    Team,
)
from app.player_modelling.context_model_conflict import (
    CTX_KEY_TEAM_OUT,
    CTX_MODEL_PREDATES_CONTEXT,
    CTX_PLAYER_CONFIRMED_OUT,
    CTX_PLAYER_SUBSTITUTE,
    detect_context_conflicts,
)
from app.player_modelling.match_context_service import add_context_item

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


def _make_disposal_projection(db, match, player, *, generated_at):
    proj = PlayerDisposalProjection(
        match_id=match.id, player_id=player.id, team_id=player.current_team_id,
        model_name="disposals_ridge", model_version="v1", generated_at=generated_at, data_cutoff=generated_at,
        team_model_version=None, lineup_status_at_generation="expected_in", games_of_history=20,
        predicted_mean=24.0, distribution_method="nb", nb_alpha=8.0, confidence_tier="higher_confidence",
        warnings=[], input_features={},
    )
    db.add(proj)
    db.commit()
    return proj


def test_player_confirmed_out_context_flags_code(db_session):
    match, home, away, player = _seed(db_session)
    add_context_item(db_session, match_id=match.id, context_type="confirmed_out", source="Official team announcement", summary="Out", confidence="official", player_id=player.id, team_id=home.id)
    result = detect_context_conflicts(db_session, {"match_id": match.id, "team_id": home.id, "player_id": player.id, "market_type": "player_disposals"})
    assert CTX_PLAYER_CONFIRMED_OUT in result.codes
    assert CTX_KEY_TEAM_OUT in result.codes


def test_named_substitute_context_flags_code(db_session):
    match, home, away, player = _seed(db_session)
    add_context_item(db_session, match_id=match.id, context_type="named_substitute", source="Manual", summary="Named sub", confidence="unverified", player_id=player.id, team_id=home.id)
    result = detect_context_conflicts(db_session, {"match_id": match.id, "team_id": home.id, "player_id": player.id, "market_type": "player_disposals"})
    assert CTX_PLAYER_SUBSTITUTE in result.codes


def test_player_projection_predates_newer_context(db_session):
    match, home, away, player = _seed(db_session)
    generated_at = NOW - timedelta(hours=5)
    _make_disposal_projection(db_session, match, player, generated_at=generated_at)
    add_context_item(
        db_session, match_id=match.id, context_type="returning_player", source="Official team announcement",
        summary="Returning from a 4-week absence", confidence="official", player_id=player.id, team_id=home.id,
        source_timestamp=NOW - timedelta(hours=1),  # newer than the projection's generated_at
    )
    result = detect_context_conflicts(db_session, {"match_id": match.id, "team_id": home.id, "player_id": player.id, "market_type": "player_disposals"})
    assert CTX_MODEL_PREDATES_CONTEXT in result.codes
    # SQLite round-trips datetimes as naive - compare on naive terms rather
    # than asserting identity with the original tz-aware Python value.
    assert result.model_generated_at.replace(tzinfo=None) == generated_at.replace(tzinfo=None)
    assert result.latest_context_at is not None
    assert result.latest_context_at > generated_at


def test_player_projection_does_not_predate_older_context(db_session):
    match, home, away, player = _seed(db_session)
    generated_at = NOW - timedelta(hours=1)
    _make_disposal_projection(db_session, match, player, generated_at=generated_at)
    add_context_item(
        db_session, match_id=match.id, context_type="returning_player", source="Manual", summary="Old note",
        confidence="unverified", player_id=player.id, team_id=home.id, source_timestamp=NOW - timedelta(days=3),
    )
    result = detect_context_conflicts(db_session, {"match_id": match.id, "team_id": home.id, "player_id": player.id, "market_type": "player_disposals"})
    assert CTX_MODEL_PREDATES_CONTEXT not in result.codes


def test_team_opportunity_always_predates_any_lineup_affecting_context(db_session):
    """Team markets are computed live from Elo/Poisson, which has no
    lineup feature at all - presence of any current lineup-affecting item
    for that team is itself sufficient, regardless of timing."""
    match, home, away, player = _seed(db_session)
    add_context_item(db_session, match_id=match.id, context_type="late_withdrawal", source="Official team announcement", summary="Withdrawn", confidence="official", player_id=player.id, team_id=home.id)
    result = detect_context_conflicts(db_session, {"match_id": match.id, "team_id": home.id, "player_id": None, "market_type": "h2h"})
    assert CTX_MODEL_PREDATES_CONTEXT in result.codes
    assert result.model_generated_at is None  # team markets have no stored generation time


def test_no_context_no_conflict_codes(db_session):
    match, home, away, player = _seed(db_session)
    result = detect_context_conflicts(db_session, {"match_id": match.id, "team_id": home.id, "player_id": player.id, "market_type": "player_disposals"})
    assert result.codes == []
    assert result.latest_context_at is None
