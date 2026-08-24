"""Targeted tests for the B2B Market Anomaly / Trading QA Engine
(app/market_monitor/*): monotonicity, adjacent-threshold jumps (local-
neighbour comparison, not global), context/lineup staleness, exchange
exclusion in the reused consensus engine, exact-market comparison (never
mixing non-equivalent lines/thresholds), and anomaly-snapshot immutability."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.market_monitor.curve_integrity import CurvePoint, check_monotonicity, find_adjacent_jumps
from app.market_monitor.context_staleness import check_context_item_staleness, check_lineup_staleness
from app.market_monitor.team_consistency import check_pair_consistency
from app.market_monitor.movement import build_bookmaker_series, detect_movement_anomalies
from app.market_monitor.detector import detect_player_family_anomalies, _Common
from app.pricing.player_pricing import price_disposals
from app.market_monitor.snapshot_service import evaluate_anomaly_snapshots, freeze_alert, freeze_anomaly_alerts
from app.market_monitor.types import MODEL_VS_MARKET_DIVERGENCE, Alert
from app.models import (
    AnomalyAlertSnapshot,
    Bookmaker,
    Match,
    MatchStatus,
    Player,
    PlayerDisposalProjection,
    PlayerModelRun,
    PlayerPropMarket,
    Round,
    Season,
    Sport,
    Team,
)
from app.models.bookmaker import ELIGIBILITY_INFORMATIONAL
from app.player_modelling.market import PlayerMarket

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _seed_match(db, *, status=MatchStatus.SCHEDULED):
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
        scheduled_start=NOW + timedelta(days=2), status=status,
    )
    db.add(match)
    db.commit()
    return match, home, away


def _seed_disposal_projection(db, match, home, *, mean=25.0, alpha=1.5):
    player = Player(sport_id=match.sport_id, display_name="Test Player", source="afltables", source_player_id="mm-p1", current_team_id=home.id)
    db.add(player)
    db.flush()
    db.add(PlayerModelRun(
        model_name="disposals_test", market=PlayerMarket.DISPOSALS.value, feature_names=[], config_json={},
        distribution_method="nb", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019,
        evaluation_end_year=2025, is_promoted=True, run_at=NOW,
    ))
    row = PlayerDisposalProjection(
        match_id=match.id, player_id=player.id, team_id=home.id, model_name="disposals_test", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
        predicted_mean=mean, distribution_method="nb", nb_alpha=alpha, confidence_tier="higher_confidence",
        warnings=[], input_features={},
    )
    db.add(row)
    db.commit()
    return player, row


def _bookmaker(db, name, eligibility=None):
    b = Bookmaker(name=name)
    if eligibility is not None:
        b.eligibility = eligibility
    db.add(b)
    db.commit()
    return b


# --- Curve integrity ---------------------------------------------------------


def test_monotonic_curve_has_no_violations():
    points = [CurvePoint(15.5, 0.80), CurvePoint(20.5, 0.45), CurvePoint(25.5, 0.15), CurvePoint(30.5, 0.03)]
    result = check_monotonicity(points)
    assert result.is_monotonic
    assert result.violations == []


def test_non_monotonic_curve_flags_the_inverted_pair():
    points = [CurvePoint(15.5, 0.80), CurvePoint(20.5, 0.45), CurvePoint(25.5, 0.50), CurvePoint(30.5, 0.03)]
    result = check_monotonicity(points)
    assert not result.is_monotonic
    assert len(result.violations) == 1
    lo, hi = result.violations[0]
    assert lo.threshold == 20.5 and hi.threshold == 25.5


def test_tiny_inversion_within_noise_floor_is_not_flagged():
    points = [CurvePoint(15.5, 0.500), CurvePoint(20.5, 0.501)]  # 0.001 < NON_MONOTONIC_MIN_PP
    assert check_monotonicity(points).is_monotonic


def test_smooth_geometric_decay_produces_no_jump_despite_large_first_gap():
    """The exact false-positive this design deliberately avoids: a smooth
    survival-function-shaped curve where the first gap is much bigger than
    later gaps purely from curve shape (the distribution's mode sitting
    near the low end), not a genuine local anomaly."""
    points = [CurvePoint(15.5, 0.70), CurvePoint(20.5, 0.30), CurvePoint(25.5, 0.10), CurvePoint(30.5, 0.03), CurvePoint(35.5, 0.01)]
    assert find_adjacent_jumps(points) == []


def test_genuine_local_spike_is_flagged():
    """One threshold's gap is way out of line with BOTH its immediate
    neighbours, not just decaying curve shape."""
    points = [CurvePoint(15.5, 0.60), CurvePoint(20.5, 0.55), CurvePoint(25.5, 0.10), CurvePoint(30.5, 0.06), CurvePoint(35.5, 0.02)]
    jumps = find_adjacent_jumps(points)
    assert len(jumps) == 1
    assert jumps[0].lower.threshold == 20.5 and jumps[0].upper.threshold == 25.5


def test_two_point_curve_never_produces_a_jump():
    assert find_adjacent_jumps([CurvePoint(15.5, 0.5), CurvePoint(20.5, 0.2)]) == []


# --- Context/lineup staleness ------------------------------------------------


class _FakeLineup:
    def __init__(self, selection_status, recorded_at, source_timestamp=None):
        self.selection_status = selection_status
        self.recorded_at = recorded_at
        self.source_timestamp = source_timestamp


def test_lineup_staleness_fires_when_notable_event_postdates_quote():
    lineup = _FakeLineup("confirmed_out", NOW)
    result = check_lineup_staleness(lineup, latest_quote_at=NOW - timedelta(hours=2))
    assert result.is_stale
    assert result.context_event_at == NOW


def test_lineup_staleness_does_not_fire_when_quote_is_newer():
    lineup = _FakeLineup("confirmed_out", NOW)
    result = check_lineup_staleness(lineup, latest_quote_at=NOW + timedelta(hours=1))
    assert not result.is_stale


def test_routine_lineup_status_is_not_a_notable_event():
    lineup = _FakeLineup("named_in_squad", NOW)
    result = check_lineup_staleness(lineup, latest_quote_at=NOW - timedelta(days=3))
    assert not result.is_stale  # NAMED_IN_SQUAD churn isn't a real "event" per item 4


class _FakeContextItem:
    def __init__(self, context_type, summary, recorded_at, source_timestamp=None):
        self.context_type = context_type
        self.summary = summary
        self.recorded_at = recorded_at
        self.source_timestamp = source_timestamp


def test_context_staleness_fires_when_context_item_postdates_quote():
    item = _FakeContextItem("injury", "Hamstring concern", NOW)
    result = check_context_item_staleness([item], latest_quote_at=NOW - timedelta(hours=5))
    assert result.is_stale
    assert "Hamstring concern" in result.context_description


def test_context_staleness_no_items_is_never_stale():
    assert not check_context_item_staleness([], latest_quote_at=NOW).is_stale


# --- Team-market internal consistency ----------------------------------------


def test_arbable_pair_is_flagged_critical_signal():
    result = check_pair_consistency("SportsBet", 2.20, 2.20)  # implied 0.4545+0.4545=0.909 < 1.0
    assert result.is_inconsistent
    assert result.combined_probability < 1.0


def test_normal_margin_pair_is_not_flagged():
    result = check_pair_consistency("SportsBet", 1.90, 2.10)  # a plausible real two-sided book
    assert not result.is_inconsistent


def test_implausibly_wide_pair_is_flagged():
    result = check_pair_consistency("SportsBet", 1.05, 1.05)  # combined ~1.90, not a real AFL margin
    assert result.is_inconsistent
    assert result.combined_probability > 1.20


# --- Movement -----------------------------------------------------------------


class _FakeQuote:
    def __init__(self, bookmaker_id, price_decimal, recorded_at):
        self.bookmaker_id = bookmaker_id
        self.price_decimal = price_decimal
        self.recorded_at = recorded_at


def test_sharp_consensus_move_detected_when_all_books_drift_together():
    quotes = []
    for bid in (1, 2, 3):
        quotes.append(_FakeQuote(bid, 2.00, NOW))  # ~0.50 implied
        quotes.append(_FakeQuote(bid, 1.50, NOW + timedelta(hours=6)))  # ~0.667 implied -> ~16.7pp move
    series = build_bookmaker_series(quotes, bookmaker_name_by_id={1: "A", 2: "B", 3: "C"})
    findings = detect_movement_anomalies(series)
    assert any(f.kind == "sharp_consensus_move" for f in findings)


def test_one_bookmaker_diverges_while_others_stay_put():
    """3 stable books dilute the mean enough that the lone mover's own
    ~5.6pp move doesn't itself drag the consensus average past the
    "stable" floor - realistic behaviour: with too few books, one mover
    IS the market, and the right classification becomes "sharp consensus
    move," not "one book diverging" (see the module docstring)."""
    quotes = [
        _FakeQuote(1, 2.00, NOW), _FakeQuote(1, 1.80, NOW + timedelta(hours=6)),  # mover: 0.500 -> 0.556 (~5.6pp)
        _FakeQuote(2, 2.00, NOW), _FakeQuote(2, 2.01, NOW + timedelta(hours=6)),  # stable
        _FakeQuote(3, 2.00, NOW), _FakeQuote(3, 1.99, NOW + timedelta(hours=6)),  # stable
        _FakeQuote(4, 2.00, NOW), _FakeQuote(4, 2.00, NOW + timedelta(hours=6)),  # stable
    ]
    series = build_bookmaker_series(quotes, bookmaker_name_by_id={1: "Mover", 2: "B", 3: "C", 4: "D"})
    findings = detect_movement_anomalies(series)
    diverging = [f for f in findings if f.kind == "bookmaker_diverges"]
    assert len(diverging) == 1
    assert diverging[0].bookmaker.bookmaker_name == "Mover"


def test_no_findings_with_fewer_than_two_bookmakers():
    series = build_bookmaker_series([_FakeQuote(1, 2.0, NOW), _FakeQuote(1, 1.9, NOW + timedelta(hours=1))], bookmaker_name_by_id={1: "A"})
    assert detect_movement_anomalies(series) == []


# --- Exact-market comparison (never mixing non-equivalent lines) -----------


def test_exact_market_comparison_never_mixes_non_equivalent_thresholds(db_session):
    """A bookmaker quote exists ONLY at 20.5 - the detector must never
    compare it against the model's price at 15.5/25.5/30.5/35.5, and must
    never invent a market-side divergence alert at a threshold nobody
    actually quoted."""
    match, home, away = _seed_match(db_session)
    player, row = _seed_disposal_projection(db_session, match, home, mean=25.0, alpha=1.5)
    bookmaker = _bookmaker(db_session, "SportsBet")
    db_session.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
        line_type="over_under", threshold=20.5, selection="over", price_decimal=1.60, recorded_at=NOW, source="the_odds_api",
    ))
    db_session.commit()

    common = _Common(match_id=match.id, home_team=home.name, away_team=away.name, generated_at=NOW)
    alerts = detect_player_family_anomalies(db_session, price_disposals(db_session, row), common, PlayerMarket.DISPOSALS.value)

    divergence_thresholds = {a.threshold for a in alerts if a.alert_type == MODEL_VS_MARKET_DIVERGENCE}
    assert divergence_thresholds <= {20.5}  # never any of the other preset thresholds, which have no real market


def test_exchange_bookmaker_excluded_from_consensus_and_outlier(db_session):
    """An exchange (informational_only eligibility) quoting a wildly
    different price must never feed the consensus mean or count toward
    the outlier check - see bookmaker_classification.py's eligibility
    rule, reused unchanged by this stage's consensus engine."""
    match, home, away = _seed_match(db_session)
    player, row = _seed_disposal_projection(db_session, match, home, mean=25.0, alpha=1.5)
    included_1 = _bookmaker(db_session, "SportsBet")
    included_2 = _bookmaker(db_session, "TAB")
    included_3 = _bookmaker(db_session, "Ladbrokes")
    exchange = _bookmaker(db_session, "Betfair Exchange", eligibility=ELIGIBILITY_INFORMATIONAL)
    for bm, price in ((included_1, 1.60), (included_2, 1.62), (included_3, 1.58), (exchange, 3.50)):
        db_session.add(PlayerPropMarket(
            match_id=match.id, player_id=player.id, bookmaker_id=bm.id, market_type=PlayerMarket.DISPOSALS.value,
            line_type="over_under", threshold=20.5, selection="over", price_decimal=price, recorded_at=NOW, source="the_odds_api",
        ))
    db_session.commit()

    from app.pricing.market_intelligence import player_market_intelligence

    intel = player_market_intelligence(db_session, match.id, player.id, PlayerMarket.DISPOSALS.value, "over_under", 20.5, 0.5)
    assert intel.n_bookmakers == 3  # exchange never counted
    assert intel.consensus.n_bookmakers == 3
    assert all(b.bookmaker_name != "Betfair Exchange" for b in intel.consensus.per_bookmaker)
    # The exchange's much longer price is still visible in the raw book list (disclosed, never hidden) ...
    assert any(b.bookmaker_name == "Betfair Exchange" for b in intel.books)
    # ... but never becomes "the" best/consensus price used for comparison.
    assert intel.best_bookmaker != "Betfair Exchange"


# --- Anomaly snapshot immutability -------------------------------------------


def _sample_alert(match_id, home, away) -> Alert:
    return Alert(
        alert_type=MODEL_VS_MARKET_DIVERGENCE, severity="warning", reason_code="divergence_10.0pp",
        detail="test alert", match_id=match_id, home_team=home, away_team=away, player_id=None, player_name=None,
        team_id=None, market_type="h2h", selection=home, threshold=None, line_value=None, model_probability=0.60,
        model_fair_odds=1.67, market_consensus_probability=0.50, bookmaker_prices=[], freshness="fresh",
        model_version="v1", lineup_status=None, context_state=None, model_risk_flags=[], generated_at=NOW,
    )


def test_freeze_alert_is_idempotent_and_never_duplicated(db_session):
    match, home, away = _seed_match(db_session)
    alert = _sample_alert(match.id, home.name, away.name)

    first = freeze_alert(db_session, alert)
    db_session.commit()
    assert first is not None

    second = freeze_alert(db_session, alert)  # same identity, re-detected
    db_session.commit()
    assert second is None

    rows = db_session.scalars(select(AnomalyAlertSnapshot).where(AnomalyAlertSnapshot.match_id == match.id)).all()
    assert len(rows) == 1
    assert rows[0].detail == "test alert"


def test_freeze_anomaly_alerts_only_freezes_scheduled_matches(db_session):
    match, home, away = _seed_match(db_session, status=MatchStatus.COMPLETED)
    _seed_disposal_projection(db_session, match, home)
    n = freeze_anomaly_alerts(db_session, [match.id])
    assert n == 0  # a completed match is no longer "before kickoff"


def test_evaluate_settles_once_and_never_revisits(db_session):
    match, home, away = _seed_match(db_session, status=MatchStatus.SCHEDULED)
    alert = _sample_alert(match.id, home.name, away.name)
    freeze_alert(db_session, alert)
    db_session.commit()

    # Not yet completed - nothing settles.
    n = evaluate_anomaly_snapshots(db_session)
    assert n == 0
    snap = db_session.scalars(select(AnomalyAlertSnapshot).where(AnomalyAlertSnapshot.match_id == match.id)).first()
    assert snap.evaluated_at is None

    match.status = MatchStatus.COMPLETED
    db_session.commit()
    n = evaluate_anomaly_snapshots(db_session)
    assert n == 1
    db_session.refresh(snap)
    first_evaluated_at = snap.evaluated_at
    assert first_evaluated_at is not None

    # Re-running must never re-settle an already-evaluated snapshot.
    n_again = evaluate_anomaly_snapshots(db_session)
    assert n_again == 0
    db_session.refresh(snap)
    assert snap.evaluated_at == first_evaluated_at
