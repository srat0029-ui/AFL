"""Targeted tests for the per-match Multi Builder (product feature stage):
same-bookmaker requirement, tier target-odds ranges, hard exclusions
(stale/confirmed-out), confirmed-lineup preference, alternate-line
de-duplication, correlation handling (strong exclusion / moderate
warning), no forced multi when candidates are insufficient, and the
"Indicative combined odds" labelling requirement."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import (
    Bookmaker, ExpectedLineup, Match, MatchStatus, OddsQuote, Player, PlayerDisposalProjection,
    PlayerModelRun, PlayerPropMarket, Round, Season, Sport, Team,
)
from app.player_modelling.market import PlayerMarket
from app.player_modelling.request_cache import clear_ttl_cache
from app.player_modelling.multi_builder import (
    INDICATIVE_ODDS_LABEL, TIER_BALANCED, TIER_CONSERVATIVE, TIER_HIGHER_RETURN, TIER_LONGER_SHOT,
    build_match_multis,
)

NOW = datetime.now(timezone.utc)


def _seed_match(db, *, home_name="Collingwood", away_name="Carlton"):
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
    round_ = db.scalar(select(Round).where(Round.season_id == season.id, Round.round_number == 1))
    if round_ is None:
        round_ = Round(season_id=season.id, round_number=1)
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


def _seed_model_runs(db):
    if db.scalar(select(PlayerModelRun).where(PlayerModelRun.model_name == "elo_placeholder")) is not None:
        return
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
        ],
    )


def _bookmaker(db, name):
    b = db.scalar(select(Bookmaker).where(Bookmaker.name == name))
    if b is None:
        b = Bookmaker(name=name)
        db.add(b)
        db.flush()
    return b


def _add_team_quotes(db, match, selection, *, market_type="h2h", line_value=None, prices):
    for bookmaker_name, price in prices:
        bookmaker = _bookmaker(db, bookmaker_name)
        db.add(OddsQuote(
            match_id=match.id, bookmaker_id=bookmaker.id, market_type=market_type, selection=selection,
            line_value=line_value, price_decimal=price, recorded_at=NOW, source="manual", is_closing_line=False,
        ))
    db.commit()


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


def _add_player_leg(
    db, match, team, *, player_name, threshold=10.5, predicted_mean=22.0, nb_alpha=0.3, prices, confirmed=True,
    recorded_at=NOW, games_of_history=40, confirmed_out=False,
):
    """`prices` is a list of (bookmaker_name, price) - the same player/
    threshold quoted at one or more bookmakers, mirroring real multi-
    bookmaker coverage. Defaults (low nb_alpha, threshold well under the
    mean) give a genuinely high model probability (~80%) so a short
    bookmaker price still nets a POSITIVE model-market difference -
    otherwise `opportunities_only=True` in load_normalized_prop_insights
    silently drops the row, which real disposal counts/thresholds would
    reflect naturally but a synthetic fixture must set up deliberately."""
    player = db.scalar(select(Player).where(Player.display_name == player_name))
    if player is None:
        player = Player(sport_id=match.sport_id, display_name=player_name, source="afltables", source_player_id=player_name, current_team_id=team.id)
        db.add(player)
        db.flush()
        _ensure_promoted_disposal_model(db)
        db.add(PlayerDisposalProjection(
            match_id=match.id, player_id=player.id, team_id=team.id, model_name="disposals_ridge", model_version="v1",
            generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=games_of_history,
            predicted_mean=predicted_mean, distribution_method="nb", nb_alpha=nb_alpha,
            confidence_tier="higher_confidence" if games_of_history >= 10 else "insufficient_history",
            warnings=[], input_features={},
        ))
        if confirmed_out:
            db.add(ExpectedLineup(
                match_id=match.id, player_id=player.id, team_id=team.id, status="expected_out",
                selection_status="confirmed_out", is_confirmed=True, recorded_at=NOW, source="manual",
            ))
        else:
            db.add(ExpectedLineup(
                match_id=match.id, player_id=player.id, team_id=team.id, status="expected_in",
                selection_status="confirmed_selected" if confirmed else "uncertain", is_confirmed=confirmed,
                recorded_at=NOW, source="manual",
            ))
    for bookmaker_name, price in prices:
        bookmaker = _bookmaker(db, bookmaker_name)
        db.add(PlayerPropMarket(
            match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
            line_type="over_under", threshold=threshold, selection="over", price_decimal=price,
            recorded_at=recorded_at, source="the_odds_api",
        ))
    db.commit()
    return player


def _all_options(result):
    return [opt for options in result.tiers.values() for opt in options]


def test_same_bookmaker_requirement(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_leg(db_session, match, home, player_name="Player A", prices=[("SportsBet", 1.60)])
    _add_player_leg(db_session, match, home, player_name="Player B", prices=[("TAB", 1.60)])

    result = build_match_multis(db_session, match.id, confirmed_only=True)
    assert _all_options(result) == []  # no bookmaker offers both legs -> no combo possible

    _add_player_leg(db_session, match, home, player_name="Player C", prices=[("SportsBet", 1.30)])  # 1.60*1.30=2.08 -> Conservative
    clear_ttl_cache()  # this test mutates DB state between two builds within the request-cache's TTL window
    result2 = build_match_multis(db_session, match.id, confirmed_only=True)
    options = _all_options(result2)
    assert options, "expected a combo once two legs share a bookmaker"
    for opt in options:
        assert opt["bookmaker"] == "SportsBet"
        assert all(leg["player_name"] != "Player B" for leg in opt["legs"])  # never mixed in from TAB


def test_target_odds_range_respected(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_leg(db_session, match, home, player_name="Player A", prices=[("SportsBet", 1.60)])
    _add_player_leg(db_session, match, home, player_name="Player B", prices=[("SportsBet", 2.20)])
    # 1.60 * 2.20 = 3.52 -> squarely in Balanced (3.00-5.00)

    result = build_match_multis(db_session, match.id, confirmed_only=True)
    balanced = result.tiers[TIER_BALANCED]
    assert balanced, "expected a Balanced option"
    for opt in balanced:
        assert 3.00 <= opt["indicative_combined_odds"] <= 5.00
    assert result.tiers[TIER_CONSERVATIVE] == []  # 3.52 never fits 1.80-2.50


def test_stale_leg_excluded(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_leg(db_session, match, home, player_name="Player A", prices=[("SportsBet", 1.60)], recorded_at=NOW - timedelta(days=5))
    _add_player_leg(db_session, match, home, player_name="Player B", prices=[("SportsBet", 2.20)])

    result = build_match_multis(db_session, match.id, confirmed_only=True)
    assert result.n_eligible_legs == 1  # the stale leg never enters the pool
    for opt in _all_options(result):
        assert all(leg["player_name"] != "Player A" for leg in opt["legs"])


def test_confirmed_out_excluded(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_leg(db_session, match, home, player_name="Player A", prices=[("SportsBet", 1.60)], confirmed_out=True)
    _add_player_leg(db_session, match, home, player_name="Player B", prices=[("SportsBet", 2.20)])

    result = build_match_multis(db_session, match.id, confirmed_only=False)
    assert result.n_eligible_legs == 1
    for opt in _all_options(result):
        assert all(leg["player_name"] != "Player A" for leg in opt["legs"])


def test_confirmed_lineup_preferred_over_unconfirmed(db_session):
    match, home, away = _seed_match(db_session)
    _seed_model_runs(db_session)
    _add_team_quotes(db_session, match, home.name, market_type="h2h", prices=[("SportsBet", 1.30)])
    _add_player_leg(db_session, match, home, player_name="Confirmed Player", prices=[("SportsBet", 1.60)], confirmed=True)
    _add_player_leg(db_session, match, home, player_name="Unconfirmed Player", prices=[("SportsBet", 1.60)], confirmed=False)
    # h2h(1.30) + either player(1.60) = 2.08, squarely in Conservative on its
    # own - only ONE of the two interchangeable players is needed, so which
    # one gets picked for Option A is a genuine preference signal.

    result = build_match_multis(db_session, match.id, confirmed_only=False)
    option_a = next(iter(_all_options(result)), None)
    assert option_a is not None
    names = {leg["player_name"] for leg in option_a["legs"] if leg["player_name"]}
    assert "Confirmed Player" in names
    assert "Unconfirmed Player" not in names


def test_alternate_lines_never_duplicated_in_one_multi(db_session):
    match, home, away = _seed_match(db_session)
    player = _add_player_leg(db_session, match, home, player_name="Player A", threshold=14.5, prices=[("SportsBet", 1.60)])
    _add_player_leg(db_session, match, home, player_name="Player A", threshold=19.5, prices=[("SportsBet", 2.50)])
    _add_player_leg(db_session, match, home, player_name="Player B", prices=[("SportsBet", 2.00)])

    result = build_match_multis(db_session, match.id, confirmed_only=True)
    # Only ONE representative line for Player A ever enters the eligible pool.
    assert result.n_eligible_legs == 2
    for opt in _all_options(result):
        player_a_legs = [leg for leg in opt["legs"] if leg["player_name"] == "Player A"]
        assert len(player_a_legs) <= 1


def test_strong_correlation_never_combined(db_session):
    match, home, away = _seed_match(db_session)
    _seed_model_runs(db_session)
    _add_team_quotes(db_session, match, home.name, market_type="h2h", prices=[("SportsBet", 1.50)])
    _add_team_quotes(db_session, match, home.name, market_type="line", line_value=-12.5, prices=[("SportsBet", 1.60)])

    result = build_match_multis(db_session, match.id, confirmed_only=True)
    for opt in _all_options(result):
        team_legs = [leg for leg in opt["legs"] if leg["opportunity_type"] == "team"]
        assert len(team_legs) <= 1, "H2H and line for the same team must never appear in the same multi"


def test_moderate_correlation_carries_warning(db_session):
    match, home, away = _seed_match(db_session)
    _seed_model_runs(db_session)
    _add_team_quotes(db_session, match, home.name, market_type="h2h", prices=[("SportsBet", 1.40)])
    _add_player_leg(db_session, match, home, player_name="Home Team Player", prices=[("SportsBet", 1.60)])  # 1.40*1.60=2.24 -> Conservative

    result = build_match_multis(db_session, match.id, confirmed_only=True)
    combo_options = [o for o in _all_options(result) if o["n_legs"] >= 2]
    assert combo_options, "expected a team + same-team-player combo"
    assert any(opt["correlation_warnings"] for opt in combo_options)


def test_no_forced_multi_with_insufficient_candidates(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_leg(db_session, match, home, player_name="Only Player", prices=[("SportsBet", 1.60)])

    result = build_match_multis(db_session, match.id, confirmed_only=True)
    assert _all_options(result) == []


def test_indicative_odds_terminology(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_leg(db_session, match, home, player_name="Player A", prices=[("SportsBet", 1.60)])
    _add_player_leg(db_session, match, home, player_name="Player B", prices=[("SportsBet", 2.20)])

    result = build_match_multis(db_session, match.id, confirmed_only=True)
    options = _all_options(result)
    assert options
    for opt in options:
        assert opt.get("bookmaker")  # sanity: real dict shape
    from app.player_modelling.multi_builder import option_as_dict
    d = option_as_dict(options[0])
    assert d["indicative_odds_label"] == INDICATIVE_ODDS_LABEL
    assert "not a real" in d["indicative_odds_explanation"].lower() or "not" in d["indicative_odds_explanation"].lower()
    assert "correlation" in d["indicative_odds_explanation"].lower()
