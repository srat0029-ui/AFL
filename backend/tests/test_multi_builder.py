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
    INDICATIVE_ODDS_LABEL, MODE_VALUE, TIER_BALANCED, TIER_CONSERVATIVE, TIER_HIGHER_RETURN, TIER_LONGER_SHOT,
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
    # one gets picked for Option A is a genuine preference signal. Value
    # mode: this synthetic team (no real Elo/Poisson signal) reads as
    # negative-edge to the model at such a short price, which High-
    # Probability mode would (correctly) exclude entirely - not what this
    # test is about.
    result = build_match_multis(db_session, match.id, confirmed_only=False, mode=MODE_VALUE)
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
    # Player A's family may contribute up to 2 CANDIDATE lines (a "best value"
    # and a "safest/highest-probability" pick - see _safest_family_member),
    # but the real invariant is that no single multi ever uses more than one.
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


def test_h2h_and_opposite_team_line_never_combined(db_session):
    """Real bug report: 'Gold Coast to win' + 'St Kilda -15.5' were being
    proposed together as if independent - they're two phrasings of the
    SAME underlying margin question (h2h/line are each collapsed to one
    representative per match regardless of team) and can be directly
    contradictory (Gold Coast winning at all means St Kilda didn't cover
    -15.5)."""
    match, home, away = _seed_match(db_session, home_name="St Kilda", away_name="Gold Coast")
    _seed_model_runs(db_session)
    _add_team_quotes(db_session, match, away.name, market_type="h2h", prices=[("SportsBet", 2.88)])  # Gold Coast to win
    _add_team_quotes(db_session, match, home.name, market_type="line", line_value=-15.5, prices=[("SportsBet", 1.89)])  # St Kilda -15.5

    result = build_match_multis(db_session, match.id, confirmed_only=True)
    for opt in _all_options(result):
        team_legs = [leg for leg in opt["legs"] if leg["opportunity_type"] == "team"]
        assert len(team_legs) <= 1, f"h2h and line for opposing teams must never combine, got {[l['label'] for l in team_legs]}"


def test_moderate_correlation_carries_warning(db_session):
    match, home, away = _seed_match(db_session)
    _seed_model_runs(db_session)
    _add_team_quotes(db_session, match, home.name, market_type="h2h", prices=[("SportsBet", 1.40)])
    _add_player_leg(db_session, match, home, player_name="Home Team Player", prices=[("SportsBet", 1.60)])  # 1.40*1.60=2.24 -> Conservative

    # Value mode: this synthetic team has no real Elo/Poisson signal, so a
    # short team price reads as negative-edge to the model - correctly
    # excluded by High-Probability mode's edge floor, but not what this
    # correlation-warning test is about.
    result = build_match_multis(db_session, match.id, confirmed_only=True, mode=MODE_VALUE)
    combo_options = [o for o in _all_options(result) if o["n_legs"] >= 2]
    assert combo_options, "expected a team + same-team-player combo"
    assert any(opt["correlation_warnings"] for opt in combo_options)


def test_no_forced_multi_with_insufficient_candidates(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_leg(db_session, match, home, player_name="Only Player", prices=[("SportsBet", 1.60)])

    result = build_match_multis(db_session, match.id, confirmed_only=True)
    assert _all_options(result) == []


def test_conservative_tier_prefers_the_safest_alternate_line_over_the_headline_value_pick(db_session):
    """Real bug report: Conservative multis were starved because the
    family-representative picker (opportunity_families.representative_score)
    is tuned for single-bet VALUE and actively discounts short/likely
    prices - so a genuinely safe ~80-95% probability leg (e.g. a low
    disposal threshold) never got offered as a multi leg at all. A
    Conservative-range combo must be able to reach for the SAFEST line in
    a player's family, not just whichever line scores highest as a
    standalone opportunity."""
    match, home, away = _seed_match(db_session)
    # Same player, two alternate lines: a big-edge "value" line the family
    # would normally headline, and a much safer, shorter, still-positive-
    # edge line.
    _add_player_leg(db_session, match, home, player_name="Player A", threshold=15.5, predicted_mean=22.0, nb_alpha=0.3, prices=[("SportsBet", 3.00)])
    _add_player_leg(db_session, match, home, player_name="Player A", threshold=5.5, predicted_mean=22.0, nb_alpha=0.3, prices=[("SportsBet", 1.30)])
    _add_player_leg(db_session, match, home, player_name="Player B", threshold=5.5, predicted_mean=22.0, nb_alpha=0.3, prices=[("SportsBet", 1.50)])
    # safe(1.30) * partner(1.50) = 1.95 -> Conservative; value(3.00) * partner(1.50) = 4.50 -> Balanced, not Conservative.

    result = build_match_multis(db_session, match.id, confirmed_only=True)
    conservative = result.tiers[TIER_CONSERVATIVE]
    assert conservative, "expected a Conservative option using the safer alternate line"
    for opt in conservative:
        player_a_leg = next(leg for leg in opt["legs"] if leg["player_name"] == "Player A")
        assert player_a_leg["bookmaker_price"] == 1.30, "Conservative must use the SAFE line, not the higher-edge value line"


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


def test_leg_dict_exposes_selection_and_line_fields_for_placed_bets(db_session):
    """These fields aren't used by any ranking/combo-validity logic (which
    reads them straight off the underlying leg dict - see _combo_key) -
    they're exposed here so a leg can be frozen into a Placed Bets record
    with its exact selection/threshold, not just its label/price."""
    match, home, away = _seed_match(db_session)
    _add_player_leg(db_session, match, home, player_name="Player A", prices=[("SportsBet", 1.60)])
    _add_player_leg(db_session, match, home, player_name="Player B", prices=[("SportsBet", 2.20)])

    result = build_match_multis(db_session, match.id, confirmed_only=True)
    options = _all_options(result)
    assert options
    from app.player_modelling.multi_builder import option_as_dict
    d = option_as_dict(options[0])
    leg = d["legs"][0]
    # Player legs never store an explicit "over" selection (see
    # best_opportunities.py - every player-market opportunity IS the over
    # side by construction, so callers that need a selection string for a
    # player leg supply "over" themselves rather than reading a stored one).
    assert leg["selection"] is None
    assert leg["threshold"] is not None
    assert leg["line_type"] == "over_under"


# --- High-Probability vs Value mode (product feature refinement) -----------


def test_high_probability_mode_excludes_low_probability_high_edge_leg(db_session):
    """The exact distinction this stage is about: a leg like 'model 82% /
    $1.25 / modest edge' belongs in High-Probability mode; a leg like
    'model fair $2.00 / bookmaker $3.90 / huge edge' but with a middling
    ~55% probability must NOT dominate High-Probability construction, even
    though Value mode is free to love it."""
    from app.player_modelling.multi_builder import MIN_LEG_PROBABILITY, MODE_HIGH_PROBABILITY, TIER_BALANCED, _candidate_pool, _match_legs

    match, home, away = _seed_match(db_session)
    _add_player_leg(db_session, match, home, player_name="Safe Player", threshold=10.5, predicted_mean=22.0, nb_alpha=0.3, prices=[("SportsBet", 1.25)])
    _add_player_leg(db_session, match, home, player_name="Big Edge Player", threshold=15.5, predicted_mean=22.0, nb_alpha=0.3, prices=[("SportsBet", 3.90)])

    legs = _match_legs(db_session, match.id)
    big_edge = next(leg for leg in legs if leg["player_name"] == "Big Edge Player")
    assert big_edge["model_probability"] < MIN_LEG_PROBABILITY[TIER_BALANCED], "fixture sanity: this leg's probability must sit below the Balanced floor"
    assert big_edge["difference_pp"] > 0.20, "fixture sanity: this leg must have a genuinely large edge"

    pool_high_prob = _candidate_pool(legs, TIER_BALANCED, MODE_HIGH_PROBABILITY, confirmed_only=False)
    assert not any(leg["player_name"] == "Big Edge Player" for leg in pool_high_prob), "High-Probability mode must exclude the low-probability, high-edge leg"

    pool_value = _candidate_pool(legs, TIER_BALANCED, MODE_VALUE, confirmed_only=False)
    assert any(leg["player_name"] == "Big Edge Player" for leg in pool_value), "Value mode must still offer the high-edge leg"
    # And Value mode ranks it ABOVE the safe leg (edge-led ranking).
    names_in_order = [leg["player_name"] for leg in pool_value]
    assert names_in_order.index("Big Edge Player") < names_in_order.index("Safe Player")


def test_min_leg_probability_and_leg_count_constants_are_configurable(db_session):
    from app.player_modelling.multi_builder import MAX_LEGS, MIN_LEGS, MIN_LEG_PROBABILITY, TIER_ORDER

    for tier in TIER_ORDER:
        assert 0.0 < MIN_LEG_PROBABILITY[tier] <= 1.0
        assert MIN_LEGS[tier] >= 2
        assert MAX_LEGS[tier] >= MIN_LEGS[tier]
    # Conservative demands the highest individual-leg probability, Longer
    # Shot the lowest - and leg-count ceilings widen as tiers get longer.
    assert MIN_LEG_PROBABILITY[TIER_CONSERVATIVE] > MIN_LEG_PROBABILITY[TIER_BALANCED] > MIN_LEG_PROBABILITY[TIER_HIGHER_RETURN] > MIN_LEG_PROBABILITY[TIER_LONGER_SHOT]
    assert MAX_LEGS[TIER_CONSERVATIVE] <= MAX_LEGS[TIER_BALANCED] <= MAX_LEGS[TIER_HIGHER_RETURN] <= MAX_LEGS[TIER_LONGER_SHOT]


def test_conservative_can_use_more_than_two_legs_when_that_fits_better(db_session):
    """Combination search, not greedy top-2: four safe, short, positive-
    edge legs whose product only lands inside Conservative's 1.80-2.50 band
    at FOUR legs (2 and 3 both fall short) must still be found."""
    match, home, away = _seed_match(db_session)
    for i in range(4):
        _add_player_leg(db_session, match, home, player_name=f"Safe Player {i}", threshold=5.5, predicted_mean=22.0, nb_alpha=0.3, prices=[("SportsBet", 1.20)])
    # 1.20^2=1.44, 1.20^3=1.728 (both below 1.80); 1.20^4=2.0736 (inside 1.80-2.50).

    result = build_match_multis(db_session, match.id, confirmed_only=True)
    conservative = result.tiers[TIER_CONSERVATIVE]
    assert conservative, "expected a 4-leg Conservative combination"
    assert conservative[0]["n_legs"] == 4
    assert 1.80 <= conservative[0]["indicative_combined_odds"] <= 2.50


def test_option_shows_lowest_and_average_leg_probability_never_a_combined_probability(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_leg(db_session, match, home, player_name="Player A", threshold=10.5, predicted_mean=22.0, nb_alpha=0.3, prices=[("SportsBet", 1.40)])
    _add_player_leg(db_session, match, home, player_name="Player B", threshold=5.5, predicted_mean=22.0, nb_alpha=0.3, prices=[("SportsBet", 1.40)])
    # 1.40 * 1.40 = 1.96 -> Conservative

    result = build_match_multis(db_session, match.id, confirmed_only=True)
    options = _all_options(result)
    assert options
    from app.player_modelling.multi_builder import option_as_dict
    d = option_as_dict(options[0])
    assert "lowest_leg_probability" in d and "average_leg_probability" in d
    assert d["lowest_leg_probability"] <= d["average_leg_probability"]
    assert not any("combined_probability" in k or "joint_probability" in k for k in d)
    assert d["mode"] in ("high_probability", "value")


def test_multi_builder_route_exposes_mode_and_probability_fields(client, db_session):
    """API-level check: the mode query param reaches build_match_multis and
    the response schema actually carries mode/lowest/average leg probability
    (not just the Python dict from option_as_dict, which schema validation
    would silently strip if MultiOptionRead hadn't been updated to match)."""
    match, home, away = _seed_match(db_session)
    _add_player_leg(db_session, match, home, player_name="Player A", threshold=10.5, predicted_mean=22.0, nb_alpha=0.3, prices=[("SportsBet", 1.40)])
    _add_player_leg(db_session, match, home, player_name="Player B", threshold=5.5, predicted_mean=22.0, nb_alpha=0.3, prices=[("SportsBet", 1.40)])

    response = client.get(f"/api/afl/matches/{match.id}/multi-builder", params={"mode": "high_probability"})
    assert response.status_code == 200
    body = response.json()
    options = [opt for tier in body["tiers"] for opt in tier["options"]]
    assert options
    for opt in options:
        assert opt["mode"] == "high_probability"
        assert "lowest_leg_probability" in opt and "average_leg_probability" in opt

    bad = client.get(f"/api/afl/matches/{match.id}/multi-builder", params={"mode": "not_a_real_mode"})
    assert bad.status_code == 422


def test_high_probability_sees_every_alternate_line_not_just_one_safest_alt(db_session):
    """This stage's core fix: _match_legs (Value mode's pool) collapses a
    player's alternate lines down to one value-ranked representative plus
    at most one "safest alternate," which would silently hide a player's
    3rd/4th/5th disposal line from High-Probability mode entirely. High
    Probability must instead see EVERY valid alternate threshold line."""
    from app.player_modelling.multi_builder import _all_alternate_legs

    match, home, away = _seed_match(db_session)
    # One player, three alternate disposal lines - low/mid/high threshold,
    # same underlying projection (mean=27, matching the module docstring's
    # own 15+/20+/25+ example).
    # Prices set a touch LONGER than each threshold's own fair odds (model
    # prob ~80%/65%/49%) so every line nets a small positive edge - the
    # rows would otherwise be silently dropped by opportunities_only, same
    # as any real market with a negative model-market difference.
    _add_player_leg(db_session, match, home, player_name="Multi Line Player", threshold=14.5, predicted_mean=27.0, nb_alpha=0.25, prices=[("SportsBet", 1.32)])
    _add_player_leg(db_session, match, home, player_name="Multi Line Player", threshold=19.5, predicted_mean=27.0, nb_alpha=0.25, prices=[("SportsBet", 1.65)])
    _add_player_leg(db_session, match, home, player_name="Multi Line Player", threshold=24.5, predicted_mean=27.0, nb_alpha=0.25, prices=[("SportsBet", 2.20)])

    all_legs = _all_alternate_legs(db_session, match.id)
    thresholds = sorted(leg["threshold"] for leg in all_legs if leg.get("player_name") == "Multi Line Player")
    assert thresholds == [14.5, 19.5, 24.5], f"expected all three alternate lines, got {thresholds}"

    # All three share one family_key (same player, same match, same market
    # family) so a combo can never use two of this player's disposal lines
    # at once - a pool-visibility fix, not a relaxation of that rule.
    family_keys = {leg["_family_key"] for leg in all_legs if leg.get("player_name") == "Multi Line Player"}
    assert len(family_keys) == 1


def test_low_probability_goal_leg_excluded_from_conservative_and_balanced_despite_big_edge(db_session):
    """Module docstring's explicit goals caution: a 2+ goals leg sitting at
    ~51% model probability must never qualify for Conservative/Balanced
    High-Probability multis no matter how attractive its price/edge looks -
    the probability floor is never bypassed by edge."""
    from app.player_modelling.multi_builder import MIN_LEG_PROBABILITY, MODE_HIGH_PROBABILITY, TIER_BALANCED, TIER_CONSERVATIVE, _all_alternate_legs, _candidate_pool

    match, home, away = _seed_match(db_session)
    _ensure_promoted_disposal_model(db_session)
    player = Player(sport_id=match.sport_id, display_name="Marginal Goalkicker", source="afltables", source_player_id="marginal-goalkicker", current_team_id=home.id)
    db_session.add(player)
    db_session.flush()
    from app.player_modelling.goal_distribution import HurdleDistribution  # local import mirrors goal_backtest's own usage

    db_session.add(ExpectedLineup(
        match_id=match.id, player_id=player.id, team_id=home.id, status="expected_in",
        selection_status="confirmed_selected", is_confirmed=True, recorded_at=NOW, source="manual",
    ))
    from app.models import PlayerGoalProjection
    # p_score/mu_scored/alpha_scored chosen so P(2+ goals) lands ~51% -
    # a "coin flip" goal leg, exactly the case that must NOT qualify.
    dist = HurdleDistribution(p_score=0.95, mu_scored=1.6, alpha_scored=0.35)
    prob_2plus = dist.prob_at_least(2)
    assert 0.45 <= prob_2plus <= 0.58, f"fixture sanity: expected ~51% P(2+ goals), got {prob_2plus:.2%}"
    db_session.add(PlayerGoalProjection(
        match_id=match.id, player_id=player.id, team_id=home.id, model_name="goals_hurdle", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
        predicted_mean=dist.mean(), distribution_kind="hurdle", nb_alpha=None, p_score=0.95, mu_scored=1.6, alpha_scored=0.35,
        scoring_archetype="forward", confidence_tier="higher_confidence", warnings=[], input_features={},
    ))
    bookmaker = _bookmaker(db_session, "SportsBet")
    db_session.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.GOALS.value,
        line_type="over_under", threshold=1.5, selection="over", price_decimal=2.60,  # big edge vs ~51% model prob
        recorded_at=NOW, source="the_odds_api",
    ))
    db_session.commit()

    all_legs = _all_alternate_legs(db_session, match.id)
    leg = next(leg for leg in all_legs if leg.get("player_name") == "Marginal Goalkicker")
    assert 0.45 <= leg["model_probability"] <= 0.58
    assert leg["difference_pp"] > 0.10, "fixture sanity: this leg must have a large positive edge"

    for tier in (TIER_CONSERVATIVE, TIER_BALANCED):
        assert leg["model_probability"] < MIN_LEG_PROBABILITY[tier]
        pool = _candidate_pool(all_legs, tier, MODE_HIGH_PROBABILITY, confirmed_only=True)
        assert leg["_family_key"] not in {p["_family_key"] for p in pool if p.get("player_name") == "Marginal Goalkicker"}


# --- Usage-Change Production Integration stage, item 4: risk-flag tiebreak -


def _minimal_leg(*, model_risk_flags, opportunity_score=0.5, best_price=2.0, model_probability=0.7, confidence=0.8, calibration=0.8, difference_pp=0.02):
    """A minimal synthetic leg dict carrying only the fields
    _combo_rank_key/representative_score actually read - lets the tiebreak
    itself be tested in isolation from a full DB-backed leg search, and
    guarantees two legs are identical on every ranking dimension EXCEPT
    model_risk_flags (a full search-based fixture can't cheaply guarantee
    an exact tie on every prior criterion)."""
    return {
        "model_probability": model_probability,
        "difference_pp": difference_pp,
        "opportunity_type": "player",
        "is_confirmed": True,
        "opportunity_components": {"confidence": confidence, "calibration": calibration},
        "opportunity_score": opportunity_score,
        "best_price": best_price,
        "model_risk_flags": model_risk_flags,
    }


def test_high_probability_tiebreak_prefers_stable_regime_leg_when_otherwise_tied():
    from app.player_modelling.multi_builder import MODE_HIGH_PROBABILITY, _combo_rank_key

    flagged = _minimal_leg(model_risk_flags=[{"code": "RECENT_USAGE_REGIME_CHANGE", "description": "..."}])
    unflagged = _minimal_leg(model_risk_flags=[])

    key_flagged = _combo_rank_key((flagged,), warnings=[], mode=MODE_HIGH_PROBABILITY)
    key_unflagged = _combo_rank_key((unflagged,), warnings=[], mode=MODE_HIGH_PROBABILITY)

    # Every ranking criterion before the risk-flag tiebreak is identical by
    # construction (same probability/confidence/calibration/edge/leg-count),
    # so this proves the flag is what breaks the tie - not model_probability
    # or any earlier criterion.
    assert key_flagged[:-1] == key_unflagged[:-1]
    assert key_unflagged > key_flagged


def test_high_probability_tiebreak_never_overrides_a_genuinely_higher_probability_leg():
    """The risk flag is the LAST-priority tiebreak - a flagged leg with a
    real probability edge must still outrank an unflagged leg with lower
    probability, exactly as it would with no flag involved at all."""
    from app.player_modelling.multi_builder import MODE_HIGH_PROBABILITY, _combo_rank_key

    flagged_higher_prob = _minimal_leg(model_risk_flags=[{"code": "RECENT_USAGE_REGIME_CHANGE", "description": "..."}], model_probability=0.85)
    unflagged_lower_prob = _minimal_leg(model_risk_flags=[], model_probability=0.60)

    key_flagged = _combo_rank_key((flagged_higher_prob,), warnings=[], mode=MODE_HIGH_PROBABILITY)
    key_unflagged = _combo_rank_key((unflagged_lower_prob,), warnings=[], mode=MODE_HIGH_PROBABILITY)

    assert key_flagged > key_unflagged


def test_value_mode_ranking_is_unaffected_by_the_risk_flag():
    """Item 4's explicit boundary: Best Value mode can still surface a
    flagged leg - the risk flag must play no role at all in Value mode's
    ranking key (only opportunity/representative score + confirmed-count +
    leg-count, unchanged from before this stage)."""
    from app.player_modelling.multi_builder import MODE_VALUE, _combo_rank_key

    flagged = _minimal_leg(model_risk_flags=[{"code": "RECENT_USAGE_REGIME_CHANGE", "description": "..."}])
    unflagged = _minimal_leg(model_risk_flags=[])

    key_flagged = _combo_rank_key((flagged,), warnings=[], mode=MODE_VALUE)
    key_unflagged = _combo_rank_key((unflagged,), warnings=[], mode=MODE_VALUE)

    assert key_flagged == key_unflagged


def test_multi_leg_dict_exposes_usage_regime_and_model_risk_flags(db_session):
    """option_as_dict must pass model_risk_flags/usage_regime through to
    the API-facing leg dict, not just use them internally for ranking."""
    match, home, away = _seed_match(db_session)
    _add_player_leg(db_session, match, home, player_name="Player A", prices=[("SportsBet", 1.60)])
    _add_player_leg(db_session, match, home, player_name="Player B", prices=[("SportsBet", 2.20)])

    result = build_match_multis(db_session, match.id, confirmed_only=True)
    options = _all_options(result)
    assert options
    from app.player_modelling.multi_builder import option_as_dict

    leg = option_as_dict(options[0])["legs"][0]
    assert "usage_regime" in leg
    assert "model_risk_flags" in leg
    assert leg["model_risk_flags"] == []  # these disposal legs carry no goal risk flag
