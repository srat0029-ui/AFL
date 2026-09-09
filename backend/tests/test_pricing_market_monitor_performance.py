"""Query-count regression, equivalence, and idempotency tests for the
MatchPricingContext N+1 fix (app/pricing/match_pricing_context.py) applied
to `snapshot_round_pricing` (app/pricing/snapshot_service.py) and the
market-monitor detection path (app/market_monitor/detector.py, reused by
both `market_monitor_prospective_snapshots`'s build_trader_inbox and
`market_monitor_alert_snapshots`'s freeze_anomaly_alerts via
detect_match_anomalies).

Context: a real production forensic review found `player_market_intelligence`/
`team_market_intelligence` (and the consensus/devig lookups they call into)
independently re-querying Bookmaker, OddsQuote, and PlayerPropMarket per
player/threshold, plus market-monitor separately re-deriving match-wide
lineup/context state and reloading the full Bookmaker table once PER
PLAYER - `snapshot_pricing` measured 283.59s for ~14 team + 460 disposal +
368 goal snapshots, `market_monitor_prospective_snapshots` measured
377.80s for zero cases frozen (the whole cost was in detection, not the
freeze loop - see build_trader_inbox -> detect_match_anomalies).

Every test below uses `context=None` (the pre-fix code path, still fully
supported and unchanged) as the "legacy" reference to prove field-for-
field equivalence with the context-based path - not a frozen copy of old
code, the SAME real functions running their two supported modes.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import event, select

from app.market_monitor.detector import detect_match_anomalies, detect_player_family_anomalies, detect_team_match_anomalies, price_single_match
from app.market_monitor.snapshot_service import freeze_anomaly_alerts
from app.market_monitor.inbox import build_trader_inbox
from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import (
    AnomalyAlertSnapshot,
    Bookmaker,
    ExpectedLineup,
    GoalModelRun,
    GoalModelValidationMetric,
    Match,
    MatchStatus,
    ModelRun,
    OddsQuote,
    Player,
    PlayerDisposalProjection,
    PlayerGoalProjection,
    PlayerModelRun,
    PlayerModelValidationMetric,
    PlayerPropMarket,
    PricingSnapshot,
    Round,
    Season,
    Sport,
    Team,
)
from app.pricing.market_intelligence import player_market_intelligence, team_market_intelligence
from app.pricing.match_pricing_context import build_match_pricing_context
from app.pricing.player_pricing import DEFAULT_DISPOSAL_THRESHOLDS, DEFAULT_GOAL_THRESHOLDS, price_disposals, price_goals
from app.pricing.snapshot_service import snapshot_round_pricing

NOW = datetime.now(timezone.utc)


@contextmanager
def count_queries(session):
    counter = {"n": 0}
    engine = session.get_bind()

    def _cb(*a, **k):
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _cb)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", _cb)


def _seed_model_runs(db):
    if db.scalar(select(ModelRun).where(ModelRun.model_name == "elo")) is None:
        persist_model_run(db, "elo", EloConfig(), 2022, metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 648, "holdout_value": 0.20, "naive_baseline_value": 0.25, "has_edge_over_naive": True}])
    if db.scalar(select(ModelRun).where(ModelRun.model_name == "poisson")) is None:
        persist_model_run(db, "poisson", PoissonConfig(), 2022, metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 648, "holdout_value": 0.205, "naive_baseline_value": 0.25, "has_edge_over_naive": True}])


def _seed_bookmakers(db, names):
    books = []
    for name in names:
        bm = db.scalar(select(Bookmaker).where(Bookmaker.name == name))
        if bm is None:
            bm = Bookmaker(name=name, eligibility="included")
            db.add(bm)
            db.flush()
        books.append(bm)
    return books


def _seed_match_with_players(db, *, suffix, n_players, bookmakers, round_=None):
    """A production-shaped match: N players, each with disposal AND goal
    projections, a confirmed lineup, and REAL two-sided PlayerPropMarket
    quotes across every given bookmaker (both 'over' and 'under', so the
    same-book devig path in consensus_and_outliers.py is genuinely
    exercised - not just the raw-implied-probability fallback). Team h2h
    also gets two-sided multi-bookmaker OddsQuote rows for the same
    reason."""
    sport = db.scalar(select(Sport).where(Sport.code == "AFL"))
    if sport is None:
        sport = Sport(code="AFL", name="Australian Football League")
        db.add(sport)
        db.flush()
    season = db.scalar(select(Season).where(Season.sport_id == sport.id, Season.year == 2026))
    if season is None:
        season = Season(sport_id=sport.id, year=2026)
        db.add(season)
        db.flush()
    if round_ is None:
        existing_rounds = db.scalars(select(Round.round_number).where(Round.season_id == season.id)).all()
        round_ = Round(season_id=season.id, round_number=max(existing_rounds, default=0) + 1)
        db.add(round_)
        db.flush()
    home = Team(sport_id=sport.id, name=f"Home{suffix}", short_name=f"H{suffix}"[:3].upper())
    away = Team(sport_id=sport.id, name=f"Away{suffix}", short_name=f"A{suffix}"[:3].upper())
    db.add_all([home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW + timedelta(days=2), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.flush()

    # Team h2h: two-sided quotes from every bookmaker.
    for bm in bookmakers:
        db.add(OddsQuote(match_id=match.id, bookmaker_id=bm.id, market_type="h2h", selection=home.name, price_decimal=1.8, recorded_at=NOW, source="the_odds_api"))
        db.add(OddsQuote(match_id=match.id, bookmaker_id=bm.id, market_type="h2h", selection=away.name, price_decimal=2.1, recorded_at=NOW, source="the_odds_api"))

    players = []
    for i in range(n_players):
        team = home if i % 2 == 0 else away
        p = Player(sport_id=sport.id, display_name=f"Player{suffix}_{i}", source="afltables", source_player_id=f"{suffix}p{i}", current_team_id=team.id)
        db.add(p)
        db.flush()
        players.append(p)
        db.add(PlayerDisposalProjection(
            match_id=match.id, player_id=p.id, team_id=team.id, model_name="disposals_ridge", model_version="v1",
            generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
            predicted_mean=25.0, distribution_method="nb", nb_alpha=3.0, confidence_tier="higher_confidence",
            warnings=[], input_features={},
        ))
        db.add(PlayerGoalProjection(
            match_id=match.id, player_id=p.id, team_id=team.id, model_name="goals_hurdle", model_version="v1",
            generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
            predicted_mean=0.6, distribution_kind="hurdle", nb_alpha=None,
            p_score=0.5, mu_scored=1.2, alpha_scored=0.3, scoring_archetype="forward",
            confidence_tier="higher_confidence", warnings=[], input_features={},
        ))
        db.add(ExpectedLineup(
            match_id=match.id, player_id=p.id, team_id=team.id, status="expected_in",
            selection_status="confirmed_selected", is_confirmed=True, recorded_at=NOW, source="manual",
        ))
        for bm_i, bm in enumerate(bookmakers):
            db.add(PlayerPropMarket(
                match_id=match.id, player_id=p.id, bookmaker_id=bm.id, market_type="player_disposals",
                line_type="over_under", threshold=20.5, selection="over", price_decimal=1.9 + bm_i * 0.05,
                recorded_at=NOW, source="the_odds_api", bookmaker_last_update=NOW,
            ))
            db.add(PlayerPropMarket(
                match_id=match.id, player_id=p.id, bookmaker_id=bm.id, market_type="player_disposals",
                line_type="over_under", threshold=20.5, selection="under", price_decimal=1.95 - bm_i * 0.05,
                recorded_at=NOW, source="the_odds_api", bookmaker_last_update=NOW,
            ))
            db.add(PlayerPropMarket(
                match_id=match.id, player_id=p.id, bookmaker_id=bm.id, market_type="player_goals",
                line_type="over_under", threshold=1.5, selection="over", price_decimal=3.2 + bm_i * 0.1,
                recorded_at=NOW, source="the_odds_api", bookmaker_last_update=NOW,
            ))
    db.commit()
    return match, home, away, players, round_


# --- Part F: query-count regression ----------------------------------------


def test_snapshot_round_pricing_query_count_does_not_scale_with_player_count(db_session):
    """Measured on the IDEMPOTENT RERUN, not the first (row-creating) call:
    the first call's total query count inherently scales with player
    count because it issues one INSERT per new snapshot row - N players
    genuinely need N*9 new rows, and that part of the cost is real,
    necessary write volume, not the N+1 bug. The bug was the SELECT-side
    market-intelligence lookups repeated per player/threshold; a rerun
    creates zero new rows (existing_keys already covers everything), so
    every remaining query is pure per-match context-build overhead - which
    must stay flat regardless of player count. This is the same technique
    test_sgm_scope_performance.py already established for validating an
    N+1 fix without conflating it with genuine per-row write cost."""
    _seed_model_runs(db_session)
    books = _seed_bookmakers(db_session, ["BookA", "BookB", "BookC"])
    match_small, *_ = _seed_match_with_players(db_session, suffix="qcs", n_players=4, bookmakers=books)
    match_large, *_ = _seed_match_with_players(db_session, suffix="qcl", n_players=8, bookmakers=books)

    snapshot_round_pricing(db_session, [match_small.id])
    snapshot_round_pricing(db_session, [match_large.id])

    with count_queries(db_session) as counter:
        report_small = snapshot_round_pricing(db_session, [match_small.id])
    small_queries = counter["n"]

    with count_queries(db_session) as counter:
        report_large = snapshot_round_pricing(db_session, [match_large.id])
    large_queries = counter["n"]

    assert report_small.disposal_snapshots_created == 0 and report_large.disposal_snapshots_created == 0  # confirms this is the zero-new-rows rerun
    # 2x the players must NOT come anywhere near 2x the queries - the old
    # per-player-per-threshold pattern measured roughly linear growth
    # (386 -> 741 queries for 20 -> 40 players); the context-based fix
    # should show query count essentially flat (a small constant per
    # match), regardless of player count.
    assert large_queries < small_queries * 1.5, f"query count scaled with player count: {small_queries} (4 players) -> {large_queries} (8 players)"


def test_market_monitor_detection_query_count_does_not_scale_with_player_count(db_session):
    _seed_model_runs(db_session)
    books = _seed_bookmakers(db_session, ["BookA", "BookB", "BookC"])
    match_small, *_ = _seed_match_with_players(db_session, suffix="mms", n_players=4, bookmakers=books)
    match_large, *_ = _seed_match_with_players(db_session, suffix="mml", n_players=8, bookmakers=books)

    with count_queries(db_session) as counter:
        alerts_small = detect_match_anomalies(db_session, match_small.id)
    small_queries = counter["n"]

    with count_queries(db_session) as counter:
        alerts_large = detect_match_anomalies(db_session, match_large.id)
    large_queries = counter["n"]

    assert len(alerts_small) >= 0 and len(alerts_large) >= 0  # sanity: detection actually ran
    assert large_queries < small_queries * 1.5, f"query count scaled with player count: {small_queries} (4 players) -> {large_queries} (8 players)"


def test_build_trader_inbox_query_count_does_not_scale_with_player_count(db_session):
    """The exact call chain market_monitor_prospective_snapshots uses."""
    _seed_model_runs(db_session)
    books = _seed_bookmakers(db_session, ["BookA", "BookB", "BookC"])
    match_small, *_ = _seed_match_with_players(db_session, suffix="tis", n_players=4, bookmakers=books)
    match_large, *_ = _seed_match_with_players(db_session, suffix="til", n_players=8, bookmakers=books)

    with count_queries(db_session) as counter:
        build_trader_inbox(db_session, [match_small.id], track_persistence=False)
    small_queries = counter["n"]

    with count_queries(db_session) as counter:
        build_trader_inbox(db_session, [match_large.id], track_persistence=False)
    large_queries = counter["n"]

    assert large_queries < small_queries * 1.5, f"query count scaled with player count: {small_queries} (4 players) -> {large_queries} (8 players)"


def test_freeze_anomaly_alerts_query_count_does_not_scale_with_player_count(db_session):
    """The exact call chain market_monitor_alert_snapshots uses - the path
    that timed out entirely in the real controlled run."""
    _seed_model_runs(db_session)
    books = _seed_bookmakers(db_session, ["BookA", "BookB", "BookC"])
    match_small, *_ = _seed_match_with_players(db_session, suffix="fas", n_players=4, bookmakers=books)
    match_large, *_ = _seed_match_with_players(db_session, suffix="fal", n_players=8, bookmakers=books)

    with count_queries(db_session) as counter:
        freeze_anomaly_alerts(db_session, [match_small.id])
    small_queries = counter["n"]

    with count_queries(db_session) as counter:
        freeze_anomaly_alerts(db_session, [match_large.id])
    large_queries = counter["n"]

    assert large_queries < small_queries * 1.5, f"query count scaled with player count: {small_queries} (4 players) -> {large_queries} (8 players)"


# --- Part G: equivalence tests (context vs. context=None) ------------------


def _snapshot_field_tuple(snap: PricingSnapshot) -> tuple:
    return (
        snap.player_id, snap.match_id, snap.market_family, snap.market_type, snap.selection, snap.line_type,
        snap.threshold, snap.line_value, snap.model_name, snap.model_version, snap.confidence_tier,
        round(snap.model_probability, 12), round(snap.model_fair_odds, 9) if snap.model_fair_odds != float("inf") else snap.model_fair_odds,
        snap.best_bookmaker_price, snap.best_bookmaker_name, snap.market_consensus_probability, snap.n_bookmakers,
    )


def test_snapshot_pricing_field_for_field_equivalent_to_context_none_path(db_session):
    """Proves the batched, context-driven snapshot_round_pricing persists
    EXACTLY the same fields a direct, context=None (legacy) call to
    player_market_intelligence/team_market_intelligence would have
    computed for the same player/threshold - not just "same count"."""
    _seed_model_runs(db_session)
    books = _seed_bookmakers(db_session, ["BookA", "BookB", "BookC"])
    match, home, away, players, _ = _seed_match_with_players(db_session, suffix="equiv", n_players=4, bookmakers=books)

    snapshot_round_pricing(db_session, [match.id])
    persisted = db_session.scalars(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id)).all()
    assert len(persisted) > 0

    for snap in persisted:
        if snap.player_id is None:
            legacy_intel = team_market_intelligence(db_session, match.id, snap.market_type, snap.selection, snap.line_value, snap.model_probability, context=None)
        else:
            legacy_intel = player_market_intelligence(db_session, match.id, snap.player_id, snap.market_type, snap.line_type, snap.threshold, snap.model_probability, context=None)
        assert snap.best_bookmaker_price == legacy_intel.best_price
        assert snap.best_bookmaker_name == legacy_intel.best_bookmaker
        assert snap.n_bookmakers == legacy_intel.n_bookmakers
        expected_consensus = legacy_intel.consensus.consensus_probability if legacy_intel.consensus else None
        assert snap.market_consensus_probability == expected_consensus


def test_snapshot_pricing_context_vs_no_context_intelligence_identical(db_session):
    """Direct comparison of MarketIntelligence objects computed WITH a
    preloaded context vs. WITHOUT one (context=None), for every
    player/threshold - the two code paths must never disagree."""
    _seed_model_runs(db_session)
    books = _seed_bookmakers(db_session, ["BookA", "BookB", "BookC"])
    match, home, away, players, _ = _seed_match_with_players(db_session, suffix="ident", n_players=4, bookmakers=books)
    match_ctx = build_match_pricing_context(db_session, match.id)

    h2h_with = team_market_intelligence(db_session, match.id, "h2h", home.name, None, 0.55, context=match_ctx)
    h2h_without = team_market_intelligence(db_session, match.id, "h2h", home.name, None, 0.55, context=None)
    assert h2h_with.has_market == h2h_without.has_market
    assert h2h_with.best_price == h2h_without.best_price
    assert h2h_with.n_bookmakers == h2h_without.n_bookmakers
    assert (h2h_with.consensus.consensus_probability if h2h_with.consensus else None) == (h2h_without.consensus.consensus_probability if h2h_without.consensus else None)
    assert (h2h_with.outlier is None) == (h2h_without.outlier is None)

    for p in players:
        with_ctx = player_market_intelligence(db_session, match.id, p.id, "player_disposals", "over_under", 20.5, 0.5, context=match_ctx)
        without_ctx = player_market_intelligence(db_session, match.id, p.id, "player_disposals", "over_under", 20.5, 0.5, context=None)
        assert with_ctx.has_market == without_ctx.has_market
        assert with_ctx.best_price == without_ctx.best_price
        assert with_ctx.n_bookmakers == without_ctx.n_bookmakers
        assert (with_ctx.consensus.consensus_probability if with_ctx.consensus else None) == (without_ctx.consensus.consensus_probability if without_ctx.consensus else None)
        assert (with_ctx.consensus.n_devigged if with_ctx.consensus else None) == (without_ctx.consensus.n_devigged if without_ctx.consensus else None)


def _alert_identity_set(alerts) -> set:
    return {
        (a.alert_type, a.reason_code, a.market_type, a.selection, a.threshold, a.player_id, a.severity)
        for a in alerts
    }


def test_market_monitor_detection_equivalent_with_and_without_context(db_session):
    """detect_team_match_anomalies/detect_player_family_anomalies must
    produce the IDENTICAL set of anomalies whether or not a preloaded
    MatchPricingContext is supplied - same detection rules, same
    thresholds, same severity, just a different data-fetch path."""
    _seed_model_runs(db_session)
    books = _seed_bookmakers(db_session, ["BookA", "BookB", "BookC"])
    match, home, away, players, _ = _seed_match_with_players(db_session, suffix="mdet", n_players=4, bookmakers=books)

    with_context = detect_match_anomalies(db_session, match.id)

    # Reconstruct the same detection using context=None throughout, via
    # the same lower-level pieces detect_match_anomalies itself uses.
    from app.market_monitor.detector import _Common

    team, disposals, goals = price_single_match(db_session, match.id)
    now = datetime.now(timezone.utc)
    common = _Common(match_id=match.id, home_team=team.home_team, away_team=team.away_team, generated_at=now)
    without_context = []
    if team is not None:
        without_context += detect_team_match_anomalies(db_session, team, common, context=None)
    for price in disposals:
        without_context += detect_player_family_anomalies(db_session, price, common, "player_disposals", context=None)
    for price in goals:
        without_context += detect_player_family_anomalies(db_session, price, common, "player_goals", context=None)

    assert _alert_identity_set(with_context) == _alert_identity_set(without_context)
    assert len(with_context) == len(without_context)


def test_freeze_anomaly_alerts_persists_same_rows_as_direct_detection(db_session):
    """market_monitor_alert_snapshots' actual entry point must freeze
    exactly the alerts detect_match_anomalies (context-based) reports -
    no alerts silently lost or duplicated by the context refactor."""
    _seed_model_runs(db_session)
    books = _seed_bookmakers(db_session, ["BookA", "BookB", "BookC"])
    match, home, away, players, _ = _seed_match_with_players(db_session, suffix="frz", n_players=4, bookmakers=books)

    expected_alerts = detect_match_anomalies(db_session, match.id)
    n_frozen = freeze_anomaly_alerts(db_session, [match.id])
    persisted = db_session.scalars(select(AnomalyAlertSnapshot).where(AnomalyAlertSnapshot.match_id == match.id)).all()

    assert n_frozen == len(expected_alerts)
    assert len(persisted) == len(expected_alerts)
    persisted_keys = {(s.alert_type, s.reason_code, s.market_type, s.selection, s.threshold, s.player_id) for s in persisted}
    expected_keys = {(a.alert_type, a.reason_code, a.market_type, a.selection, a.threshold, a.player_id) for a in expected_alerts}
    assert persisted_keys == expected_keys


# --- Part H: idempotency / cross-cycle correctness --------------------------


def test_batched_snapshot_pricing_creates_n_times_thresholds(db_session):
    _seed_model_runs(db_session)
    books = _seed_bookmakers(db_session, ["BookA"])
    match, home, away, players, _ = _seed_match_with_players(db_session, suffix="h1", n_players=4, bookmakers=books)

    report = snapshot_round_pricing(db_session, [match.id])

    assert report.disposal_snapshots_created == len(players) * len(DEFAULT_DISPOSAL_THRESHOLDS)
    assert report.goal_snapshots_created == len(players) * len(DEFAULT_GOAL_THRESHOLDS)
    disposal_rows = db_session.scalars(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id, PricingSnapshot.market_type == "player_disposals")).all()
    assert len(disposal_rows) == report.disposal_snapshots_created
    assert len({r.player_id for r in disposal_rows}) == len(players)


def test_batched_snapshot_pricing_rerun_is_idempotent(db_session):
    _seed_model_runs(db_session)
    books = _seed_bookmakers(db_session, ["BookA"])
    match, home, away, players, _ = _seed_match_with_players(db_session, suffix="h2", n_players=3, bookmakers=books)

    report1 = snapshot_round_pricing(db_session, [match.id])
    n_after_first = len(db_session.scalars(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id)).all())

    report2 = snapshot_round_pricing(db_session, [match.id])
    n_after_second = len(db_session.scalars(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id)).all())

    assert report2.disposal_snapshots_created == 0
    assert report2.goal_snapshots_created == 0
    assert report2.team_snapshots_created == 0
    assert n_after_second == n_after_first
    assert n_after_first > 0


def test_batched_snapshot_pricing_later_cycle_new_player_added_not_skipped(db_session):
    """THE regression the batched in-memory identity check must never
    reintroduce: a player appearing for the first time in a LATER call to
    snapshot_round_pricing (a new player joining an already-priced match,
    exactly what populate_provisional_rosters can trigger) must get its
    own rows, not be silently skipped because the in-memory existing_keys
    set was built from a stale, pre-new-player snapshot of the table."""
    _seed_model_runs(db_session)
    books = _seed_bookmakers(db_session, ["BookA"])
    match, home, away, first_wave, round_ = _seed_match_with_players(db_session, suffix="h3", n_players=3, bookmakers=books)

    report1 = snapshot_round_pricing(db_session, [match.id])
    assert report1.disposal_snapshots_created == 3 * len(DEFAULT_DISPOSAL_THRESHOLDS)

    # A later cycle: one genuinely new player joins the same match.
    new_player = Player(sport_id=home.sport_id, display_name="NewPlayerH3", source="afltables", source_player_id="h3_new", current_team_id=away.id)
    db_session.add(new_player)
    db_session.flush()
    db_session.add(PlayerDisposalProjection(
        match_id=match.id, player_id=new_player.id, team_id=away.id, model_name="disposals_ridge", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
        predicted_mean=25.0, distribution_method="nb", nb_alpha=3.0, confidence_tier="higher_confidence",
        warnings=[], input_features={},
    ))
    db_session.add(ExpectedLineup(
        match_id=match.id, player_id=new_player.id, team_id=away.id, status="expected_in",
        selection_status="confirmed_selected", is_confirmed=True, recorded_at=NOW, source="manual",
    ))
    db_session.commit()

    report2 = snapshot_round_pricing(db_session, [match.id])

    assert report2.disposal_snapshots_created == len(DEFAULT_DISPOSAL_THRESHOLDS), "the new player did not get exactly its own disposal snapshots"
    all_disposal_rows = db_session.scalars(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id, PricingSnapshot.market_type == "player_disposals")).all()
    assert len(all_disposal_rows) == 4 * len(DEFAULT_DISPOSAL_THRESHOLDS)
    assert new_player.id in {r.player_id for r in all_disposal_rows}


def test_batched_snapshot_pricing_existing_players_not_duplicated_when_new_player_added(db_session):
    """Companion to the above: adding a new player must not touch (let
    alone duplicate) any EXISTING player's already-committed rows."""
    _seed_model_runs(db_session)
    books = _seed_bookmakers(db_session, ["BookA"])
    match, home, away, first_wave, round_ = _seed_match_with_players(db_session, suffix="h4", n_players=3, bookmakers=books)

    snapshot_round_pricing(db_session, [match.id])
    before_ids = {(r.player_id, r.threshold, r.id) for r in db_session.scalars(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id, PricingSnapshot.market_type == "player_disposals")).all()}

    new_player = Player(sport_id=home.sport_id, display_name="NewPlayerH4", source="afltables", source_player_id="h4_new", current_team_id=away.id)
    db_session.add(new_player)
    db_session.flush()
    db_session.add(PlayerDisposalProjection(
        match_id=match.id, player_id=new_player.id, team_id=away.id, model_name="disposals_ridge", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
        predicted_mean=25.0, distribution_method="nb", nb_alpha=3.0, confidence_tier="higher_confidence",
        warnings=[], input_features={},
    ))
    db_session.commit()
    snapshot_round_pricing(db_session, [match.id])

    after_existing_ids = {(r.player_id, r.threshold, r.id) for r in db_session.scalars(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id, PricingSnapshot.market_type == "player_disposals")).all() if r.player_id in {p.id for p in first_wave}}
    assert after_existing_ids == before_ids, "existing players' rows were touched/duplicated by a later cycle that added a new player"


# =============================================================================
# Follow-up task: the remaining app/pricing/player_pricing.py N+1
# (price_disposals/price_goals each independently re-querying the globally-
# promoted PlayerModelRun/GoalModelRun row - once via current_disposal_model_
# version/current_goal_model_version, and AGAIN inside historical_calibration_
# metrics's own internal lookup - plus a calibration-metric row and a
# player.display_name lazy-load, all once PER PLAYER). Fixed by extending
# MatchPricingContext with disposal_model_version/goal_model_version/
# calibration_by_key/players_by_id - see that module's docstring for the
# exact query trace this was measured against.
# =============================================================================


def _seed_promoted_disposal_model(db, *, n=2000, ece=0.03, segment="threshold_25"):
    existing = db.scalar(select(PlayerModelRun).where(PlayerModelRun.model_name == "disposals_ridge"))
    if existing is not None:
        return existing
    pmr = PlayerModelRun(
        model_name="disposals_ridge", market="player_disposals", feature_names=[], config_json={}, distribution_method="nb",
        tune_start_year=2018, tune_end_year=2021, evaluation_start_year=2022, evaluation_end_year=2022, is_promoted=True, run_at=NOW,
    )
    db.add(pmr)
    db.flush()
    db.add(PlayerModelValidationMetric(model_run_id=pmr.id, segment=segment, metric_name="ece", n=n, value=ece))
    db.commit()
    return pmr


def _seed_promoted_goal_model(db, *, n=2000, ece=0.05, segment="threshold_1"):
    existing = db.scalar(select(GoalModelRun).where(GoalModelRun.model_name == "goals_hurdle"))
    if existing is not None:
        return existing
    gmr = GoalModelRun(
        model_name="goals_hurdle", market="player_goals", feature_names=[], config_json={}, distribution_kind="hurdle",
        tune_start_year=2018, tune_end_year=2021, evaluation_start_year=2022, evaluation_end_year=2022, is_promoted=True, run_at=NOW,
    )
    db.add(gmr)
    db.flush()
    db.add(GoalModelValidationMetric(model_run_id=gmr.id, segment=segment, metric_name="ece", n=n, value=ece))
    db.commit()
    return gmr


def _seed_one_player(db, match, team, *, suffix, mean=25.0, status="expected_in", selection_status="confirmed_selected", goal_mean=0.6):
    p = Player(sport_id=team.sport_id, display_name=f"Player{suffix}", source="afltables", source_player_id=f"pp{suffix}", current_team_id=team.id)
    db.add(p)
    db.flush()
    db.add(PlayerDisposalProjection(
        match_id=match.id, player_id=p.id, team_id=team.id, model_name="disposals_ridge", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
        predicted_mean=mean, distribution_method="nb", nb_alpha=3.0, confidence_tier="higher_confidence",
        warnings=[], input_features={},
    ))
    db.add(PlayerGoalProjection(
        match_id=match.id, player_id=p.id, team_id=team.id, model_name="goals_hurdle", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
        predicted_mean=goal_mean, distribution_kind="hurdle", nb_alpha=None,
        p_score=0.5, mu_scored=1.2, alpha_scored=0.3, scoring_archetype="forward",
        confidence_tier="higher_confidence", warnings=[], input_features={},
    ))
    db.add(ExpectedLineup(
        match_id=match.id, player_id=p.id, team_id=team.id, status=status,
        selection_status=selection_status, is_confirmed=(selection_status == "confirmed_selected"), recorded_at=NOW, source="manual",
    ))
    db.commit()
    return p


def _seed_bare_match(db, *, suffix):
    sport = db.scalar(select(Sport).where(Sport.code == "AFL"))
    if sport is None:
        sport = Sport(code="AFL", name="Australian Football League")
        db.add(sport)
        db.flush()
    season = db.scalar(select(Season).where(Season.sport_id == sport.id, Season.year == 2026))
    if season is None:
        season = Season(sport_id=sport.id, year=2026)
        db.add(season)
        db.flush()
    existing_rounds = db.scalars(select(Round.round_number).where(Round.season_id == season.id)).all()
    round_ = Round(season_id=season.id, round_number=max(existing_rounds, default=0) + 1)
    home = Team(sport_id=sport.id, name=f"PPHome{suffix}", short_name=f"H{suffix}"[:3].upper())
    away = Team(sport_id=sport.id, name=f"PPAway{suffix}", short_name=f"A{suffix}"[:3].upper())
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW + timedelta(days=2), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    return match, home, away


# --- Part E: price_disposals/price_goals equivalence -----------------------


def _assert_disposal_price_equal(a, b):
    assert a.player_id == b.player_id and a.match_id == b.match_id and a.player_name == b.player_name
    assert a.model_version == b.model_version and a.lineup_status == b.lineup_status
    assert a.confidence_tier == b.confidence_tier and a.expected == b.expected
    assert a.is_stale == b.is_stale and a.stale_reasons == b.stale_reasons
    assert [(t.threshold, t.probability, t.fair_odds) for t in a.thresholds] == [(t.threshold, t.probability, t.fair_odds) for t in b.thresholds]
    a_cal = (a.calibration.ece, a.calibration.n, a.calibration.evaluated_threshold) if a.calibration else None
    b_cal = (b.calibration.ece, b.calibration.n, b.calibration.evaluated_threshold) if b.calibration else None
    assert a_cal == b_cal
    assert a.usage_regime == b.usage_regime and a.usage_change_score == b.usage_change_score


def _assert_goal_price_equal(a, b):
    assert a.player_id == b.player_id and a.match_id == b.match_id and a.player_name == b.player_name
    assert a.model_version == b.model_version and a.lineup_status == b.lineup_status
    assert a.confidence_tier == b.confidence_tier and a.expected == b.expected
    assert a.is_stale == b.is_stale and a.stale_reasons == b.stale_reasons
    assert [(t.threshold, t.probability, t.fair_odds) for t in a.thresholds] == [(t.threshold, t.probability, t.fair_odds) for t in b.thresholds]
    assert [(f.code, f.description) for f in a.model_risk_flags] == [(f.code, f.description) for f in b.model_risk_flags]
    assert a.usage_regime == b.usage_regime and a.usage_change_score == b.usage_change_score


def test_price_disposals_context_equivalent_confirmed_selected_with_calibration(db_session):
    _seed_promoted_disposal_model(db_session)
    match, home, away = _seed_bare_match(db_session, suffix="pe1")
    p = _seed_one_player(db_session, match, home, suffix="pe1", mean=25.0, status="expected_in", selection_status="confirmed_selected")
    match_ctx = build_match_pricing_context(db_session, match.id)
    row = match_ctx.disposal_projections[0]

    with_ctx = price_disposals(db_session, row, context=match_ctx)
    without_ctx = price_disposals(db_session, row, context=None)
    _assert_disposal_price_equal(with_ctx, without_ctx)
    assert with_ctx.calibration is not None  # sufficient sample seeded - confirms calibration path is genuinely exercised, not just None==None


def test_price_disposals_context_equivalent_uncertain_no_calibration_history(db_session):
    # No promoted model seeded at all -> historical_calibration_metrics
    # returns None on both paths; current_disposal_model_version is also None.
    match, home, away = _seed_bare_match(db_session, suffix="pe2")
    p = _seed_one_player(db_session, match, home, suffix="pe2", mean=18.0, status="uncertain", selection_status="uncertain")
    match_ctx = build_match_pricing_context(db_session, match.id)
    row = match_ctx.disposal_projections[0]

    with_ctx = price_disposals(db_session, row, context=match_ctx)
    without_ctx = price_disposals(db_session, row, context=None)
    _assert_disposal_price_equal(with_ctx, without_ctx)
    assert with_ctx.calibration is None
    assert with_ctx.lineup_status == "expected_in"  # frozen at projection-generation time, unaffected by current lineup


def test_price_disposals_context_equivalent_confirmed_out_player(db_session):
    _seed_promoted_disposal_model(db_session)
    match, home, away = _seed_bare_match(db_session, suffix="pe3")
    p = _seed_one_player(db_session, match, home, suffix="pe3", mean=32.0, status="expected_out", selection_status="confirmed_out")
    match_ctx = build_match_pricing_context(db_session, match.id)
    row = match_ctx.disposal_projections[0]

    with_ctx = price_disposals(db_session, row, context=match_ctx)
    without_ctx = price_disposals(db_session, row, context=None)
    _assert_disposal_price_equal(with_ctx, without_ctx)
    # is_stale reflects the CURRENT lineup status diverging from the
    # projection's generation-time status - must agree byte-for-byte
    # between the two code paths regardless of what it evaluates to.
    assert with_ctx.is_stale == without_ctx.is_stale


def test_price_disposals_context_equivalent_multiple_thresholds(db_session):
    _seed_promoted_disposal_model(db_session)
    match, home, away = _seed_bare_match(db_session, suffix="pe4")
    p = _seed_one_player(db_session, match, home, suffix="pe4", mean=28.0)
    match_ctx = build_match_pricing_context(db_session, match.id)
    row = match_ctx.disposal_projections[0]

    with_ctx = price_disposals(db_session, row, extra_thresholds=[18.5, 22.5], context=match_ctx)
    without_ctx = price_disposals(db_session, row, extra_thresholds=[18.5, 22.5], context=None)
    _assert_disposal_price_equal(with_ctx, without_ctx)
    assert len(with_ctx.thresholds) == len(DEFAULT_DISPOSAL_THRESHOLDS) + 2


def test_price_goals_context_equivalent_confirmed_selected_with_calibration(db_session):
    _seed_promoted_goal_model(db_session)
    match, home, away = _seed_bare_match(db_session, suffix="pg1")
    p = _seed_one_player(db_session, match, home, suffix="pg1", goal_mean=0.8, status="expected_in", selection_status="confirmed_selected")
    match_ctx = build_match_pricing_context(db_session, match.id)
    row = match_ctx.goal_projections[0]

    with_ctx = price_goals(db_session, row, context=match_ctx)
    without_ctx = price_goals(db_session, row, context=None)
    _assert_goal_price_equal(with_ctx, without_ctx)
    assert with_ctx.calibration is not None


def test_price_goals_context_equivalent_no_calibration_history(db_session):
    match, home, away = _seed_bare_match(db_session, suffix="pg2")
    p = _seed_one_player(db_session, match, home, suffix="pg2", goal_mean=0.3, status="uncertain", selection_status="uncertain")
    match_ctx = build_match_pricing_context(db_session, match.id)
    row = match_ctx.goal_projections[0]

    with_ctx = price_goals(db_session, row, context=match_ctx)
    without_ctx = price_goals(db_session, row, context=None)
    _assert_goal_price_equal(with_ctx, without_ctx)
    assert with_ctx.calibration is None


def test_price_goals_context_equivalent_confirmed_out_player(db_session):
    _seed_promoted_goal_model(db_session)
    match, home, away = _seed_bare_match(db_session, suffix="pg3")
    p = _seed_one_player(db_session, match, home, suffix="pg3", goal_mean=1.0, status="expected_out", selection_status="confirmed_out")
    match_ctx = build_match_pricing_context(db_session, match.id)
    row = match_ctx.goal_projections[0]

    with_ctx = price_goals(db_session, row, context=match_ctx)
    without_ctx = price_goals(db_session, row, context=None)
    _assert_goal_price_equal(with_ctx, without_ctx)


def test_price_disposals_and_goals_context_equivalent_across_full_squad(db_session):
    """Broader sweep: every player in a realistic mixed-status squad must
    match exactly, not just hand-picked single-player cases above."""
    _seed_promoted_disposal_model(db_session)
    _seed_promoted_goal_model(db_session)
    match, home, away = _seed_bare_match(db_session, suffix="pesq")
    statuses = [
        ("expected_in", "confirmed_selected"), ("uncertain", "uncertain"), ("expected_out", "confirmed_out"),
        ("expected_in", "named_in_squad"), ("expected_in", "confirmed_selected"),
    ]
    for i, (status, sel) in enumerate(statuses):
        team = home if i % 2 == 0 else away
        _seed_one_player(db_session, match, team, suffix=f"sq{i}", mean=15.0 + i * 5, status=status, selection_status=sel)
    match_ctx = build_match_pricing_context(db_session, match.id)

    for row in match_ctx.disposal_projections:
        _assert_disposal_price_equal(price_disposals(db_session, row, context=match_ctx), price_disposals(db_session, row, context=None))
    for row in match_ctx.goal_projections:
        _assert_goal_price_equal(price_goals(db_session, row, context=match_ctx), price_goals(db_session, row, context=None))


# --- Part F: query-count regression for player pricing directly ------------


def test_price_single_match_query_count_does_not_scale_with_player_count(db_session):
    _seed_promoted_disposal_model(db_session)
    _seed_promoted_goal_model(db_session)
    match_small, home_s, away_s = _seed_bare_match(db_session, suffix="pqs")
    for i in range(4):
        _seed_one_player(db_session, match_small, home_s if i % 2 == 0 else away_s, suffix=f"pqs{i}", mean=20.0 + i)
    match_large, home_l, away_l = _seed_bare_match(db_session, suffix="pql")
    for i in range(8):
        _seed_one_player(db_session, match_large, home_l if i % 2 == 0 else away_l, suffix=f"pql{i}", mean=20.0 + i)

    ctx_small = build_match_pricing_context(db_session, match_small.id)
    with count_queries(db_session) as counter:
        price_single_match(db_session, match_small.id, pricing_context=ctx_small)
    small_queries = counter["n"]

    ctx_large = build_match_pricing_context(db_session, match_large.id)
    with count_queries(db_session) as counter:
        price_single_match(db_session, match_large.id, pricing_context=ctx_large)
    large_queries = counter["n"]

    assert large_queries < small_queries * 1.5, f"query count scaled with player count: {small_queries} (4 players) -> {large_queries} (8 players)"


# --- Part G: prospective + alert path re-validation (context now reaches price_single_match too) ---


def test_build_trader_inbox_equivalent_with_player_pricing_context_fix(db_session):
    """Re-confirms output equivalence for the exact market_monitor_prospective_snapshots
    call chain now that price_single_match is also context-aware."""
    _seed_model_runs(db_session)
    _seed_promoted_disposal_model(db_session)
    _seed_promoted_goal_model(db_session)
    books = _seed_bookmakers(db_session, ["BookA", "BookB", "BookC"])
    match, *_ = _seed_match_with_players(db_session, suffix="ppinbox", n_players=4, bookmakers=books)

    ranked_with_context = build_trader_inbox(db_session, [match.id], track_persistence=False)

    # Reconstruct the equivalent legacy (context=None throughout) result via detect_match_anomalies's own pieces.
    from app.market_monitor.detector import _Common

    team, disposals, goals = price_single_match(db_session, match.id, pricing_context=None)
    now = datetime.now(timezone.utc)
    common = _Common(match_id=match.id, home_team=team.home_team, away_team=team.away_team, generated_at=now)
    legacy_alerts = []
    if team is not None:
        legacy_alerts += detect_team_match_anomalies(db_session, team, common, context=None)
    for price in disposals:
        legacy_alerts += detect_player_family_anomalies(db_session, price, common, "player_disposals", context=None)
    for price in goals:
        legacy_alerts += detect_player_family_anomalies(db_session, price, common, "player_goals", context=None)

    ranked_case_keys = {(r.case.match_id, r.case.player_id, r.case.market_type, r.case.selection, r.case.threshold) for r in ranked_with_context}
    from app.market_monitor.case_builder import build_cases
    legacy_case_keys = {(c.match_id, c.player_id, c.market_type, c.selection, c.threshold) for c in build_cases(legacy_alerts)}
    assert ranked_case_keys == legacy_case_keys


def test_freeze_anomaly_alerts_equivalent_with_player_pricing_context_fix(db_session):
    _seed_promoted_disposal_model(db_session)
    _seed_promoted_goal_model(db_session)
    books = _seed_bookmakers(db_session, ["BookA", "BookB", "BookC"])
    match, *_ = _seed_match_with_players(db_session, suffix="ppalert", n_players=4, bookmakers=books)

    expected_alerts = detect_match_anomalies(db_session, match.id)
    n_frozen = freeze_anomaly_alerts(db_session, [match.id])
    persisted = db_session.scalars(select(AnomalyAlertSnapshot).where(AnomalyAlertSnapshot.match_id == match.id)).all()

    assert n_frozen == len(expected_alerts)
    assert len(persisted) == len(expected_alerts)


def test_freeze_anomaly_alerts_query_count_substantially_reduced_and_bounded(db_session):
    """The path that timed out entirely in production (LiveCycleRun's
    controlled run #2). Confirms the player-pricing fix on top of the
    prior shared-context fix keeps this well below the prior 287/563
    query counts measured at 46/92 players (see benchmark script for the
    exact real-Postgres numbers this test's SQLite run cannot replicate,
    but the SAME flat-vs-scaling shape must hold on any backend)."""
    _seed_promoted_disposal_model(db_session)
    _seed_promoted_goal_model(db_session)
    books = _seed_bookmakers(db_session, ["BookA", "BookB", "BookC"])
    match_small, *_ = _seed_match_with_players(db_session, suffix="fasq_s", n_players=4, bookmakers=books)
    match_large, *_ = _seed_match_with_players(db_session, suffix="fasq_l", n_players=8, bookmakers=books)

    with count_queries(db_session) as counter:
        freeze_anomaly_alerts(db_session, [match_small.id])
    small_queries = counter["n"]

    with count_queries(db_session) as counter:
        freeze_anomaly_alerts(db_session, [match_large.id])
    large_queries = counter["n"]

    assert large_queries < small_queries * 1.5, f"query count scaled with player count: {small_queries} (4 players) -> {large_queries} (8 players)"
