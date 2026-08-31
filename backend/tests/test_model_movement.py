from datetime import datetime, timedelta, timezone

import pytest

from app.models import (
    KIND_PROBABILITY,
    KIND_PROJECTED_MEAN,
    ModelValueObservation,
    VALUE_PLAYER_DISPOSAL_PROJECTED_MEAN,
    VALUE_TEAM_FAIR_ODDS,
    VALUE_TEAM_WIN_PROBABILITY,
)
from app.player_modelling.model_movement import _build_movement, _classify, recent_model_movements

NOW = datetime.now(timezone.utc)


def _obs(*, value_type=VALUE_TEAM_WIN_PROBABILITY, value_kind=KIND_PROBABILITY, value, recorded_at, lineup_status=None, match_id=1, player_id=None, selection="Collingwood", threshold=None):
    return ModelValueObservation(
        match_id=match_id, player_id=player_id, value_type=value_type, value_kind=value_kind, selection=selection,
        threshold=threshold, value=value, lineup_status=lineup_status, model_name="elo_poisson", model_version="v1",
        data_cutoff=NOW, recorded_at=recorded_at,
    )


class TestClassify:
    def test_team_probability_below_notable(self):
        notable, material = _classify(VALUE_TEAM_WIN_PROBABILITY, 0.01, None)
        assert notable is False and material is False

    def test_team_probability_notable_not_material(self):
        notable, material = _classify(VALUE_TEAM_WIN_PROBABILITY, 0.04, None)
        assert notable is True and material is False

    def test_team_probability_material(self):
        notable, material = _classify(VALUE_TEAM_WIN_PROBABILITY, 0.08, None)
        assert notable is True and material is True

    def test_direction_does_not_matter_only_magnitude(self):
        notable_pos, material_pos = _classify(VALUE_TEAM_WIN_PROBABILITY, 0.08, None)
        notable_neg, material_neg = _classify(VALUE_TEAM_WIN_PROBABILITY, -0.08, None)
        assert (notable_pos, material_pos) == (notable_neg, material_neg)

    def test_fair_odds_is_never_independently_material(self):
        # Fair odds is a monotonic transform of team win probability, which
        # already has its own materiality check - fair odds movements are
        # still computed/returned for display, just never flagged as their
        # own signal (avoids double-counting the same underlying fact).
        notable, material = _classify(VALUE_TEAM_FAIR_ODDS, 5.0, 0.5)
        assert notable is False and material is False

    def test_unrecognized_value_type_defaults_to_not_material(self):
        notable, material = _classify("something_new", 999.0, 999.0)
        assert notable is False and material is False


class TestBuildMovement:
    def test_computes_deltas_and_hours_between(self):
        previous = _obs(value=0.50, recorded_at=NOW - timedelta(hours=6))
        current = _obs(value=0.58, recorded_at=NOW)

        m = _build_movement(previous, current)

        assert m.previous_value == 0.50
        assert m.current_value == 0.58
        assert m.absolute_change == pytest.approx(0.08)
        assert m.relative_change == pytest.approx(0.16)
        assert m.hours_between == pytest.approx(6.0)
        assert m.is_material is True

    def test_lineup_status_change_detected(self):
        previous = _obs(value_type=VALUE_PLAYER_DISPOSAL_PROJECTED_MEAN, value_kind=KIND_PROJECTED_MEAN, value=29.4, recorded_at=NOW - timedelta(hours=2), lineup_status="uncertain", player_id=1)
        current = _obs(value_type=VALUE_PLAYER_DISPOSAL_PROJECTED_MEAN, value_kind=KIND_PROJECTED_MEAN, value=31.1, recorded_at=NOW, lineup_status="expected_in", player_id=1)

        m = _build_movement(previous, current)

        assert m.lineup_status_changed is True
        assert m.previous_lineup_status == "uncertain"
        assert m.current_lineup_status == "expected_in"
        assert m.is_notable is True  # 1.7 disposal change clears the notable default (calibrated to this exact example)
        assert m.is_material is False  # but not the stricter material bar

    def test_zero_previous_value_gives_none_relative_change(self):
        previous = _obs(value=0.0, recorded_at=NOW - timedelta(hours=1))
        current = _obs(value=0.05, recorded_at=NOW)

        m = _build_movement(previous, current)

        assert m.relative_change is None


class TestRecentModelMovements:
    def test_empty_match_ids_returns_empty(self, db_session):
        assert recent_model_movements(db_session, []) == []

    def test_identity_with_single_observation_is_excluded(self, db_session):
        db_session.add(_obs(value=0.5, recorded_at=NOW, match_id=1))
        db_session.commit()

        assert recent_model_movements(db_session, [1]) == []

    def test_two_observations_produce_one_movement(self, db_session):
        db_session.add(_obs(value=0.50, recorded_at=NOW - timedelta(hours=6), match_id=1))
        db_session.add(_obs(value=0.60, recorded_at=NOW, match_id=1))
        db_session.commit()

        movements = recent_model_movements(db_session, [1])

        assert len(movements) == 1
        assert movements[0].current_value == 0.60

    def test_material_movements_sort_before_non_material(self, db_session):
        # Small move on Carlton, large move on Collingwood.
        db_session.add(_obs(value=0.50, recorded_at=NOW - timedelta(hours=6), match_id=1, selection="Carlton"))
        db_session.add(_obs(value=0.51, recorded_at=NOW, match_id=1, selection="Carlton"))
        db_session.add(_obs(value=0.40, recorded_at=NOW - timedelta(hours=6), match_id=1, selection="Collingwood"))
        db_session.add(_obs(value=0.55, recorded_at=NOW, match_id=1, selection="Collingwood"))
        db_session.commit()

        movements = recent_model_movements(db_session, [1])

        assert len(movements) == 2
        assert movements[0].selection == "Collingwood"
        assert movements[0].is_material is True
        assert movements[1].is_material is False
