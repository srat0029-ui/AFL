"""Tests for the Model vs Market Disagreements diagnostic (Market
Integrity stage, Section 18) — must surface disagreements in EITHER
direction, including the market-far-more-confident-than-model case that
is invisible everywhere else in this app (Best Opportunities/Final
Shortlist only show markets where the model exceeds the market)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import (
    Bookmaker,
    ExpectedLineup,
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
from app.player_modelling.market import PlayerMarket
from app.player_modelling.model_market_disagreements import (
    DIRECTION_MARKET_ABOVE_MODEL,
    DIRECTION_MODEL_ABOVE_MARKET,
    load_model_market_disagreements,
)

NOW = datetime.now(timezone.utc)


def _seed_match(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=1)
    db.add(round_)
    db.flush()
    home = Team(sport_id=sport.id, name="Collingwood", short_name="COL")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db.add_all([home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW + timedelta(days=1), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    return match, home, away


def _add_player_market(db, match, home, *, player_name, predicted_mean, threshold, price, confidence_tier="higher_confidence"):
    run = db.scalar(select(PlayerModelRun).where(PlayerModelRun.model_name == "disposals_ridge"))
    if run is None:
        run = PlayerModelRun(
            model_name="disposals_ridge", market=PlayerMarket.DISPOSALS.value, feature_names=[], config_json={},
            distribution_method="nb", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019,
            evaluation_end_year=2025, is_promoted=True, run_at=NOW,
        )
        db.add(run)
        db.commit()
    player = Player(sport_id=match.sport_id, display_name=player_name, source="afltables", source_player_id=player_name, current_team_id=home.id)
    db.add(player)
    db.flush()
    db.add(
        PlayerDisposalProjection(
            match_id=match.id, player_id=player.id, team_id=home.id, model_name="disposals_ridge", model_version="v1",
            generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
            predicted_mean=predicted_mean, distribution_method="nb", nb_alpha=3.0, confidence_tier=confidence_tier,
            warnings=[], input_features={},
        )
    )
    db.add(
        ExpectedLineup(
            match_id=match.id, player_id=player.id, team_id=home.id, status="expected_in",
            selection_status="confirmed_selected", is_confirmed=True, recorded_at=NOW, source="manual",
        )
    )
    bookmaker = db.scalar(select(Bookmaker).where(Bookmaker.name == "SportsBet"))
    if bookmaker is None:
        bookmaker = Bookmaker(name="SportsBet")
        db.add(bookmaker)
        db.flush()
    db.add(
        PlayerPropMarket(
            match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
            line_type="multi_plus", threshold=threshold, selection="over", price_decimal=price,
            recorded_at=NOW, source="the_odds_api",
        )
    )
    db.commit()
    return player


def test_market_above_model_disagreement_surfaced(db_session):
    match, home, away = _seed_match(db_session)
    # Model thinks this threshold is unlikely (low mean relative to a high
    # threshold), but the market prices it as near-certain (short odds) -
    # the real "Nick Daicos" pattern this stage's brief describes.
    _add_player_market(db_session, match, home, player_name="Elite Player", predicted_mean=20.0, threshold=29.5, price=1.07)

    rows = load_model_market_disagreements(db_session, threshold_pp=0.10, limit=10)
    assert len(rows) == 1
    assert rows[0]["direction"] == DIRECTION_MARKET_ABOVE_MODEL
    assert rows[0]["difference_pp"] < 0


def test_model_above_market_disagreement_also_surfaced(db_session):
    match, home, away = _seed_match(db_session)
    # Model likes this a lot more than the market does (long odds for a
    # threshold the model considers quite likely).
    _add_player_market(db_session, match, home, player_name="Undervalued Player", predicted_mean=30.0, threshold=15.5, price=8.0)

    rows = load_model_market_disagreements(db_session, threshold_pp=0.10, limit=10)
    assert len(rows) == 1
    assert rows[0]["direction"] == DIRECTION_MODEL_ABOVE_MARKET
    assert rows[0]["difference_pp"] > 0


def test_below_threshold_not_flagged(db_session):
    match, home, away = _seed_match(db_session)
    # Model and market roughly agree - shouldn't appear even at a loose threshold.
    _add_player_market(db_session, match, home, player_name="Agreement Player", predicted_mean=20.0, threshold=20.5, price=2.0)

    rows = load_model_market_disagreements(db_session, threshold_pp=0.50, limit=10)
    assert rows == []


def test_this_is_not_gated_by_opportunities_only_unlike_best_opportunities(db_session):
    """The critical fix this module exists for: best_opportunities.py's
    player path is opportunities_only=True (model > market only), so a
    market-above-model disagreement would otherwise never appear ANYWHERE
    in the app. This module must bypass that filter."""
    match, home, away = _seed_match(db_session)
    _add_player_market(db_session, match, home, player_name="Elite Player", predicted_mean=20.0, threshold=29.5, price=1.07)

    from app.player_modelling.best_opportunities import load_best_opportunities

    best_opps = load_best_opportunities(db_session, market_scope="player", include_uncertain=True, include_stale=True, include_insufficient_history=True)
    assert best_opps == []  # invisible in Best Opportunities (model below market)

    disagreements = load_model_market_disagreements(db_session, threshold_pp=0.10)
    assert len(disagreements) == 1  # but visible here
