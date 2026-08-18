"""Tests for the Best Opportunities ranking engine (Sections 6-11, 17-18 of
the best-bets stage brief): round-wide scope, player+team merging via the
same transparent score, and the default quality gates."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import (
    Bookmaker,
    ExpectedLineup,
    Match,
    MatchStatus,
    OddsQuote,
    Player,
    PlayerDisposalProjection,
    PlayerModelRun,
    PlayerPropMarket,
    Round,
    Season,
    Sport,
    Team,
)
from app.player_modelling.best_opportunities import load_best_opportunities
from app.player_modelling.market import PlayerMarket

NOW = datetime.now(timezone.utc)


def _seed_match(db, *, home_name="Collingwood", away_name="Carlton", round_number=1):
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
    round_ = db.scalar(select(Round).where(Round.season_id == season.id, Round.round_number == round_number))
    if round_ is None:
        round_ = Round(season_id=season.id, round_number=round_number)
        db.add(round_)
        db.flush()
    home = Team(sport_id=sport.id, name=home_name, short_name=home_name[:3].upper())
    away = Team(sport_id=sport.id, name=away_name, short_name=away_name[:3].upper())
    db.add_all([home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW + timedelta(days=1), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    return match, home, away


def _ensure_promoted_disposal_model(db):
    run = db.scalar(select(PlayerModelRun).where(PlayerModelRun.model_name == "disposals_ridge"))
    if run is None:
        run = PlayerModelRun(
            model_name="disposals_ridge", market=PlayerMarket.DISPOSALS.value, feature_names=[], config_json={},
            distribution_method="nb", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019,
            evaluation_end_year=2025, is_promoted=True, run_at=NOW,
        )
        db.add(run)
        db.commit()
    return run


def _add_player_opportunity(db, match, home, *, player_name="Nick Daicos", price=8.5, confidence_tier="higher_confidence"):
    player = Player(sport_id=match.sport_id, display_name=player_name, source="afltables", source_player_id=player_name, current_team_id=home.id)
    db.add(player)
    db.flush()
    _ensure_promoted_disposal_model(db)
    db.add(
        PlayerDisposalProjection(
            match_id=match.id, player_id=player.id, team_id=home.id, model_name="disposals_ridge", model_version="v1",
            generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
            predicted_mean=28.0, distribution_method="nb", nb_alpha=3.0, confidence_tier=confidence_tier,
            warnings=[], input_features={},
        )
    )
    db.add(
        ExpectedLineup(
            match_id=match.id, player_id=player.id, team_id=home.id, status="expected_in",
            selection_status="confirmed_selected", is_confirmed=True, recorded_at=NOW, source="manual",
        )
    )
    bookmaker = db.scalar(select(Bookmaker).where(Bookmaker.name == "Sportsbet"))
    if bookmaker is None:
        bookmaker = Bookmaker(name="Sportsbet")
        db.add(bookmaker)
        db.flush()
    db.add(
        PlayerPropMarket(
            match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
            line_type="over_under", threshold=27.5, selection="over", price_decimal=price,
            recorded_at=NOW, source="the_odds_api",
        )
    )
    db.commit()
    return player


def _seed_model_runs(db):
    persist_model_run(
        db, "elo", EloConfig(), 2022,
        metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 648,
                  "holdout_value": 0.20, "naive_baseline_value": 0.25, "has_edge_over_naive": True}],
    )
    persist_model_run(
        db, "poisson", PoissonConfig(), 2022,
        metrics=[
            {"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 648,
             "holdout_value": 0.205, "naive_baseline_value": 0.25, "has_edge_over_naive": True},
            {"market_type": "line", "metric_name": "mae", "holdout_n": 648,
             "holdout_value": 27.2, "naive_baseline_value": 31.3, "has_edge_over_naive": True},
            {"market_type": "total", "metric_name": "mae", "holdout_n": 648,
             "holdout_value": 23.5, "naive_baseline_value": 23.5, "has_edge_over_naive": True},
        ],
    )


def _add_team_quote(db, match, bookmaker_name, price, selection):
    bookmaker = db.scalar(select(Bookmaker).where(Bookmaker.name == bookmaker_name))
    if bookmaker is None:
        bookmaker = Bookmaker(name=bookmaker_name)
        db.add(bookmaker)
        db.flush()
    db.add(OddsQuote(
        match_id=match.id, bookmaker_id=bookmaker.id, market_type="h2h", selection=selection,
        line_value=None, price_decimal=price, recorded_at=NOW, source="manual", is_closing_line=False,
    ))
    db.commit()


def test_player_opportunity_included_with_best_price_and_score(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_opportunity(db_session, match, home)

    results = load_best_opportunities(db_session, market_scope="player")
    assert len(results) == 1
    assert results[0]["opportunity_type"] == "player"
    assert results[0]["best_bookmaker"] == "Sportsbet"
    assert results[0]["opportunity_score"] > 0


def test_insufficient_history_excluded_by_default(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_opportunity(db_session, match, home, confidence_tier="insufficient_history")

    default_results = load_best_opportunities(db_session, market_scope="player")
    assert default_results == []

    included = load_best_opportunities(db_session, market_scope="player", include_insufficient_history=True)
    assert len(included) == 1


def test_stale_price_excluded_by_default(db_session):
    match, home, away = _seed_match(db_session)
    player = _add_player_opportunity(db_session, match, home)
    quote = db_session.scalar(select(PlayerPropMarket).where(PlayerPropMarket.player_id == player.id))
    quote.recorded_at = NOW - timedelta(hours=48)
    db_session.commit()

    default_results = load_best_opportunities(db_session, market_scope="player")
    assert default_results == []

    included = load_best_opportunities(db_session, market_scope="player", include_stale=True)
    assert len(included) == 1
    assert included[0]["odds_freshness"] == "stale"


def test_team_market_merged_with_best_price_across_bookmakers(db_session):
    _seed_model_runs(db_session)
    match, home, away = _seed_match(db_session)
    _add_team_quote(db_session, match, "Sportsbet", 1.72, home.name)
    _add_team_quote(db_session, match, "TAB", 1.90, home.name)

    results = load_best_opportunities(db_session, market_scope="team")
    assert len(results) == 1
    assert results[0]["opportunity_type"] == "team"
    assert results[0]["best_price"] == 1.90
    assert results[0]["best_bookmaker"] == "TAB"
    assert results[0]["n_bookmakers"] == 2
    assert results[0]["label"] == f"{home.name} to win"
    bookmaker_names = {b["bookmaker_name"] for b in results[0]["bookmakers"]}
    assert bookmaker_names == {"Sportsbet", "TAB"}
    assert results[0]["bookmakers"][0]["price_decimal"] == 1.90  # sorted best-price-first


def test_no_team_opportunities_without_odds_quotes(db_session):
    _seed_model_runs(db_session)
    match, home, away = _seed_match(db_session)

    results = load_best_opportunities(db_session, market_scope="team")
    assert results == []


def test_no_team_opportunities_when_models_unavailable(db_session):
    match, home, away = _seed_match(db_session)
    _add_team_quote(db_session, match, "Sportsbet", 1.72, home.name)

    results = load_best_opportunities(db_session, market_scope="team")
    assert results == []


def test_all_scope_merges_player_and_team_sorted_by_score(db_session):
    _seed_model_runs(db_session)
    match, home, away = _seed_match(db_session)
    _add_player_opportunity(db_session, match, home)
    _add_team_quote(db_session, match, "Sportsbet", 1.72, home.name)

    results = load_best_opportunities(db_session, market_scope="all")
    types = {r["opportunity_type"] for r in results}
    assert types == {"player", "team"}
    scores = [r["opportunity_score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_limit_caps_results(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_opportunity(db_session, match, home, player_name="Player One", price=8.0)
    _add_player_opportunity(db_session, match, home, player_name="Player Two", price=8.5)

    results = load_best_opportunities(db_session, market_scope="player", limit=1)
    assert len(results) == 1


def test_scope_restricted_to_next_upcoming_round_only(db_session):
    match, home, away = _seed_match(db_session, round_number=1)
    _add_player_opportunity(db_session, match, home)

    later_match, later_home, _ = _seed_match(db_session, home_name="Sydney", away_name="Essendon", round_number=2)
    later_match.scheduled_start = NOW + timedelta(days=8)
    db_session.commit()
    _add_player_opportunity(db_session, later_match, later_home, player_name="Later Player")

    results = load_best_opportunities(db_session, market_scope="player", include_uncertain=True)
    match_ids = {r["match_id"] for r in results}
    assert match_ids == {match.id}


# --- Market Integrity stage: exchange-price exclusion (Section 4) ----------
# Real case this fixes: Betfair (a betting exchange, provider_key
# "betfair_ex_au") quoted "Richmond to win" at $34.00 while every
# sportsbook clustered $11-19 - Betfair's back price is a different
# product (set by other bettors, not a bookmaker), and must never
# silently become "the" best price.


def test_exchange_bookmaker_excluded_from_best_price_by_default(db_session):
    _seed_model_runs(db_session)
    match, home, away = _seed_match(db_session)
    _add_team_quote(db_session, match, "TAB", 1.90, home.name)
    betfair = Bookmaker(name="Betfair", provider_key="betfair_ex_au", is_exchange=True, eligibility="informational_only")
    db_session.add(betfair)
    db_session.flush()
    db_session.add(OddsQuote(
        match_id=match.id, bookmaker_id=betfair.id, market_type="h2h", selection=home.name,
        line_value=None, price_decimal=3.40, recorded_at=NOW, source="manual", is_closing_line=False,
    ))
    db_session.commit()

    results = load_best_opportunities(db_session, market_scope="team")
    assert len(results) == 1
    assert results[0]["best_price"] == 1.90
    assert results[0]["best_bookmaker"] == "TAB"
    assert results[0]["best_price_is_exchange"] is False
    assert results[0]["eligible_price_available"] is True
    assert results[0]["best_price_all_bookmakers"] == 3.40
    assert results[0]["best_bookmaker_all_bookmakers"] == "Betfair"
    assert results[0]["best_price_all_differs_from_enabled"] is True
    # Betfair's price still visible in the full comparison, just not headlined.
    bookmaker_names = {b["bookmaker_name"] for b in results[0]["bookmakers"]}
    assert bookmaker_names == {"TAB", "Betfair"}


def test_falls_back_to_exchange_price_with_disclosure_when_no_eligible_bookmaker(db_session):
    _seed_model_runs(db_session)
    match, home, away = _seed_match(db_session)
    betfair = Bookmaker(name="Betfair", provider_key="betfair_ex_au", is_exchange=True, eligibility="informational_only")
    db_session.add(betfair)
    db_session.flush()
    db_session.add(OddsQuote(
        match_id=match.id, bookmaker_id=betfair.id, market_type="h2h", selection=home.name,
        line_value=None, price_decimal=3.40, recorded_at=NOW, source="manual", is_closing_line=False,
    ))
    db_session.commit()

    results = load_best_opportunities(db_session, market_scope="team")
    assert len(results) == 1
    assert results[0]["eligible_price_available"] is False
    assert results[0]["best_bookmaker"] == "Betfair"
    assert any("informational only" in w for w in results[0]["warnings"])


def test_excluded_bookmaker_also_never_headlines(db_session):
    _seed_model_runs(db_session)
    match, home, away = _seed_match(db_session)
    _add_team_quote(db_session, match, "TAB", 1.90, home.name)
    excluded_book = Bookmaker(name="Blacklisted Book", eligibility="excluded")
    db_session.add(excluded_book)
    db_session.flush()
    db_session.add(OddsQuote(
        match_id=match.id, bookmaker_id=excluded_book.id, market_type="h2h", selection=home.name,
        line_value=None, price_decimal=5.00, recorded_at=NOW, source="manual", is_closing_line=False,
    ))
    db_session.commit()

    results = load_best_opportunities(db_session, market_scope="team")
    assert results[0]["best_price"] == 1.90
    assert results[0]["best_bookmaker"] == "TAB"


# --- Market Integrity stage: latest-snapshot-per-bookmaker dedup (Section 1/2) ---
# Real case this fixes: OddsQuote is append-only, so a bookmaker whose
# price moved between refreshes appeared TWICE in the same market's
# bookmaker list (an old and new quote from the same book compared as if
# they were two different bookmakers) - inflating n_bookmakers and
# triggering false extreme-price-difference flags.


def test_only_latest_quote_per_bookmaker_counted(db_session):
    _seed_model_runs(db_session)
    match, home, away = _seed_match(db_session)
    tab = Bookmaker(name="TAB")
    db_session.add(tab)
    db_session.flush()
    # Older snapshot, then a newer one at a different price for the SAME bookmaker.
    db_session.add(OddsQuote(
        match_id=match.id, bookmaker_id=tab.id, market_type="h2h", selection=home.name,
        line_value=None, price_decimal=1.50, recorded_at=NOW - timedelta(hours=16), source="the_odds_api", is_closing_line=False,
    ))
    db_session.add(OddsQuote(
        match_id=match.id, bookmaker_id=tab.id, market_type="h2h", selection=home.name,
        line_value=None, price_decimal=1.90, recorded_at=NOW, source="the_odds_api", is_closing_line=False,
    ))
    db_session.commit()

    results = load_best_opportunities(db_session, market_scope="team")
    assert len(results) == 1
    assert results[0]["n_bookmakers"] == 1
    assert len(results[0]["bookmakers"]) == 1
    assert results[0]["best_price"] == 1.90  # the LATEST snapshot, not the highest historical price

