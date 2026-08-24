"""Targeted tests for the Prospective Alert Validation + Root-Cause
Intelligence stage (item 10): immutable case snapshots, toward/away
movement calculation, outlier convergence, context repricing,
persisted-to-kickoff classification, and no future information in frozen
case state."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.market_monitor.case_builder import build_cases
from app.market_monitor.case_snapshot_service import (
    _consensus_span,
    freeze_or_refresh_case_snapshots,
    settle_case_snapshots,
)
from app.market_monitor.inbox import RankedCase
from app.market_monitor.outcome_taxonomy import (
    CONSENSUS_REPRICED_AFTER_CONTEXT,
    CURVE_ANOMALY_RESOLVED,
    INCONCLUSIVE,
    MARKET_MOVED_AWAY_FROM_MODEL,
    MARKET_MOVED_TOWARD_MODEL,
    MODEL_MOVED_TOWARD_MARKET,
    OUTLIER_CONVERGED,
    PERSISTED_TO_KICKOFF,
    classify_outcomes,
)
from app.market_monitor.priority import TIER_CRITICAL, PriorityBreakdown
from app.market_monitor.types import MODEL_VS_MARKET_DIVERGENCE, Alert
from app.models import AnomalyCaseSnapshot, Bookmaker, Match, MatchStatus, Player, PlayerPropMarket, Round, Season, Sport, Team
from app.player_modelling.market import PlayerMarket

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _seed_match(db, *, status=MatchStatus.SCHEDULED, scheduled_start=None):
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
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=scheduled_start or (NOW + timedelta(days=2)), status=status,
    )
    db.add(match)
    db.commit()
    return match, home, away


def _seed_player(db, match, home):
    player = Player(sport_id=match.sport_id, display_name="Test Player", source="afltables", source_player_id="mm-p1", current_team_id=home.id)
    db.add(player)
    db.commit()
    return player


def _bookmaker(db, name):
    b = Bookmaker(name=name)
    db.add(b)
    db.commit()
    return b


def _alert(**overrides) -> Alert:
    base = dict(
        alert_type=MODEL_VS_MARKET_DIVERGENCE, severity="warning", reason_code="x", detail="x",
        match_id=1, home_team="Home", away_team="Away", player_id=None, player_name="Test Player", team_id=None,
        market_type=PlayerMarket.DISPOSALS.value, selection="over", threshold=25.5, line_value=None,
        model_probability=0.148, model_fair_odds=6.76, market_consensus_probability=0.45,
        bookmaker_prices=[], freshness="fresh", model_version="v1", lineup_status=None, context_state=None,
        model_risk_flags=[], generated_at=NOW, magnitude=0.30,
    )
    base.update(overrides)
    return Alert(**base)


def _critical_ranked_case(case):
    priority = PriorityBreakdown(total_score=75.0, tier=TIER_CRITICAL, components=[], persistence_label="transient", n_snapshots=1, model_support=None)
    return RankedCase(case=case, priority=priority, lifecycle="new", manual_status=None)


# --- classify_outcomes: toward/away movement --------------------------------


def test_market_moved_toward_model_when_gap_shrinks():
    codes = classify_outcomes(
        consensus_at_freeze=0.45, consensus_at_settlement=0.30, model_probability_at_freeze=0.15, model_probability_at_settlement=0.15,
        had_outlier_alert=False, outlier_converged=None, had_stale_context_alert=False, stale_market_repriced=None,
        had_curve_alert=False, curve_anomaly_resolved=None,
    )
    assert codes == [MARKET_MOVED_TOWARD_MODEL]


def test_market_moved_away_from_model_when_gap_grows():
    codes = classify_outcomes(
        consensus_at_freeze=0.45, consensus_at_settlement=0.60, model_probability_at_freeze=0.15, model_probability_at_settlement=0.15,
        had_outlier_alert=False, outlier_converged=None, had_stale_context_alert=False, stale_market_repriced=None,
        had_curve_alert=False, curve_anomaly_resolved=None,
    )
    assert codes == [MARKET_MOVED_AWAY_FROM_MODEL]


def test_persisted_to_kickoff_when_consensus_barely_moves():
    codes = classify_outcomes(
        consensus_at_freeze=0.45, consensus_at_settlement=0.46, model_probability_at_freeze=0.15, model_probability_at_settlement=0.15,
        had_outlier_alert=False, outlier_converged=None, had_stale_context_alert=False, stale_market_repriced=None,
        had_curve_alert=False, curve_anomaly_resolved=None,
    )
    assert codes == [PERSISTED_TO_KICKOFF]


def test_model_moved_toward_market():
    codes = classify_outcomes(
        consensus_at_freeze=0.45, consensus_at_settlement=None, model_probability_at_freeze=0.15, model_probability_at_settlement=0.30,
        had_outlier_alert=False, outlier_converged=None, had_stale_context_alert=False, stale_market_repriced=None,
        had_curve_alert=False, curve_anomaly_resolved=None,
    )
    assert codes == [MODEL_MOVED_TOWARD_MARKET]


def test_no_outcome_is_ever_labelled_a_win():
    from app.market_monitor.outcome_taxonomy import ALL_OUTCOME_CODES

    assert not any("WIN" in code for code in ALL_OUTCOME_CODES)


# --- classify_outcomes: outlier convergence / context repricing / curve -----


def test_outlier_converged_only_fires_when_had_alert_and_converged():
    fired = classify_outcomes(
        consensus_at_freeze=None, consensus_at_settlement=None, model_probability_at_freeze=None, model_probability_at_settlement=None,
        had_outlier_alert=True, outlier_converged=True, had_stale_context_alert=False, stale_market_repriced=None,
        had_curve_alert=False, curve_anomaly_resolved=None,
    )
    assert fired == [OUTLIER_CONVERGED]

    not_converged = classify_outcomes(
        consensus_at_freeze=None, consensus_at_settlement=None, model_probability_at_freeze=None, model_probability_at_settlement=None,
        had_outlier_alert=True, outlier_converged=False, had_stale_context_alert=False, stale_market_repriced=None,
        had_curve_alert=False, curve_anomaly_resolved=None,
    )
    assert not_converged == [INCONCLUSIVE]

    no_alert = classify_outcomes(
        consensus_at_freeze=None, consensus_at_settlement=None, model_probability_at_freeze=None, model_probability_at_settlement=None,
        had_outlier_alert=False, outlier_converged=True, had_stale_context_alert=False, stale_market_repriced=None,
        had_curve_alert=False, curve_anomaly_resolved=None,
    )
    assert no_alert == [INCONCLUSIVE]


def test_context_repriced_only_fires_when_had_alert_and_repriced():
    codes = classify_outcomes(
        consensus_at_freeze=None, consensus_at_settlement=None, model_probability_at_freeze=None, model_probability_at_settlement=None,
        had_outlier_alert=False, outlier_converged=None, had_stale_context_alert=True, stale_market_repriced=True,
        had_curve_alert=False, curve_anomaly_resolved=None,
    )
    assert codes == [CONSENSUS_REPRICED_AFTER_CONTEXT]


def test_curve_anomaly_resolved_only_fires_when_had_alert_and_resolved():
    codes = classify_outcomes(
        consensus_at_freeze=None, consensus_at_settlement=None, model_probability_at_freeze=None, model_probability_at_settlement=None,
        had_outlier_alert=False, outlier_converged=None, had_stale_context_alert=False, stale_market_repriced=None,
        had_curve_alert=True, curve_anomaly_resolved=True,
    )
    assert codes == [CURVE_ANOMALY_RESOLVED]


def test_a_case_can_carry_more_than_one_outcome_code():
    codes = classify_outcomes(
        consensus_at_freeze=0.45, consensus_at_settlement=0.30, model_probability_at_freeze=0.15, model_probability_at_settlement=0.15,
        had_outlier_alert=True, outlier_converged=True, had_stale_context_alert=False, stale_market_repriced=None,
        had_curve_alert=False, curve_anomaly_resolved=None,
    )
    assert codes == [MARKET_MOVED_TOWARD_MODEL, OUTLIER_CONVERGED]


def test_inconclusive_fallback_when_nothing_fires():
    codes = classify_outcomes(
        consensus_at_freeze=None, consensus_at_settlement=None, model_probability_at_freeze=None, model_probability_at_settlement=None,
        had_outlier_alert=False, outlier_converged=None, had_stale_context_alert=False, stale_market_repriced=None,
        had_curve_alert=False, curve_anomaly_resolved=None,
    )
    assert codes == [INCONCLUSIVE]


# --- No future information in frozen case state ------------------------------


def test_consensus_span_never_sees_a_quote_recorded_after_now(db_session):
    match, home, away = _seed_match(db_session)
    player = _seed_player(db_session, match, home)
    bookmaker = _bookmaker(db_session, "Ladbrokes")
    db_session.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
        line_type="over_under", threshold=25.5, selection="over", price_decimal=1.96, recorded_at=NOW, source="the_odds_api",
    ))
    db_session.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
        line_type="over_under", threshold=25.5, selection="over", price_decimal=2.50, recorded_at=NOW + timedelta(days=2), source="the_odds_api",
    ))
    db_session.commit()

    case = build_cases([_alert(match_id=match.id, player_id=player.id, home_team=home.name, away_team=away.name)])[0]

    _, latest_prob_early, _, latest_at_early, n_books_early = _consensus_span(db_session, case, now=NOW + timedelta(hours=1))
    assert n_books_early == 1
    assert latest_at_early.replace(tzinfo=timezone.utc) == NOW  # the future quote is invisible at this cutoff

    _, latest_prob_late, _, latest_at_late, n_books_late = _consensus_span(db_session, case, now=NOW + timedelta(days=3))
    assert latest_at_late.replace(tzinfo=timezone.utc) == NOW + timedelta(days=2)  # visible once "now" passes it
    assert latest_prob_late != latest_prob_early


# --- Immutable case snapshots -------------------------------------------------


def test_frozen_fields_are_never_overwritten_on_refresh(db_session):
    match, home, away = _seed_match(db_session)
    player = _seed_player(db_session, match, home)
    bookmaker = _bookmaker(db_session, "Ladbrokes")
    db_session.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
        line_type="over_under", threshold=25.5, selection="over", price_decimal=1.96, recorded_at=NOW, source="the_odds_api",
    ))
    db_session.commit()

    case = build_cases([_alert(match_id=match.id, player_id=player.id, home_team=home.name, away_team=away.name)])[0]
    ranked = _critical_ranked_case(case)

    n_new, n_refreshed = freeze_or_refresh_case_snapshots(db_session, [ranked], now=NOW)
    assert (n_new, n_refreshed) == (1, 0)

    snap = db_session.scalar(select(AnomalyCaseSnapshot).where(AnomalyCaseSnapshot.case_id == case.case_id))
    frozen_snapshot = (snap.frozen_at, snap.model_probability, snap.market_consensus_probability_at_freeze, snap.priority_score, snap.bookmaker_prices_at_freeze, snap.n_bookmakers_at_freeze)

    # A new, later quote arrives with a materially different price.
    db_session.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
        line_type="over_under", threshold=25.5, selection="over", price_decimal=3.25, recorded_at=NOW + timedelta(hours=2), source="the_odds_api",
    ))
    db_session.commit()

    n_new2, n_refreshed2 = freeze_or_refresh_case_snapshots(db_session, [ranked], now=NOW + timedelta(hours=3))
    assert (n_new2, n_refreshed2) == (0, 1)

    db_session.refresh(snap)
    assert (snap.frozen_at, snap.model_probability, snap.market_consensus_probability_at_freeze, snap.priority_score, snap.bookmaker_prices_at_freeze, snap.n_bookmakers_at_freeze) == frozen_snapshot
    # Only the rolling fields moved.
    assert snap.n_prekickoff_refreshes == 1
    assert snap.latest_observed_at.replace(tzinfo=timezone.utc) == NOW + timedelta(hours=3)
    assert snap.market_consensus_probability_latest != snap.market_consensus_probability_at_freeze


def test_freeze_only_touches_high_priority_and_critical_tiers(db_session):
    match, home, away = _seed_match(db_session)
    player = _seed_player(db_session, match, home)
    case = build_cases([_alert(match_id=match.id, player_id=player.id, home_team=home.name, away_team=away.name)])[0]
    low_priority = RankedCase(case=case, priority=PriorityBreakdown(total_score=5.0, tier="review_worthy", components=[], persistence_label="transient", n_snapshots=1, model_support=None), lifecycle="new", manual_status=None)

    n_new, n_refreshed = freeze_or_refresh_case_snapshots(db_session, [low_priority], now=NOW)
    assert (n_new, n_refreshed) == (0, 0)
    assert db_session.scalar(select(AnomalyCaseSnapshot).where(AnomalyCaseSnapshot.case_id == case.case_id)) is None


# --- Settlement: outcome classification + persisted-to-kickoff end to end ----


def test_settle_classifies_persisted_to_kickoff_when_completed_match_barely_moved(db_session):
    match, home, away = _seed_match(db_session, status=MatchStatus.SCHEDULED)
    player = _seed_player(db_session, match, home)
    bookmaker = _bookmaker(db_session, "Ladbrokes")
    db_session.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
        line_type="over_under", threshold=25.5, selection="over", price_decimal=1.96, recorded_at=NOW, source="the_odds_api",
    ))
    db_session.commit()

    case = build_cases([_alert(match_id=match.id, player_id=player.id, home_team=home.name, away_team=away.name)])[0]
    ranked = _critical_ranked_case(case)
    freeze_or_refresh_case_snapshots(db_session, [ranked], now=NOW)

    match.status = MatchStatus.COMPLETED
    db_session.commit()

    n_settled = settle_case_snapshots(db_session, now=NOW + timedelta(hours=3))
    assert n_settled == 1

    snap = db_session.scalar(select(AnomalyCaseSnapshot).where(AnomalyCaseSnapshot.case_id == case.case_id))
    assert snap.resolved_at is not None
    assert PERSISTED_TO_KICKOFF in snap.outcome_codes
    assert snap.time_to_resolution_hours == 3.0


def test_settle_never_revisits_an_already_resolved_snapshot(db_session):
    match, home, away = _seed_match(db_session, status=MatchStatus.SCHEDULED)
    player = _seed_player(db_session, match, home)
    bookmaker = _bookmaker(db_session, "Ladbrokes")
    db_session.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
        line_type="over_under", threshold=25.5, selection="over", price_decimal=1.96, recorded_at=NOW, source="the_odds_api",
    ))
    db_session.commit()

    case = build_cases([_alert(match_id=match.id, player_id=player.id, home_team=home.name, away_team=away.name)])[0]
    ranked = _critical_ranked_case(case)
    freeze_or_refresh_case_snapshots(db_session, [ranked], now=NOW)
    match.status = MatchStatus.COMPLETED
    db_session.commit()

    first_pass = settle_case_snapshots(db_session, now=NOW + timedelta(hours=3))
    assert first_pass == 1
    resolved_at_first = db_session.scalar(select(AnomalyCaseSnapshot).where(AnomalyCaseSnapshot.case_id == case.case_id)).resolved_at

    second_pass = settle_case_snapshots(db_session, now=NOW + timedelta(hours=10))
    assert second_pass == 0
    assert db_session.scalar(select(AnomalyCaseSnapshot).where(AnomalyCaseSnapshot.case_id == case.case_id)).resolved_at == resolved_at_first
