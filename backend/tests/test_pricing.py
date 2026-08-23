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
    PricingSnapshot,
    Round,
    Season,
    Sport,
    Team,
)
from app.pricing.player_pricing import price_disposals
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
