"""Targeted tests for the B2B pricing engine (app/pricing/*): team-market
pricing at arbitrary lines, player threshold pricing built off the real
distribution reconstruction, fair-odds correctness, prospective-snapshot
idempotency, and settlement reuse. Reuses the same Elo/Poisson seed
pattern as test_edge_calculator.py rather than inventing a new one."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.edges.calculator import build_model_context
from app.edges.fair_odds import fair_odds_from_probability
from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import (
    Match,
    MatchStatus,
    Player,
    PlayerDisposalProjection,
    PlayerGoalProjection,
    PricingSnapshot,
    Round,
    Season,
    Sport,
    Team,
)
from app.pricing.player_pricing import USAGE_REGIME_CHANGE_FLAG, price_disposals, price_goals
from app.pricing.snapshot_service import settle_pricing_snapshots, snapshot_price
from app.pricing.team_pricing import price_team_market

NOW = datetime.now(timezone.utc)


def _seed_upcoming_match(db) -> Match:
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
        home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW + timedelta(days=2), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    return match


def _seed_model_runs(db) -> None:
    persist_model_run(
        db, "elo", EloConfig(), 2022,
        metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 648, "holdout_value": 0.20, "naive_baseline_value": 0.25, "has_edge_over_naive": True}],
    )
    persist_model_run(
        db, "poisson", PoissonConfig(), 2022,
        metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 648, "holdout_value": 0.205, "naive_baseline_value": 0.25, "has_edge_over_naive": True}],
    )


def test_team_price_home_draw_away_sum_to_one(db_session):
    match = _seed_upcoming_match(db_session)
    _seed_model_runs(db_session)
    context = build_model_context(db_session)

    price = price_team_market(match, context, NOW, NOW)

    total = price.home_win_probability + price.draw_probability + price.away_win_probability
    assert abs(total - 1.0) < 1e-9
    assert price.confidence_tier == "validated_edge_over_naive"


def test_team_price_fair_odds_is_inverse_of_probability(db_session):
    match = _seed_upcoming_match(db_session)
    _seed_model_runs(db_session)
    context = build_model_context(db_session)

    price = price_team_market(match, context, NOW, NOW)

    assert abs(price.home_fair_odds - fair_odds_from_probability(price.home_win_probability)) < 1e-9


def test_team_price_arbitrary_line_and_total_are_priced_on_request(db_session):
    match = _seed_upcoming_match(db_session)
    _seed_model_runs(db_session)
    context = build_model_context(db_session)

    price = price_team_market(match, context, NOW, NOW, line_values=[-12.5], total_lines=[165.5])

    assert len(price.lines) == 1
    assert price.lines[0].line_value == -12.5
    assert 0.0 <= price.lines[0].home_probability <= 1.0
    assert len(price.totals) == 1
    assert price.totals[0].line_value == 165.5
    # home + away probability for the SAME line don't have to sum to 1 in
    # general (line markets aren't a strict partition the way h2h is), but
    # over/under for the SAME total line must.
    assert abs(price.totals[0].over_probability + price.totals[0].under_probability - 1.0) < 1e-9


def test_no_lines_requested_means_no_lines_priced(db_session):
    """The engine can price ANY line on request - it must not guess a
    default set when the caller asks for none."""
    match = _seed_upcoming_match(db_session)
    _seed_model_runs(db_session)
    context = build_model_context(db_session)

    price = price_team_market(match, context, NOW, NOW)

    assert price.lines == []
    assert price.totals == []


def _seed_disposal_projection(db, match, mean=25.0, alpha=1.5) -> PlayerDisposalProjection:
    home = db.scalar(select(Team).where(Team.id == match.home_team_id))
    player = Player(sport_id=home.sport_id, display_name="Test Player", source="afltables", source_player_id="p1", current_team_id=home.id)
    db.add(player)
    db.flush()
    row = PlayerDisposalProjection(
        match_id=match.id, player_id=player.id, team_id=home.id, model_name="disposal_nb", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=20,
        predicted_mean=mean, distribution_method="nb", nb_alpha=alpha, confidence_tier="higher_confidence",
        warnings=[], input_features={},
    )
    db.add(row)
    db.commit()
    return row


def test_disposal_price_arbitrary_threshold_matches_distribution_directly(db_session):
    from app.player_modelling.disposal_distribution import NegativeBinomialDistribution

    match = _seed_upcoming_match(db_session)
    row = _seed_disposal_projection(db_session, match, mean=25.0, alpha=1.5)

    priced = price_disposals(db_session, row, extra_thresholds=[22.5])

    expected_dist = NegativeBinomialDistribution(mu=25.0, alpha=1.5)
    requested = next(t for t in priced.thresholds if t.threshold == 22.5)
    assert abs(requested.probability - expected_dist.prob_over(22.5)) < 1e-9
    assert priced.expected == 25.0
    # Preset thresholds are still included alongside the arbitrary one.
    assert any(t.threshold == 20.5 for t in priced.thresholds)


def test_disposal_price_fair_odds_matches_probability(db_session):
    match = _seed_upcoming_match(db_session)
    row = _seed_disposal_projection(db_session, match)

    priced = price_disposals(db_session, row)

    for t in priced.thresholds:
        if t.probability > 0:
            assert abs(t.fair_odds - fair_odds_from_probability(t.probability)) < 1e-9


def test_snapshot_price_is_idempotent_and_never_overwritten(db_session):
    match = _seed_upcoming_match(db_session)

    first = snapshot_price(
        db_session, match_id=match.id, player_id=None, market_family="team", market_type="h2h", selection="Carlton",
        line_type=None, threshold=None, line_value=None, model_name="elo_poisson", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status=None, confidence_tier="validated_edge_over_naive",
        model_probability=0.6,
    )
    db_session.commit()
    assert first is not None

    # Re-snapshotting the SAME market at the SAME model version must not
    # create a duplicate or touch the existing row - even with a
    # different probability, which would indicate the model changed its
    # mind, not that a new model version was deployed.
    second = snapshot_price(
        db_session, match_id=match.id, player_id=None, market_family="team", market_type="h2h", selection="Carlton",
        line_type=None, threshold=None, line_value=None, model_name="elo_poisson", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status=None, confidence_tier="validated_edge_over_naive",
        model_probability=0.99,
    )
    assert second is None
    rows = db_session.scalars(select(PricingSnapshot)).all()
    assert len(rows) == 1
    assert rows[0].model_probability == 0.6  # untouched by the second call


def test_snapshot_price_new_model_version_gets_its_own_row(db_session):
    match = _seed_upcoming_match(db_session)
    for version in ("v1", "v2"):
        snapshot_price(
            db_session, match_id=match.id, player_id=None, market_family="team", market_type="h2h", selection="Carlton",
            line_type=None, threshold=None, line_value=None, model_name="elo_poisson", model_version=version,
            generated_at=NOW, data_cutoff=NOW, lineup_status=None, confidence_tier="validated_edge_over_naive",
            model_probability=0.6,
        )
    db_session.commit()
    rows = db_session.scalars(select(PricingSnapshot)).all()
    assert len(rows) == 2
    assert {r.model_version for r in rows} == {"v1", "v2"}


def test_settle_pricing_snapshot_team_market_reuses_compute_team_market_result(db_session):
    match = _seed_upcoming_match(db_session)
    match.status = MatchStatus.COMPLETED
    match.home_score, match.away_score = 100, 80
    db_session.commit()

    snapshot_price(
        db_session, match_id=match.id, player_id=None, market_family="team", market_type="h2h", selection="Carlton",
        line_type=None, threshold=None, line_value=None, model_name="elo_poisson", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status=None, confidence_tier="validated_edge_over_naive",
        model_probability=0.6,
    )
    db_session.commit()

    report = settle_pricing_snapshots(db_session)

    assert report.settled == 1
    assert report.won == 1
    snap = db_session.scalars(select(PricingSnapshot)).first()
    assert snap.outcome == "won"
    assert snap.actual_stat_value == 20.0


def test_settle_pricing_snapshot_is_idempotent(db_session):
    match = _seed_upcoming_match(db_session)
    match.status = MatchStatus.COMPLETED
    match.home_score, match.away_score = 100, 80
    db_session.commit()
    snapshot_price(
        db_session, match_id=match.id, player_id=None, market_family="team", market_type="h2h", selection="Carlton",
        line_type=None, threshold=None, line_value=None, model_name="elo_poisson", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status=None, confidence_tier="validated_edge_over_naive",
        model_probability=0.6,
    )
    db_session.commit()

    settle_pricing_snapshots(db_session)
    second_report = settle_pricing_snapshots(db_session)

    assert second_report.settled == 0  # already settled - not re-touched


# --- Usage-Change Production Integration stage: model-risk metadata --------


def _seed_goal_projection(db, match, *, usage_regime=None, usage_change_score=None, p_score=0.6, mu_scored=1.5, alpha_scored=0.4, name="Test Goalkicker", source_id="g1") -> PlayerGoalProjection:
    home = db.scalar(select(Team).where(Team.id == match.home_team_id))
    player = Player(sport_id=home.sport_id, display_name=name, source="afltables", source_player_id=source_id, current_team_id=home.id)
    db.add(player)
    db.flush()
    row = PlayerGoalProjection(
        match_id=match.id, player_id=player.id, team_id=home.id, model_name="goal_hurdle", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=20,
        predicted_mean=p_score * mu_scored, distribution_kind="hurdle", p_score=p_score, mu_scored=mu_scored,
        alpha_scored=alpha_scored, scoring_archetype="forward", confidence_tier="higher_confidence",
        warnings=[], input_features={}, usage_regime=usage_regime, usage_change_score=usage_change_score,
    )
    db.add(row)
    db.commit()
    return row


def test_goal_price_carries_risk_flag_when_usage_regime_changed(db_session):
    match = _seed_upcoming_match(db_session)
    row = _seed_goal_projection(db_session, match, usage_regime="changed", usage_change_score=2.1)

    priced = price_goals(db_session, row)

    assert priced.usage_regime == "changed"
    assert priced.usage_change_score == 2.1
    codes = {f.code for f in priced.model_risk_flags}
    assert codes == {USAGE_REGIME_CHANGE_FLAG}
    assert "11%" in priced.model_risk_flags[0].description
    assert "wrong" not in priced.model_risk_flags[0].description.lower()


def test_goal_price_has_no_risk_flag_when_usage_regime_stable(db_session):
    match = _seed_upcoming_match(db_session)
    row = _seed_goal_projection(db_session, match, usage_regime="stable", usage_change_score=0.4)

    priced = price_goals(db_session, row)

    assert priced.usage_regime == "stable"
    assert priced.model_risk_flags == []


def test_goal_price_has_no_risk_flag_when_usage_regime_unknown(db_session):
    """Rows persisted before this stage (or with insufficient history) have
    usage_regime=None - must never be treated as "changed" by default."""
    match = _seed_upcoming_match(db_session)
    row = _seed_goal_projection(db_session, match, usage_regime=None, usage_change_score=None)

    priced = price_goals(db_session, row)

    assert priced.model_risk_flags == []


def test_goal_price_probability_identical_regardless_of_usage_regime(db_session):
    """Core boundary (item 2): the risk flag is informational metadata only
    - the SAME p_score/mu_scored/alpha_scored must produce the exact same
    thresholds/probabilities and the exact same confidence_tier regardless
    of usage_regime."""
    match = _seed_upcoming_match(db_session)
    row_a = _seed_goal_projection(db_session, match, usage_regime="changed", usage_change_score=3.0, name="Changed Regime Player", source_id="g-changed")
    row_b = _seed_goal_projection(db_session, match, usage_regime="stable", usage_change_score=0.1, name="Stable Regime Player", source_id="g-stable")

    priced_a = price_goals(db_session, row_a)
    priced_b = price_goals(db_session, row_b)

    assert priced_a.expected == priced_b.expected
    assert priced_a.confidence_tier == priced_b.confidence_tier == "higher_confidence"
    for ta, tb in zip(priced_a.thresholds, priced_b.thresholds):
        assert ta.threshold == tb.threshold
        assert abs(ta.probability - tb.probability) < 1e-12  # identical distribution params -> identical probability, flag or no flag


def test_disposal_price_exposes_usage_regime_but_never_a_risk_flag(db_session):
    """Disposal's usage-change effect (~1.7% MAE) did not meet the evidence
    bar for a structured risk flag (see scripts/usage_regime_change_research.py) -
    usage_regime is still exposed as low-priority informational context."""
    match = _seed_upcoming_match(db_session)
    row = _seed_disposal_projection(db_session, match)
    row.usage_regime, row.usage_change_score = "changed", 2.5
    db_session.commit()

    priced = price_disposals(db_session, row)

    assert priced.usage_regime == "changed"
    assert priced.usage_change_score == 2.5
    assert priced.model_risk_flags == []


def test_pricing_snapshot_freezes_usage_regime_and_never_rewrites_it(db_session):
    match = _seed_upcoming_match(db_session)

    first = snapshot_price(
        db_session, match_id=match.id, player_id=None, market_family="player_goals", market_type="player_goals",
        selection="over", line_type="over_under", threshold=1.5, line_value=None, model_name="goal_hurdle",
        model_version="v1", generated_at=NOW, data_cutoff=NOW, lineup_status="expected_in",
        confidence_tier="higher_confidence", model_probability=0.4, usage_regime_at_prediction="changed",
    )
    db_session.commit()
    assert first is not None
    assert first.usage_regime_at_prediction == "changed"

    # A later snapshot_price call at the SAME identity (even with a
    # different usage_regime_at_prediction) must not touch the frozen row.
    second = snapshot_price(
        db_session, match_id=match.id, player_id=None, market_family="player_goals", market_type="player_goals",
        selection="over", line_type="over_under", threshold=1.5, line_value=None, model_name="goal_hurdle",
        model_version="v1", generated_at=NOW, data_cutoff=NOW, lineup_status="expected_in",
        confidence_tier="higher_confidence", model_probability=0.4, usage_regime_at_prediction="stable",
    )
    assert second is None
    rows = db_session.scalars(select(PricingSnapshot)).all()
    assert len(rows) == 1
    assert rows[0].usage_regime_at_prediction == "changed"  # untouched by the second call
