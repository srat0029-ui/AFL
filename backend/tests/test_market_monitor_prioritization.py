"""Targeted tests for the Alert Precision + Trader Prioritisation stage:
case deduplication, persistent vs transient, cross-book confirmation,
model-supported outlier, context severity, neighbouring-threshold
(curve) confirmation, deterministic prioritisation, and resolved-case
handling."""

from datetime import datetime, timedelta, timezone

from app.market_monitor.case_builder import build_cases, case_key
from app.market_monitor.case_persistence import (
    LIFECYCLE_NEW,
    LIFECYCLE_PERSISTING,
    LIFECYCLE_RESOLVED,
    lifecycle_status,
    record_case_observation,
    resolve_stale_cases,
    set_manual_status,
)
from app.market_monitor.priority import compute_priority
from app.market_monitor.types import (
    BOOKMAKER_VS_CONSENSUS_OUTLIER,
    LARGE_MARKET_DISPERSION,
    MODEL_VS_MARKET_DIVERGENCE,
    STALE_AFTER_LINEUP_CHANGE,
    ADJACENT_THRESHOLD_JUMP,
    BookmakerPriceEntry,
    Alert,
)
from app.market_monitor.common import aware
from app.models import AnomalyCaseRecord

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _alert(**overrides) -> Alert:
    base = dict(
        alert_type=MODEL_VS_MARKET_DIVERGENCE, severity="warning", reason_code="x", detail="x",
        match_id=1, home_team="Home", away_team="Away", player_id=100, player_name="Test Player", team_id=10,
        market_type="player_disposals", selection="over", threshold=20.5, line_value=None,
        model_probability=0.60, model_fair_odds=1.67, market_consensus_probability=0.45,
        bookmaker_prices=[], freshness="fresh", model_version="v1", lineup_status=None, context_state=None,
        model_risk_flags=[], generated_at=NOW, magnitude=0.15,
    )
    base.update(overrides)
    return Alert(**base)


def _books(*pairs) -> list[BookmakerPriceEntry]:
    return [BookmakerPriceEntry(name, price, NOW, "included") for name, price in pairs]


# --- Case deduplication ------------------------------------------------------


def test_alerts_on_the_same_market_merge_into_one_case():
    alerts = [
        _alert(alert_type=MODEL_VS_MARKET_DIVERGENCE, magnitude=0.12),
        _alert(alert_type=LARGE_MARKET_DISPERSION, magnitude=0.11),
        _alert(alert_type=BOOKMAKER_VS_CONSENSUS_OUTLIER, magnitude=25.0),
    ]
    cases = build_cases(alerts)
    assert len(cases) == 1
    assert len(cases[0].alerts) == 3
    assert set(cases[0].supporting_alert_types) == {LARGE_MARKET_DISPERSION, BOOKMAKER_VS_CONSENSUS_OUTLIER}
    assert cases[0].primary_alert.alert_type == MODEL_VS_MARKET_DIVERGENCE  # divergence outranks the others deterministically


def test_alerts_on_different_thresholds_stay_separate_cases():
    alerts = [_alert(threshold=15.5), _alert(threshold=25.5)]
    cases = build_cases(alerts)
    assert len(cases) == 2


def test_primary_alert_selection_is_deterministic_by_severity_then_type():
    """Same severity, different types - a re-run must always pick the
    same primary alert, never depend on list order."""
    alerts_a = [_alert(alert_type=LARGE_MARKET_DISPERSION, severity="warning"), _alert(alert_type=MODEL_VS_MARKET_DIVERGENCE, severity="warning")]
    alerts_b = list(reversed(alerts_a))
    assert build_cases(alerts_a)[0].primary_alert.alert_type == build_cases(alerts_b)[0].primary_alert.alert_type == MODEL_VS_MARKET_DIVERGENCE


# --- Cross-book confirmation + model support ---------------------------------


def test_cross_book_confirmation_scales_with_book_count():
    few_books = build_cases([_alert(bookmaker_prices=_books(("A", 2.0), ("B", 2.1)))])[0]
    many_books = build_cases([_alert(bookmaker_prices=_books(*[(f"BK{i}", 2.0 + i * 0.01) for i in range(10)]))])[0]
    p_few = compute_priority(few_books, kickoff=None)
    p_many = compute_priority(many_books, kickoff=None)
    cross_few = next(c for c in p_few.components if c.name == "cross_book_confirmation")
    cross_many = next(c for c in p_many.components if c.name == "cross_book_confirmation")
    assert cross_many.contribution > cross_few.contribution
    assert p_many.total_score > p_few.total_score


def test_model_supported_outlier_scores_higher_than_market_only_disagreement():
    # Model (0.60) sits close to the outlier's own implied prob, far from consensus (0.45)
    supported = build_cases([_alert(
        model_probability=0.60, market_consensus_probability=0.45, magnitude=0.15,
        bookmaker_prices=_books(("Outlier", 1 / 0.62), ("A", 1 / 0.44), ("B", 1 / 0.45), ("C", 1 / 0.46)),
    )])[0]
    # Model (0.46) sits close to consensus, NOT the outlier - a market-only disagreement
    market_only = build_cases([_alert(
        model_probability=0.46, market_consensus_probability=0.45, magnitude=0.15,
        bookmaker_prices=_books(("Outlier", 1 / 0.62), ("A", 1 / 0.44), ("B", 1 / 0.45), ("C", 1 / 0.46)),
    )])[0]
    p_supported = compute_priority(supported, kickoff=None)
    p_market_only = compute_priority(market_only, kickoff=None)
    assert p_supported.model_support is True
    assert p_market_only.model_support is False
    assert p_supported.total_score > p_market_only.total_score


# --- Context severity ---------------------------------------------------------


def test_confirmed_out_ranks_higher_than_confirmed_selected():
    confirmed_out = build_cases([_alert(
        alert_type=STALE_AFTER_LINEUP_CHANGE, lineup_status="confirmed_out", magnitude=None,
    )])[0]
    confirmed_selected = build_cases([_alert(
        alert_type=STALE_AFTER_LINEUP_CHANGE, lineup_status="confirmed_selected", magnitude=None,
    )])[0]
    p_out = compute_priority(confirmed_out, kickoff=None)
    p_selected = compute_priority(confirmed_selected, kickoff=None)
    ctx_out = next(c for c in p_out.components if c.name == "context_severity")
    ctx_selected = next(c for c in p_selected.components if c.name == "context_severity")
    assert ctx_out.contribution > ctx_selected.contribution


# --- Neighbouring-threshold (curve) confirmation ------------------------------


def test_confirmed_local_jump_scores_higher_than_a_weak_one():
    strong_jump = build_cases([_alert(alert_type=ADJACENT_THRESHOLD_JUMP, magnitude=8.0, threshold=25.5)])[0]  # 8x neighbouring gaps
    weak_jump = build_cases([_alert(alert_type=ADJACENT_THRESHOLD_JUMP, magnitude=3.1, threshold=25.5)])[0]  # just over the detection floor
    p_strong = compute_priority(strong_jump, kickoff=None)
    p_weak = compute_priority(weak_jump, kickoff=None)
    assert p_strong.total_score > p_weak.total_score


# --- Deterministic prioritisation --------------------------------------------


def test_compute_priority_is_deterministic_across_repeated_calls():
    case = build_cases([_alert(magnitude=0.18, bookmaker_prices=_books(("A", 2.0), ("B", 2.05)))])[0]
    scores = {compute_priority(case, kickoff=None, now=NOW).total_score for _ in range(5)}
    assert len(scores) == 1


# --- Persistence: transient vs persistent, resolved handling -----------------


def test_persistence_score_increases_with_repeated_snapshots(db_session):
    case = build_cases([_alert(magnitude=0.20)])[0]
    p1 = compute_priority(case, kickoff=None, n_snapshots=1)
    p3 = compute_priority(case, kickoff=None, n_snapshots=3)
    assert p1.persistence_label == "transient"
    assert p3.persistence_label == "persistent"
    assert p3.total_score > p1.total_score


def test_record_case_observation_tracks_first_last_seen_and_count(db_session):
    key = "1:100:10:player_disposals:over:20.5:"
    r1 = record_case_observation(db_session, key, match_id=1, now=NOW)
    db_session.commit()
    assert r1.n_snapshots == 1
    assert aware(r1.first_seen_at) == NOW

    r2 = record_case_observation(db_session, key, match_id=1, now=NOW + timedelta(hours=1))
    db_session.commit()
    assert r2.n_snapshots == 2
    assert aware(r2.first_seen_at) == NOW  # first_seen never moves
    assert aware(r2.last_seen_at) == NOW + timedelta(hours=1)
    assert lifecycle_status(r2) == LIFECYCLE_PERSISTING


def test_resolve_stale_cases_marks_cases_absent_from_current_pass(db_session):
    key_a, key_b = "case-a", "case-b"
    record_case_observation(db_session, key_a, match_id=1, now=NOW)
    record_case_observation(db_session, key_a, match_id=1, now=NOW + timedelta(minutes=30))  # a 2nd sighting -> "persisting"
    record_case_observation(db_session, key_b, match_id=1, now=NOW)
    db_session.commit()

    n = resolve_stale_cases(db_session, current_case_keys={key_a}, now=NOW + timedelta(hours=2))
    db_session.commit()
    assert n == 1

    from sqlalchemy import select

    rec_a = db_session.scalar(select(AnomalyCaseRecord).where(AnomalyCaseRecord.case_key == key_a))
    rec_b = db_session.scalar(select(AnomalyCaseRecord).where(AnomalyCaseRecord.case_key == key_b))
    assert lifecycle_status(rec_a) == LIFECYCLE_PERSISTING  # still active, untouched
    assert lifecycle_status(rec_b) == LIFECYCLE_RESOLVED


def test_a_resolved_case_that_reappears_restarts_its_streak(db_session):
    key = "case-a"
    record_case_observation(db_session, key, match_id=1, now=NOW)
    db_session.commit()
    resolve_stale_cases(db_session, current_case_keys=set(), now=NOW + timedelta(hours=1))
    db_session.commit()

    record = record_case_observation(db_session, key, match_id=1, now=NOW + timedelta(hours=5))
    db_session.commit()
    assert record.resolved_at is None
    assert record.n_snapshots == 1  # restarted, not inflated across the gap
    assert aware(record.first_seen_at) == NOW + timedelta(hours=5)


def test_never_seen_case_has_new_lifecycle():
    assert lifecycle_status(None) == LIFECYCLE_NEW


def test_manual_status_can_be_set_and_is_never_touched_by_scoring(db_session):
    key = "case-a"
    record_case_observation(db_session, key, match_id=1, now=NOW)
    db_session.commit()

    updated = set_manual_status(db_session, key, "acknowledged")
    db_session.commit()
    assert updated.manual_status == "acknowledged"

    # Re-observing the case must never reset a human's manual status.
    record_case_observation(db_session, key, match_id=1, now=NOW + timedelta(hours=1))
    db_session.commit()
    from sqlalchemy import select

    record = db_session.scalar(select(AnomalyCaseRecord).where(AnomalyCaseRecord.case_key == key))
    assert record.manual_status == "acknowledged"


def test_set_manual_status_rejects_unknown_values(db_session):
    key = "case-a"
    record_case_observation(db_session, key, match_id=1, now=NOW)
    db_session.commit()
    import pytest

    with pytest.raises(ValueError):
        set_manual_status(db_session, key, "not_a_real_status")
