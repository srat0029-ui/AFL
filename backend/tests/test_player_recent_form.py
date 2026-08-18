from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Match, MatchStatus, Player, PlayerMatchStat, Round, Season, Sport, Team
from app.player_modelling.player_recent_form import (
    conservative_model_flag,
    form_disagreement_label,
    hit_rate,
    hit_rate_description,
    load_recent_form,
    meets_threshold,
)

NOW = datetime.now(timezone.utc)


def _seed_player_with_history(db, disposal_history: list[int]):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name="Collingwood", short_name="COL")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db.add_all([round_, home, away])
    db.flush()
    player = Player(sport_id=sport.id, display_name="Test Player", source="afltables", source_player_id="p1", current_team_id=home.id)
    db.add(player)
    db.flush()

    for i, disposals in enumerate(disposal_history):
        match = Match(
            sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
            scheduled_start=NOW - timedelta(days=(len(disposal_history) - i) * 7), status=MatchStatus.COMPLETED,
        )
        db.add(match)
        db.flush()
        db.add(PlayerMatchStat(
            player_id=player.id, match_id=match.id, team_id=home.id, opponent_team_id=away.id,
            source="afltables", recorded_at=NOW, disposals=disposals,
        ))
    db.commit()
    return player


def test_load_recent_form_returns_chronological_order_most_recent_last(db_session):
    player = _seed_player_with_history(db_session, [10, 20, 30, 40, 50])
    form = load_recent_form(db_session, player.id, NOW + timedelta(days=1), "disposals")
    assert form.last10 == [10, 20, 30, 40, 50]
    assert form.last5 == [10, 20, 30, 40, 50]
    assert form.last5_avg == 30.0


def test_load_recent_form_only_last5_when_more_than_5_present(db_session):
    player = _seed_player_with_history(db_session, [5, 10, 15, 20, 25, 30, 35])
    form = load_recent_form(db_session, player.id, NOW + timedelta(days=1), "disposals")
    assert form.last5 == [15, 20, 25, 30, 35]
    assert form.last10 == [5, 10, 15, 20, 25, 30, 35]


def test_load_recent_form_excludes_matches_after_cutoff(db_session):
    player = _seed_player_with_history(db_session, [10, 20, 30])
    # cutoff BEFORE any match happened
    form = load_recent_form(db_session, player.id, NOW - timedelta(days=100), "disposals")
    assert form.last10 == []
    assert form.last5_avg is None


def test_meets_threshold_multi_plus_uses_gte():
    assert meets_threshold(2, 2.0, "multi_plus") is True
    assert meets_threshold(1, 2.0, "multi_plus") is False


def test_meets_threshold_over_under_uses_gt():
    assert meets_threshold(28, 27.5, "over_under") is True
    assert meets_threshold(27, 27.5, "over_under") is False


def test_hit_rate_description_counts_correctly():
    desc = hit_rate_description([25, 32, 18, 40, 29, 31, 22, 35, 19, 33], 30.0, "multi_plus")
    assert desc == "30+ in 5 of last 10 games"


def test_hit_rate_description_empty_history():
    assert hit_rate_description([], 30.0, "multi_plus") == "No recent history available."


def test_hit_rate_fraction():
    assert hit_rate([30, 30, 20, 20], 30.0, "multi_plus") == 0.5


def test_form_disagreement_hot_streak_exceeds_model():
    label = form_disagreement_label(model_probability=0.3, recent_hit_rate=0.6)
    assert label == "Recent hot streak exceeds model expectation"


def test_form_disagreement_model_above_recent_form():
    label = form_disagreement_label(model_probability=0.7, recent_hit_rate=0.3)
    assert label == "Model probability substantially above recent hit rate"


def test_form_disagreement_none_when_close():
    assert form_disagreement_label(model_probability=0.45, recent_hit_rate=0.5) is None


def test_form_disagreement_none_without_recent_data():
    assert form_disagreement_label(model_probability=0.45, recent_hit_rate=None) is None


def test_conservative_model_flag_fires_when_two_baselines_exceed_gap():
    flag = conservative_model_flag(20.0, {"last5_avg": 25.0, "last10_avg": 24.0, "ewma": 21.0})
    assert flag == "Model is conservative relative to recent form"


def test_conservative_model_flag_absent_when_only_one_baseline_exceeds_gap():
    flag = conservative_model_flag(20.0, {"last5_avg": 25.0, "last10_avg": 21.0, "ewma": 21.0})
    assert flag is None


def test_conservative_model_flag_absent_when_baselines_missing():
    flag = conservative_model_flag(20.0, {"last5_avg": None, "last10_avg": None, "ewma": None})
    assert flag is None
