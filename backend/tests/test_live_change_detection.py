"""Tests for live_change_detection.py — Section 5/16: a match must be
flagged for regeneration exactly when something relevant to it changed,
and left alone otherwise (the whole point of scoped regeneration).
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import (
    ExpectedLineup,
    GoalModelRun,
    Match,
    MatchStatus,
    Player,
    PlayerMatchStat,
    PlayerModelRun,
    Round,
    Season,
    Sport,
    Team,
)
from app.player_modelling.live_change_detection import detect_matches_needing_regeneration
from app.player_modelling.live_engine import generate_live_projections
from app.player_modelling.live_persistence import persist_projection_run
from app.player_modelling.market import PlayerMarket
from app.player_modelling.upcoming_features import load_next_upcoming_round

BASE = datetime(2025, 4, 1, tzinfo=timezone.utc)
UPCOMING = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _seed_environment(db, n_matches=2):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season_2025 = Season(sport_id=sport.id, year=2025)
    season_2026 = Season(sport_id=sport.id, year=2026)
    db.add_all([season_2025, season_2026])
    db.flush()

    teams = [Team(sport_id=sport.id, name=f"Team{i}", short_name=f"T{i}") for i in range(2 * n_matches)]
    db.add_all(teams)
    db.flush()

    players_by_team = {}
    for team in teams:
        players = []
        for j in range(3):
            p = Player(sport_id=sport.id, display_name=f"{team.short_name} Player {j}", source="afltables", source_player_id=f"players/{team.short_name}/P{j}.html")
            db.add(p)
            db.flush()
            players.append(p)
        players_by_team[team.id] = players

    for round_num in range(1, 4):
        round_ = Round(season_id=season_2025.id, round_number=round_num)
        db.add(round_)
        db.flush()
        for i in range(n_matches):
            home, away = teams[2 * i], teams[2 * i + 1]
            match = Match(
                sport_id=sport.id, season_id=season_2025.id, round_id=round_.id,
                home_team_id=home.id, away_team_id=away.id, scheduled_start=BASE + timedelta(days=7 * round_num, hours=i),
                status=MatchStatus.COMPLETED, home_score=80, away_score=70,
            )
            db.add(match)
            db.flush()
            for team_id, opp_id in ((home.id, away.id), (away.id, home.id)):
                for p in players_by_team[team_id]:
                    db.add(
                        PlayerMatchStat(
                            player_id=p.id, match_id=match.id, team_id=team_id, opponent_team_id=opp_id, source="afltables",
                            recorded_at=match.scheduled_start, disposals=15, goals=1, kicks=8, marks=4, handballs=7,
                            tackles=2, contested_possessions=5, uncontested_possessions=6, inside_50s=2,
                            marks_inside_50=1, goal_assists=0, time_on_ground_pct=80, behinds=0,
                        )
                    )
    db.flush()

    upcoming_round = Round(season_id=season_2026.id, round_number=1)
    db.add(upcoming_round)
    db.flush()
    upcoming_matches = []
    for i in range(n_matches):
        home, away = teams[2 * i], teams[2 * i + 1]
        m = Match(
            sport_id=sport.id, season_id=season_2026.id, round_id=upcoming_round.id,
            home_team_id=home.id, away_team_id=away.id, scheduled_start=UPCOMING + timedelta(hours=i), status=MatchStatus.SCHEDULED,
        )
        db.add(m)
        db.flush()
        upcoming_matches.append(m)
    db.commit()

    return upcoming_matches, teams, players_by_team


def _seed_team_and_player_models(db):
    persist_model_run(db, "elo", EloConfig(), 2024, metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 100, "holdout_value": 0.2, "naive_baseline_value": 0.25, "has_edge_over_naive": True}])
    persist_model_run(db, "poisson", PoissonConfig(), 2024, metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 100, "holdout_value": 0.21, "naive_baseline_value": 0.25, "has_edge_over_naive": True}])
    now = datetime.now(timezone.utc)
    db.add(PlayerModelRun(model_name="disposals_baseline_last5", market=PlayerMarket.DISPOSALS.value, feature_names=[], config_json={}, distribution_method="nb", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019, evaluation_end_year=2025, is_promoted=True, run_at=now))
    db.add(GoalModelRun(model_name="goals_baseline_last5", market=PlayerMarket.GOALS.value, feature_names=[], config_json={}, distribution_kind="nb", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019, evaluation_end_year=2025, is_promoted=True, run_at=now))
    db.commit()


def _set_lineup(db, match_id, player_id, team_id, status="expected_in", selection_status="confirmed_selected"):
    db.add(ExpectedLineup(match_id=match_id, player_id=player_id, team_id=team_id, status=status, selection_status=selection_status, is_confirmed=(selection_status == "confirmed_selected"), recorded_at=datetime.now(timezone.utc), source="manual"))
    db.commit()


def test_no_matches_need_regeneration_when_nothing_persisted_and_nothing_expected(db_session):
    matches, teams, players = _seed_environment(db_session)
    _seed_team_and_player_models(db_session)
    upcoming = load_next_upcoming_round(db_session)
    # no lineups at all -> expected set is empty for every match, and no projections exist -> both empty sets match -> no regeneration needed
    changed = detect_matches_needing_regeneration(db_session, upcoming)
    assert changed == set()


def test_new_expected_player_triggers_regeneration_for_that_match_only(db_session):
    matches, teams, players = _seed_environment(db_session, n_matches=2)
    _seed_team_and_player_models(db_session)
    upcoming = load_next_upcoming_round(db_session)

    _set_lineup(db_session, matches[0].id, players[teams[0].id][0].id, teams[0].id)

    changed = detect_matches_needing_regeneration(db_session, upcoming)
    assert changed == {matches[0].id}
    assert matches[1].id not in changed


def test_persisted_and_matching_state_needs_no_regeneration(db_session):
    matches, teams, players = _seed_environment(db_session, n_matches=1)
    _seed_team_and_player_models(db_session)
    _set_lineup(db_session, matches[0].id, players[teams[0].id][0].id, teams[0].id)

    run = generate_live_projections(db_session)
    persist_projection_run(db_session, run)

    upcoming = load_next_upcoming_round(db_session)
    changed = detect_matches_needing_regeneration(db_session, upcoming)
    assert changed == set()


def test_lineup_status_change_after_persist_triggers_regeneration(db_session):
    matches, teams, players = _seed_environment(db_session, n_matches=1)
    _seed_team_and_player_models(db_session)
    player = players[teams[0].id][0]
    _set_lineup(db_session, matches[0].id, player.id, teams[0].id, selection_status="confirmed_selected")

    run = generate_live_projections(db_session)
    persist_projection_run(db_session, run)

    upcoming = load_next_upcoming_round(db_session)
    assert detect_matches_needing_regeneration(db_session, upcoming) == set()

    # now mark the player confirmed_out - status flips to expected_out
    lineup = db_session.query(ExpectedLineup).filter_by(player_id=player.id, match_id=matches[0].id).one()
    lineup.selection_status = "confirmed_out"
    lineup.status = "expected_out"
    db_session.commit()

    changed = detect_matches_needing_regeneration(db_session, upcoming)
    assert changed == {matches[0].id}


def test_removed_lineup_record_triggers_regeneration(db_session):
    matches, teams, players = _seed_environment(db_session, n_matches=1)
    _seed_team_and_player_models(db_session)
    player = players[teams[0].id][0]
    _set_lineup(db_session, matches[0].id, player.id, teams[0].id)

    run = generate_live_projections(db_session)
    persist_projection_run(db_session, run)
    upcoming = load_next_upcoming_round(db_session)
    assert detect_matches_needing_regeneration(db_session, upcoming) == set()

    db_session.query(ExpectedLineup).filter_by(player_id=player.id, match_id=matches[0].id).delete()
    db_session.commit()

    changed = detect_matches_needing_regeneration(db_session, upcoming)
    assert changed == {matches[0].id}


def test_unrelated_match_is_not_affected_by_another_matchs_change(db_session):
    matches, teams, players = _seed_environment(db_session, n_matches=2)
    _seed_team_and_player_models(db_session)
    p0 = players[teams[0].id][0]
    p2 = players[teams[2].id][0]
    _set_lineup(db_session, matches[0].id, p0.id, teams[0].id)
    _set_lineup(db_session, matches[1].id, p2.id, teams[2].id)

    run = generate_live_projections(db_session)
    persist_projection_run(db_session, run)
    upcoming = load_next_upcoming_round(db_session)
    assert detect_matches_needing_regeneration(db_session, upcoming) == set()

    lineup = db_session.query(ExpectedLineup).filter_by(player_id=p0.id, match_id=matches[0].id).one()
    lineup.selection_status = "confirmed_out"
    lineup.status = "expected_out"
    db_session.commit()

    changed = detect_matches_needing_regeneration(db_session, upcoming)
    assert changed == {matches[0].id}
    assert matches[1].id not in changed
