from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import (
    Bookmaker,
    ExpectedLineup,
    GoalModelRun,
    Match,
    MatchStatus,
    Player,
    PlayerDisposalProjection,
    PlayerModelRun,
    PlayerModelValidationMetric,
    PlayerPropMarket,
    Round,
    Season,
    Sport,
    Team,
)
from app.player_modelling.market import PlayerMarket
from app.player_modelling.prop_insights_normalized import load_normalized_prop_insights

NOW = datetime.now(timezone.utc)


def _ensure_promoted_disposal_model(db):
    if db.scalar(select(PlayerModelRun).where(PlayerModelRun.model_name == "disposals_ridge")) is None:
        db.add(
            PlayerModelRun(
                model_name="disposals_ridge", market=PlayerMarket.DISPOSALS.value, feature_names=[], config_json={},
                distribution_method="nb", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019,
                evaluation_end_year=2025, is_promoted=True, run_at=NOW,
            )
        )
        db.commit()


def _seed(db, is_confirmed=True, selection_status="confirmed_selected"):
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
        scheduled_start=NOW + timedelta(days=1), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.flush()
    player = Player(sport_id=sport.id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    db.add(player)
    db.flush()

    _ensure_promoted_disposal_model(db)
    db.add(
        PlayerDisposalProjection(
            match_id=match.id, player_id=player.id, team_id=home.id, model_name="disposals_ridge", model_version="v1",
            generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
            predicted_mean=28.0, distribution_method="nb", nb_alpha=3.0, confidence_tier="higher_confidence",
            warnings=[], input_features={},
        )
    )
    db.add(
        ExpectedLineup(
            match_id=match.id, player_id=player.id, team_id=home.id, status="expected_in" if is_confirmed else "uncertain",
            selection_status=selection_status, is_confirmed=is_confirmed, recorded_at=NOW, source="manual",
        )
    )
    db.commit()
    return match, home, away, player


def _add_quote(db, match, player, bookmaker_name, price, selection="over", threshold=27.5, recorded_at=None, source="the_odds_api"):
    bookmaker = db.scalar(select(Bookmaker).where(Bookmaker.name == bookmaker_name))
    if bookmaker is None:
        bookmaker = Bookmaker(name=bookmaker_name)
        db.add(bookmaker)
        db.flush()
    db.add(
        PlayerPropMarket(
            match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
            line_type="over_under", threshold=threshold, selection=selection, price_decimal=price,
            recorded_at=recorded_at or NOW, source=source,
        )
    )
    db.commit()
    return bookmaker


def test_best_price_selected_across_multiple_bookmakers(db_session):
    match, home, away, player = _seed(db_session)
    _add_quote(db_session, match, player, "Sportsbet", 1.72)
    _add_quote(db_session, match, player, "TAB", 1.80)
    _add_quote(db_session, match, player, "Ladbrokes", 1.87)

    rows = load_normalized_prop_insights(db_session)
    assert len(rows) == 1
    assert rows[0]["best_price"] == 1.87
    assert rows[0]["best_bookmaker"] == "Ladbrokes"
    assert rows[0]["n_bookmakers"] == 3


def test_devig_uses_same_bookmaker_paired_side(db_session):
    match, home, away, player = _seed(db_session)
    _add_quote(db_session, match, player, "Sportsbet", 1.90, selection="over")
    _add_quote(db_session, match, player, "Sportsbet", 1.90, selection="under")

    rows = load_normalized_prop_insights(db_session)
    assert rows[0]["overround_removed"] is True
    assert rows[0]["devigged_probability"] is not None
    assert 0 < rows[0]["devigged_probability"] < 1


def test_one_sided_market_uses_raw_implied_probability(db_session):
    match, home, away, player = _seed(db_session)
    _add_quote(db_session, match, player, "Sportsbet", 1.90, selection="over")

    rows = load_normalized_prop_insights(db_session)
    assert rows[0]["overround_removed"] is False
    assert rows[0]["devigged_probability"] is None
    assert any("Only one side" in w for w in rows[0]["warnings"])


def test_confirmed_out_never_shown(db_session):
    match, home, away, player = _seed(db_session, is_confirmed=False, selection_status="confirmed_out")
    _add_quote(db_session, match, player, "Sportsbet", 1.90)
    rows = load_normalized_prop_insights(db_session, include_uncertain=True)
    assert rows == []


def test_uncertain_participation_downgrades_confidence_and_is_excludable(db_session):
    match, home, away, player = _seed(db_session, is_confirmed=False, selection_status="named_in_squad")
    _add_quote(db_session, match, player, "Sportsbet", 1.90)

    with_uncertain = load_normalized_prop_insights(db_session, include_uncertain=True)
    assert len(with_uncertain) == 1
    assert with_uncertain[0]["confidence_tier"] == "moderate_confidence"  # downgraded from higher_confidence

    without_uncertain = load_normalized_prop_insights(db_session, include_uncertain=False)
    assert without_uncertain == []


def test_stale_price_excluded_from_headline_best_price(db_session):
    match, home, away, player = _seed(db_session)
    _add_quote(db_session, match, player, "Sportsbet", 1.72, recorded_at=NOW)
    _add_quote(db_session, match, player, "TAB", 2.50, recorded_at=NOW - timedelta(hours=48))  # much better price, but stale

    rows = load_normalized_prop_insights(db_session)
    assert rows[0]["best_price"] == 1.72
    assert rows[0]["best_bookmaker"] == "Sportsbet"
    # the stale quote is still visible in the full bookmaker list
    assert any(b["bookmaker_name"] == "TAB" and b["freshness"] == "stale" for b in rows[0]["bookmakers"])


def test_price_movement_first_current_highest_lowest(db_session):
    match, home, away, player = _seed(db_session)
    t1 = NOW - timedelta(hours=3)
    t2 = NOW - timedelta(hours=2)
    t3 = NOW - timedelta(hours=1)
    _add_quote(db_session, match, player, "Sportsbet", 1.70, recorded_at=t1)
    _add_quote(db_session, match, player, "Sportsbet", 1.95, recorded_at=t2)
    _add_quote(db_session, match, player, "Sportsbet", 1.80, recorded_at=t3)

    rows = load_normalized_prop_insights(db_session)
    movement = rows[0]["price_movement"]
    assert movement["first_price"] == 1.70
    assert movement["current_price"] == 1.80
    assert movement["highest_price"] == 1.95
    assert movement["lowest_price"] == 1.70


def test_opportunities_only_filters_out_non_positive_difference(db_session):
    match, home, away, player = _seed(db_session)
    # a price so short it implies a probability far above the model's -> negative difference
    _add_quote(db_session, match, player, "Sportsbet", 1.01)
    rows_all = load_normalized_prop_insights(db_session, opportunities_only=False)
    rows_opportunities = load_normalized_prop_insights(db_session, opportunities_only=True)
    assert len(rows_all) == 1
    assert rows_all[0]["difference_pp"] < 0
    assert rows_opportunities == []


def test_calibration_metrics_populated_when_promoted_model_has_sufficient_sample(db_session):
    match, home, away, player = _seed(db_session)
    _add_quote(db_session, match, player, "Sportsbet", 1.90, threshold=29.0)
    run = db_session.scalar(select(PlayerModelRun).where(PlayerModelRun.model_name == "disposals_ridge"))
    db_session.add(
        PlayerModelValidationMetric(model_run_id=run.id, segment="threshold_30", metric_name="ece", n=64282, value=0.005)
    )
    db_session.commit()

    rows = load_normalized_prop_insights(db_session)
    calibration = rows[0]["calibration"]
    assert calibration is not None
    assert calibration["evaluated_threshold"] == 30.0
    assert calibration["ece"] == 0.005
    assert calibration["n"] == 64282
    assert any("calibration error (ECE)" in w for w in rows[0]["warnings"])


def test_calibration_metrics_none_when_no_validation_metric_exists(db_session):
    match, home, away, player = _seed(db_session)
    _add_quote(db_session, match, player, "Sportsbet", 1.90)

    rows = load_normalized_prop_insights(db_session)
    assert rows[0]["calibration"] is None


def test_manual_and_automated_quotes_both_considered_for_best_price(db_session):
    match, home, away, player = _seed(db_session)
    _add_quote(db_session, match, player, "Sportsbet", 1.72, source="the_odds_api")
    _add_quote(db_session, match, player, "TAB", 1.95, source="manual")

    rows = load_normalized_prop_insights(db_session)
    assert rows[0]["best_price"] == 1.95
    assert rows[0]["best_bookmaker"] == "TAB"
    sources = {b["bookmaker_name"]: b["source"] for b in rows[0]["bookmakers"]}
    assert sources == {"Sportsbet": "the_odds_api", "TAB": "manual"}
