"""Tests for team_selection_ingestion.py — Section 16: confirmed/
provisional lineup states, source provenance, manual-override preservation,
changed-lineup reporting, identity resolution/ambiguity, and the
suggested-roster/announcement-state helpers.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import ExpectedLineup, Match, MatchStatus, Player, PlayerMatchStat, Round, SelectionStatus, Season, Sport, Team
from app.player_modelling.team_selection_ingestion import (
    ANNOUNCEMENT_FINAL_CONFIRMED,
    ANNOUNCEMENT_NOT_ANNOUNCED,
    ANNOUNCEMENT_SQUAD_ANNOUNCED,
    SelectionEntry,
    derive_announcement_state,
    ingest_team_selections,
    resolve_player_identity,
    suggest_roster_from_recent_match,
)

BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _seed_teams_and_match(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    away = Team(sport_id=sport.id, name="Richmond", short_name="RIC")
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id,
        home_team_id=home.id, away_team_id=away.id, scheduled_start=BASE, status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    return match, home, away


def _add_player(db, sport_id, team_id, name, source_id, current_team=True):
    p = Player(sport_id=sport_id, display_name=name, source="afltables", source_player_id=source_id, current_team_id=team_id if current_team else None)
    db.add(p)
    db.commit()
    return p


# --- Identity resolution ---


def test_resolve_player_identity_matches_current_roster(db_session):
    match, home, away = _seed_teams_and_match(db_session)
    p = _add_player(db_session, home.sport_id, home.id, "Sam Walsh", "players/W/Sam_Walsh.html")
    resolution = resolve_player_identity(db_session, home.id, "Sam Walsh")
    assert resolution.player is not None
    assert resolution.player.id == p.id
    assert resolution.is_ambiguous is False


def test_resolve_player_identity_case_insensitive(db_session):
    match, home, away = _seed_teams_and_match(db_session)
    p = _add_player(db_session, home.sport_id, home.id, "Sam Walsh", "players/W/Sam_Walsh.html")
    resolution = resolve_player_identity(db_session, home.id, "sam walsh")
    assert resolution.player is not None
    assert resolution.player.id == p.id


def test_resolve_player_identity_unresolved_when_no_match(db_session):
    match, home, away = _seed_teams_and_match(db_session)
    resolution = resolve_player_identity(db_session, home.id, "Nobody Here")
    assert resolution.player is None
    assert resolution.is_ambiguous is False


def test_resolve_player_identity_ambiguous_when_multiple_current_roster_matches(db_session):
    """Never invents/guesses - if two players on the SAME team share a
    display name, this must be reported as ambiguous, not silently
    resolved to either one."""
    match, home, away = _seed_teams_and_match(db_session)
    _add_player(db_session, home.sport_id, home.id, "Same Name", "players/S/Same_Name1.html")
    _add_player(db_session, home.sport_id, home.id, "Same Name", "players/S/Same_Name2.html")
    resolution = resolve_player_identity(db_session, home.id, "Same Name")
    assert resolution.player is None
    assert resolution.is_ambiguous is True


def test_resolve_player_identity_falls_back_to_historical_team_for_recent_trade(db_session):
    match, home, away = _seed_teams_and_match(db_session)
    # player's current_team_id points elsewhere, but they have a historical PlayerMatchStat for `home`
    p = _add_player(db_session, home.sport_id, away.id, "Traded Player", "players/T/Traded_Player.html")
    stat_match = Match(
        sport_id=home.sport_id, season_id=match.season_id, round_id=match.round_id,
        home_team_id=home.id, away_team_id=away.id, scheduled_start=BASE - timedelta(days=200), status=MatchStatus.COMPLETED,
    )
    db_session.add(stat_match)
    db_session.flush()
    db_session.add(
        PlayerMatchStat(
            player_id=p.id, match_id=stat_match.id, team_id=home.id, opponent_team_id=away.id,
            source="afltables", recorded_at=stat_match.scheduled_start, disposals=15,
        )
    )
    db_session.commit()

    resolution = resolve_player_identity(db_session, home.id, "Traded Player")
    assert resolution.player is not None
    assert resolution.player.id == p.id


# --- ingest_team_selections ---


def test_ingest_creates_rows_with_provenance(db_session):
    match, home, away = _seed_teams_and_match(db_session)
    p = _add_player(db_session, home.sport_id, home.id, "Sam Walsh", "players/W/Sam_Walsh.html")
    ts = datetime(2026, 7, 30, 18, 20, tzinfo=timezone.utc)

    report = ingest_team_selections(
        db_session, match.id,
        [SelectionEntry(team_id=home.id, selection_status="confirmed_selected", player_name="Sam Walsh", source_reference="round23-announcement")],
        source="afl_official", source_timestamp=ts,
    )
    assert report.created == [p.id]
    assert report.unresolved == []

    row = db_session.scalar(select(ExpectedLineup).where(ExpectedLineup.player_id == p.id, ExpectedLineup.match_id == match.id))
    assert row.selection_status == "confirmed_selected"
    assert row.is_confirmed is True
    assert row.status == "expected_in"
    assert row.source == "afl_official"
    # SQLite drops tzinfo on round-trip for DateTime(timezone=True) columns - compare naively, as elsewhere in this codebase.
    assert row.source_timestamp.replace(tzinfo=timezone.utc) == ts
    assert row.source_reference == "round23-announcement"
    assert row.is_manual_override is False


def test_ingest_reports_unresolved_names_never_invents_player(db_session):
    match, home, away = _seed_teams_and_match(db_session)
    report = ingest_team_selections(
        db_session, match.id, [SelectionEntry(team_id=home.id, selection_status="confirmed_selected", player_name="Totally Unknown Player")],
        source="manual_bulk",
    )
    assert report.unresolved == ["Totally Unknown Player"]
    assert report.created == []
    assert db_session.scalar(select(ExpectedLineup)) is None


def test_ingest_reports_ambiguous_names(db_session):
    match, home, away = _seed_teams_and_match(db_session)
    _add_player(db_session, home.sport_id, home.id, "Dup", "players/D/Dup1.html")
    _add_player(db_session, home.sport_id, home.id, "Dup", "players/D/Dup2.html")
    report = ingest_team_selections(
        db_session, match.id, [SelectionEntry(team_id=home.id, selection_status="confirmed_selected", player_name="Dup")], source="manual_bulk",
    )
    assert report.ambiguous == ["Dup"]
    assert report.created == []


def test_ingest_reports_status_changed(db_session):
    match, home, away = _seed_teams_and_match(db_session)
    p = _add_player(db_session, home.sport_id, home.id, "Sam Walsh", "players/W/Sam_Walsh.html")
    ingest_team_selections(db_session, match.id, [SelectionEntry(team_id=home.id, selection_status="named_in_squad", player_id=p.id)], source="manual_bulk")
    report = ingest_team_selections(db_session, match.id, [SelectionEntry(team_id=home.id, selection_status="confirmed_selected", player_id=p.id)], source="manual_bulk")
    assert report.status_changed == [(p.id, "named_in_squad", "confirmed_selected")]
    assert report.updated == [p.id]


def test_ingest_skips_manual_override_by_default(db_session):
    """Section 3: a manually-overridden row is preserved unless the caller
    explicitly opts into overriding it."""
    match, home, away = _seed_teams_and_match(db_session)
    p = _add_player(db_session, home.sport_id, home.id, "Sam Walsh", "players/W/Sam_Walsh.html")
    now = datetime.now(timezone.utc)
    db_session.add(
        ExpectedLineup(
            match_id=match.id, player_id=p.id, team_id=home.id, status="expected_out",
            selection_status="confirmed_out", is_confirmed=False, recorded_at=now, source="manual", is_manual_override=True,
        )
    )
    db_session.commit()

    report = ingest_team_selections(db_session, match.id, [SelectionEntry(team_id=home.id, selection_status="confirmed_selected", player_id=p.id)], source="manual_bulk")
    assert report.skipped_manual_override == [p.id]
    assert report.updated == []

    row = db_session.scalar(select(ExpectedLineup).where(ExpectedLineup.player_id == p.id))
    assert row.selection_status == "confirmed_out"  # unchanged


def test_ingest_can_force_override_manual_when_explicitly_requested(db_session):
    match, home, away = _seed_teams_and_match(db_session)
    p = _add_player(db_session, home.sport_id, home.id, "Sam Walsh", "players/W/Sam_Walsh.html")
    now = datetime.now(timezone.utc)
    db_session.add(
        ExpectedLineup(
            match_id=match.id, player_id=p.id, team_id=home.id, status="expected_out",
            selection_status="confirmed_out", is_confirmed=False, recorded_at=now, source="manual", is_manual_override=True,
        )
    )
    db_session.commit()

    report = ingest_team_selections(
        db_session, match.id, [SelectionEntry(team_id=home.id, selection_status="confirmed_selected", player_id=p.id)],
        source="manual_bulk", allow_override_manual=True,
    )
    assert report.updated == [p.id]
    row = db_session.scalar(select(ExpectedLineup).where(ExpectedLineup.player_id == p.id))
    assert row.selection_status == "confirmed_selected"
    assert row.is_manual_override is False  # a bulk write always clears the override flag


def test_ingest_bulk_write_never_sets_manual_override(db_session):
    match, home, away = _seed_teams_and_match(db_session)
    p = _add_player(db_session, home.sport_id, home.id, "Sam Walsh", "players/W/Sam_Walsh.html")
    ingest_team_selections(db_session, match.id, [SelectionEntry(team_id=home.id, selection_status="placeholder", player_id=p.id)], source="manual_bulk")
    row = db_session.scalar(select(ExpectedLineup).where(ExpectedLineup.player_id == p.id))
    assert row.is_manual_override is False


# --- announcement state ---


def test_derive_announcement_state_not_announced_when_only_placeholder():
    assert derive_announcement_state(["placeholder", "placeholder"]) == ANNOUNCEMENT_NOT_ANNOUNCED
    assert derive_announcement_state([]) == ANNOUNCEMENT_NOT_ANNOUNCED


def test_derive_announcement_state_squad_announced():
    assert derive_announcement_state(["named_in_squad", "placeholder"]) == ANNOUNCEMENT_SQUAD_ANNOUNCED


def test_derive_announcement_state_final_confirmed_takes_priority():
    assert derive_announcement_state(["named_in_squad", "confirmed_selected"]) == ANNOUNCEMENT_FINAL_CONFIRMED


# --- suggested roster ---


def test_suggest_roster_from_recent_match_returns_real_players_only(db_session):
    match, home, away = _seed_teams_and_match(db_session)
    p1 = _add_player(db_session, home.sport_id, home.id, "Player One", "players/O/Player_One.html")
    p2 = _add_player(db_session, home.sport_id, home.id, "Player Two", "players/T/Player_Two.html")
    completed = Match(
        sport_id=home.sport_id, season_id=match.season_id, round_id=match.round_id,
        home_team_id=home.id, away_team_id=away.id, scheduled_start=BASE - timedelta(days=10), status=MatchStatus.COMPLETED,
    )
    db_session.add(completed)
    db_session.flush()
    for p in (p1, p2):
        db_session.add(
            PlayerMatchStat(
                player_id=p.id, match_id=completed.id, team_id=home.id, opponent_team_id=away.id,
                source="afltables", recorded_at=completed.scheduled_start, disposals=15,
            )
        )
    db_session.commit()

    suggestions = suggest_roster_from_recent_match(db_session, home.id)
    assert {s.player_id for s in suggestions} == {p1.id, p2.id}
    assert all(s.last_match_id == completed.id for s in suggestions)


def test_suggest_roster_empty_when_no_completed_matches(db_session):
    match, home, away = _seed_teams_and_match(db_session)
    assert suggest_roster_from_recent_match(db_session, home.id) == []


def test_suggest_roster_includes_player_rested_from_the_single_most_recent_match(db_session):
    """Real-world bug: a fit, regular player rested/omitted for just ONE
    week (e.g. a bye-style rest) must still show up as selectable - looking
    only at the single most recent match drops them entirely, even though
    they played every other recent week and are clearly still on the list."""
    match, home, away = _seed_teams_and_match(db_session)
    regular = _add_player(db_session, home.sport_id, home.id, "Regular Player", "players/R/Regular_Player.html")
    rested_this_week = _add_player(db_session, home.sport_id, home.id, "Rested Player", "players/R/Rested_Player.html")

    older = Match(
        sport_id=home.sport_id, season_id=match.season_id, round_id=match.round_id,
        home_team_id=home.id, away_team_id=away.id, scheduled_start=BASE - timedelta(days=17), status=MatchStatus.COMPLETED,
    )
    latest = Match(
        sport_id=home.sport_id, season_id=match.season_id, round_id=match.round_id,
        home_team_id=home.id, away_team_id=away.id, scheduled_start=BASE - timedelta(days=10), status=MatchStatus.COMPLETED,
    )
    db_session.add_all([older, latest])
    db_session.flush()
    for p in (regular, rested_this_week):
        db_session.add(PlayerMatchStat(
            player_id=p.id, match_id=older.id, team_id=home.id, opponent_team_id=away.id,
            source="afltables", recorded_at=older.scheduled_start, disposals=15,
        ))
    db_session.add(PlayerMatchStat(
        player_id=regular.id, match_id=latest.id, team_id=home.id, opponent_team_id=away.id,
        source="afltables", recorded_at=latest.scheduled_start, disposals=18,
    ))
    db_session.commit()

    suggestions = suggest_roster_from_recent_match(db_session, home.id)
    assert {s.player_id for s in suggestions} == {regular.id, rested_this_week.id}
