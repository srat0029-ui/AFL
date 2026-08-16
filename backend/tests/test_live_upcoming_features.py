"""Tests for upcoming_features.py — leakage-safety and lineup-filtering
logic for the live-projection stage. Mirrors the disposal/goal feature
builder tests' point-in-time discipline, applied to the "as of now"
synthetic-row mechanism (see module docstring).
"""

from datetime import datetime, timedelta, timezone

from app.player_modelling.disposal_data import PlayerGameRow, TeamGameRow
from app.player_modelling.goal_data import PlayerGoalGameRow, TeamGoalGameRow
from app.player_modelling.upcoming_features import (
    ExpectedPlayer,
    UpcomingMatchTeams,
    build_upcoming_disposal_features,
    build_upcoming_goal_features,
)

BASE = datetime(2025, 4, 1, tzinfo=timezone.utc)
UPCOMING = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _player_row(player_id, match_id, when, disposals, team_id=10, opponent_id=20):
    return PlayerGameRow(
        player_id=player_id, match_id=match_id, team_id=team_id, opponent_team_id=opponent_id,
        season_year=when.year, round_number=1, is_final=False, is_home=True, venue_id=1, scheduled_start=when,
        disposals=disposals, kicks=disposals // 2, handballs=disposals // 2, marks=3, tackles=2, clearances=1,
        inside_50s=2, contested_possessions=4, uncontested_possessions=5, time_on_ground_pct=80,
        subbed_on=False, subbed_off=False,
    )


class _FakeDb:
    """A minimal stand-in - build_upcoming_disposal_features/goal_features
    call load_player_game_rows(db)/load_team_game_rows(db) internally, so
    these unit tests monkeypatch those loaders instead of hitting a real DB
    (see the tests below)."""


def test_upcoming_disposal_features_only_use_strictly_prior_real_rows(monkeypatch):
    real_rows = [_player_row(1, m, BASE + timedelta(days=7 * i), disposals=10 + i) for i, m in enumerate([100, 101, 102])]
    monkeypatch.setattr("app.player_modelling.upcoming_features.load_player_game_rows", lambda db: real_rows)
    monkeypatch.setattr("app.player_modelling.upcoming_features.load_team_game_rows", lambda db: [])

    upcoming = [
        UpcomingMatchTeams(
            match_id=999, home_team_id=10, away_team_id=20, venue_id=1, scheduled_start=UPCOMING,
            season_year=2026, round_number=1, is_final=False,
        )
    ]
    expected = [ExpectedPlayer(player_id=1, match_id=999, team_id=10, opponent_team_id=20, is_home=True, status="expected_in")]

    result = build_upcoming_disposal_features(None, upcoming, expected, {})
    row = result[(1, 999)]

    assert row.games_of_history == 3  # exactly the 3 real prior games, not counting the synthetic row itself
    # last3_avg over (10, 11, 12) = 11 - the synthetic row's dummy disposals=0 must never enter this average
    assert row.features["disposals_last3_avg"] == 11.0


def test_upcoming_disposal_features_zero_history_for_debutant(monkeypatch):
    monkeypatch.setattr("app.player_modelling.upcoming_features.load_player_game_rows", lambda db: [])
    monkeypatch.setattr("app.player_modelling.upcoming_features.load_team_game_rows", lambda db: [])

    upcoming = [
        UpcomingMatchTeams(match_id=999, home_team_id=10, away_team_id=20, venue_id=1, scheduled_start=UPCOMING, season_year=2026, round_number=1, is_final=False)
    ]
    expected = [ExpectedPlayer(player_id=5, match_id=999, team_id=10, opponent_team_id=20, is_home=True, status="uncertain")]

    result = build_upcoming_disposal_features(None, upcoming, expected, {})
    row = result[(5, 999)]
    assert row.games_of_history == 0
    assert row.features["disposals_last5_avg"] is None


def test_upcoming_goal_features_only_use_strictly_prior_real_rows(monkeypatch):
    real_rows = [
        PlayerGoalGameRow(
            player_id=2, match_id=200 + i, team_id=10, opponent_team_id=20, season_year=2025, round_number=1,
            is_final=False, is_home=True, venue_id=1, scheduled_start=BASE + timedelta(days=7 * i), goals=i,
            behinds=0, disposals=15, kicks=8, marks=4, handballs=7, tackles=2, contested_possessions=5,
            uncontested_possessions=6, inside_50s=2, marks_inside_50=1, goal_assists=0, time_on_ground_pct=80,
            subbed_on=False, subbed_off=False,
        )
        for i in range(3)
    ]
    monkeypatch.setattr("app.player_modelling.upcoming_features.load_player_goal_game_rows", lambda db: real_rows)
    monkeypatch.setattr("app.player_modelling.upcoming_features.load_team_goal_game_rows", lambda db: [])

    upcoming = [UpcomingMatchTeams(match_id=999, home_team_id=10, away_team_id=20, venue_id=1, scheduled_start=UPCOMING, season_year=2026, round_number=1, is_final=False)]
    expected = [ExpectedPlayer(player_id=2, match_id=999, team_id=10, opponent_team_id=20, is_home=True, status="expected_in")]

    result = build_upcoming_goal_features(None, upcoming, expected, {})
    row = result[(2, 999)]
    assert row.games_of_history == 3
    # goals were 0, 1, 2 -> avg of last 3 = 1.0; the synthetic row's dummy goals=0 must not shift this
    assert row.features["goals_last3_avg"] == 1.0


def test_two_upcoming_matches_for_different_players_do_not_cross_contaminate(monkeypatch):
    """Adversarial case: two different upcoming matches processed in one
    call (e.g. two different rounds slipped past the loader's single-round
    scoping) - each player's own synthetic row must still only reflect
    THAT player's real history, never another player's placeholder."""
    real_rows = [_player_row(1, 50, BASE, disposals=20), _player_row(2, 51, BASE, disposals=5)]
    monkeypatch.setattr("app.player_modelling.upcoming_features.load_player_game_rows", lambda db: real_rows)
    monkeypatch.setattr("app.player_modelling.upcoming_features.load_team_game_rows", lambda db: [])

    upcoming = [
        UpcomingMatchTeams(match_id=900, home_team_id=10, away_team_id=20, venue_id=1, scheduled_start=UPCOMING, season_year=2026, round_number=1, is_final=False),
        UpcomingMatchTeams(match_id=901, home_team_id=10, away_team_id=20, venue_id=1, scheduled_start=UPCOMING + timedelta(days=7), season_year=2026, round_number=2, is_final=False),
    ]
    expected = [
        ExpectedPlayer(player_id=1, match_id=900, team_id=10, opponent_team_id=20, is_home=True, status="expected_in"),
        ExpectedPlayer(player_id=2, match_id=901, team_id=10, opponent_team_id=20, is_home=True, status="expected_in"),
    ]

    result = build_upcoming_disposal_features(None, upcoming, expected, {})
    assert result[(1, 900)].features["disposals_last5_avg"] == 20.0
    assert result[(2, 901)].features["disposals_last5_avg"] == 5.0
