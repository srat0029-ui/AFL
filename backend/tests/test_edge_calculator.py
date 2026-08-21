from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.edges.calculator import ModelsUnavailableError, build_model_context, compute_match_edges, compute_match_predictions
from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import Bookmaker, Match, MatchStatus, OddsQuote, Round, Season, Sport, Team


def _seed_upcoming_match(db_session) -> Match:
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season = Season(sport_id=sport.id, year=2026)
    db_session.add(season)
    db_session.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    away = Team(sport_id=sport.id, name="Richmond", short_name="RIC")
    db_session.add_all([round_, home, away])
    db_session.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id,
        home_team_id=home.id, away_team_id=away.id,
        scheduled_start=datetime(2026, 8, 20, tzinfo=timezone.utc), status=MatchStatus.SCHEDULED,
    )
    db_session.add(match)
    db_session.commit()
    return match


def _seed_model_runs(db_session, *, total_has_edge: bool = False) -> None:
    persist_model_run(
        db_session, "elo", EloConfig(), 2022,
        metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 648,
                  "holdout_value": 0.20, "naive_baseline_value": 0.25, "has_edge_over_naive": True}],
    )
    persist_model_run(
        db_session, "poisson", PoissonConfig(), 2022,
        metrics=[
            {"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 648,
             "holdout_value": 0.205, "naive_baseline_value": 0.25, "has_edge_over_naive": True},
            {"market_type": "line", "metric_name": "mae", "holdout_n": 648,
             "holdout_value": 27.2, "naive_baseline_value": 31.3, "has_edge_over_naive": True},
            {"market_type": "total", "metric_name": "mae", "holdout_n": 648,
             "holdout_value": 23.5, "naive_baseline_value": 23.5, "has_edge_over_naive": total_has_edge},
        ],
    )


def _add_quote(db_session, match, bookmaker_name, market_type, selection, price, line_value=None):
    bookmaker = db_session.scalar(select(Bookmaker).where(Bookmaker.name == bookmaker_name))
    if bookmaker is None:
        bookmaker = Bookmaker(name=bookmaker_name)
        db_session.add(bookmaker)
        db_session.flush()
    quote = OddsQuote(
        match_id=match.id, bookmaker_id=bookmaker.id, market_type=market_type,
        selection=selection, line_value=line_value, price_decimal=price,
        recorded_at=datetime.now(timezone.utc), source="manual", is_closing_line=False,
    )
    db_session.add(quote)
    db_session.commit()
    return quote


def test_build_model_context_raises_when_no_model_runs(db_session):
    with pytest.raises(ModelsUnavailableError):
        build_model_context(db_session)


def test_build_model_context_succeeds_with_cold_start_data(db_session):
    _seed_model_runs(db_session)
    context = build_model_context(db_session)

    assert context.has_edge[("elo", "h2h")] is True
    assert context.has_edge[("poisson", "total")] is False


def test_compute_match_edges_empty_when_no_odds(db_session):
    _seed_model_runs(db_session)
    match = _seed_upcoming_match(db_session)
    context = build_model_context(db_session)

    assert compute_match_edges(db_session, match, context) == []


def test_h2h_both_sides_quoted_devigs_correctly(db_session):
    _seed_model_runs(db_session)
    match = _seed_upcoming_match(db_session)
    _add_quote(db_session, match, "Sportsbet", "h2h", "Carlton", 1.85)
    _add_quote(db_session, match, "Sportsbet", "h2h", "Richmond", 2.05)
    context = build_model_context(db_session)

    edges = compute_match_edges(db_session, match, context)
    assert len(edges) == 2

    carlton_edge = next(e for e in edges if e.selection == "Carlton")
    assert carlton_edge.overround_removed is True
    assert carlton_edge.fair_market_probability < carlton_edge.market_implied_probability  # de-vig removes margin
    assert carlton_edge.fair_odds == pytest.approx(1.0 / carlton_edge.model_probability)
    assert carlton_edge.confidence_tier != "insufficient_data"  # h2h has a validated edge


def test_h2h_one_side_only_flags_overround_not_removed(db_session):
    _seed_model_runs(db_session)
    match = _seed_upcoming_match(db_session)
    _add_quote(db_session, match, "Sportsbet", "h2h", "Carlton", 1.85)
    context = build_model_context(db_session)

    edges = compute_match_edges(db_session, match, context)
    assert len(edges) == 1
    assert edges[0].overround_removed is False
    assert edges[0].fair_market_probability == pytest.approx(edges[0].market_implied_probability)
    assert any("overround" in r for r in edges[0].confidence_reasons)


def test_degenerate_price_from_provider_never_crashes_the_whole_match(db_session):
    """Real bug seen live: a provider (The Odds API) occasionally reports a
    degenerate price_decimal of 1.0 for a suspended/settled market, which
    implied_probability correctly rejects — but compute_match_edges used to
    let that ValueError propagate uncaught, which (via best_opportunities'
    per-round loop) took down opportunities for EVERY match in the round,
    not just the one bad quote. Both sides of the pairing must be guarded:
    the degenerate quote itself is skipped entirely, and a fine quote whose
    PAIRED opposite side is degenerate falls back to its own raw implied
    probability instead of crashing remove_overround."""
    _seed_model_runs(db_session)
    match = _seed_upcoming_match(db_session)
    _add_quote(db_session, match, "Sportsbet", "h2h", "Carlton", 1.0)  # degenerate/placeholder
    _add_quote(db_session, match, "Sportsbet", "h2h", "Richmond", 2.05)  # otherwise-fine paired quote
    context = build_model_context(db_session)

    edges = compute_match_edges(db_session, match, context)  # must not raise
    assert len(edges) == 1
    richmond_edge = edges[0]
    assert richmond_edge.selection == "Richmond"
    assert richmond_edge.overround_removed is False
    assert richmond_edge.fair_market_probability == pytest.approx(richmond_edge.market_implied_probability)


def test_total_market_with_no_validated_edge_is_insufficient_data(db_session):
    """The core 'do not manufacture an insight' check: Stage 1.3 found the
    Poisson total-points market has no demonstrated edge over naive — any
    edge computed for it here must be capped at insufficient_data
    regardless of how large the raw number looks."""
    _seed_model_runs(db_session, total_has_edge=False)
    match = _seed_upcoming_match(db_session)
    _add_quote(db_session, match, "Sportsbet", "total", "over", 1.90, line_value=100.0)  # deliberately absurd line
    _add_quote(db_session, match, "Sportsbet", "total", "under", 1.90, line_value=100.0)
    context = build_model_context(db_session)

    edges = compute_match_edges(db_session, match, context)
    over_edge = next(e for e in edges if e.selection == "over")

    # a line of 100 (well below any realistic AFL total) should show a huge
    # raw edge, which makes the hard gate the only thing preventing a
    # manufactured "strong opportunity" claim
    assert over_edge.confidence_tier == "insufficient_data"


def test_total_market_with_validated_edge_is_not_hard_gated(db_session):
    _seed_model_runs(db_session, total_has_edge=True)
    match = _seed_upcoming_match(db_session)
    _add_quote(db_session, match, "Sportsbet", "total", "over", 1.90, line_value=165.5)
    _add_quote(db_session, match, "Sportsbet", "total", "under", 1.90, line_value=165.5)
    context = build_model_context(db_session)

    edges = compute_match_edges(db_session, match, context)
    over_edge = next(e for e in edges if e.selection == "over")
    assert over_edge.confidence_tier != "insufficient_data"
    assert over_edge.overround_removed is True


def test_line_market_pairing_devigs_home_and_away(db_session):
    _seed_model_runs(db_session)
    match = _seed_upcoming_match(db_session)
    _add_quote(db_session, match, "TAB", "line", "Carlton", 1.90, line_value=-12.5)
    _add_quote(db_session, match, "TAB", "line", "Richmond", 1.90, line_value=12.5)
    context = build_model_context(db_session)

    edges = compute_match_edges(db_session, match, context)
    assert len(edges) == 2
    assert all(e.overround_removed for e in edges)
    carlton_edge = next(e for e in edges if e.selection == "Carlton")
    richmond_edge = next(e for e in edges if e.selection == "Richmond")
    assert carlton_edge.fair_market_probability + richmond_edge.fair_market_probability == pytest.approx(1.0)


def test_secondary_model_probability_populated_for_h2h_only(db_session):
    _seed_model_runs(db_session)
    match = _seed_upcoming_match(db_session)
    _add_quote(db_session, match, "Sportsbet", "h2h", "Carlton", 1.85)
    _add_quote(db_session, match, "Sportsbet", "total", "over", 1.90, line_value=165.5)
    context = build_model_context(db_session)

    edges = compute_match_edges(db_session, match, context)
    h2h_edge = next(e for e in edges if e.market_type == "h2h")
    total_edge = next(e for e in edges if e.market_type == "total")

    assert h2h_edge.secondary_model_probability is not None
    assert total_edge.secondary_model_probability is None  # only Poisson covers totals, no cross-check available


def test_expected_value_matches_fair_odds_math_module(db_session):
    _seed_model_runs(db_session)
    match = _seed_upcoming_match(db_session)
    _add_quote(db_session, match, "Sportsbet", "h2h", "Carlton", 1.85)
    _add_quote(db_session, match, "Sportsbet", "h2h", "Richmond", 2.05)
    context = build_model_context(db_session)

    edges = compute_match_edges(db_session, match, context)
    carlton_edge = next(e for e in edges if e.selection == "Carlton")
    expected = carlton_edge.model_probability * (1.85 - 1.0) - (1 - carlton_edge.model_probability)
    assert carlton_edge.expected_value == pytest.approx(expected)


def test_compute_match_predictions_works_without_any_odds(db_session):
    """The whole point of splitting this out: predictions should be
    available for a match that has no odds entered at all yet."""
    _seed_model_runs(db_session)
    match = _seed_upcoming_match(db_session)
    context = build_model_context(db_session)

    predictions = compute_match_predictions(match, context)

    assert predictions.match_id == match.id
    assert 0.0 <= predictions.elo_home_win_probability <= 1.0
    probs_sum = (
        predictions.poisson_home_win_probability
        + predictions.poisson_draw_probability
        + predictions.poisson_away_win_probability
    )
    assert probs_sum == pytest.approx(1.0, abs=1e-6)
    assert predictions.poisson_expected_total_points == pytest.approx(
        predictions.poisson_home_expected_score + predictions.poisson_away_expected_score
    )
    assert predictions.poisson_expected_margin == pytest.approx(
        predictions.poisson_home_expected_score - predictions.poisson_away_expected_score
    )


def test_compute_match_edges_predictions_are_consistent_with_standalone_call(db_session):
    """compute_match_edges and compute_match_predictions must agree on the
    underlying model probabilities — they now share one code path."""
    _seed_model_runs(db_session)
    match = _seed_upcoming_match(db_session)
    _add_quote(db_session, match, "Sportsbet", "h2h", "Carlton", 1.85)
    context = build_model_context(db_session)

    predictions = compute_match_predictions(match, context)
    edges = compute_match_edges(db_session, match, context)

    carlton_edge = next(e for e in edges if e.selection == "Carlton")
    assert carlton_edge.model_probability == pytest.approx(predictions.elo_home_win_probability)
