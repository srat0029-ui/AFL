"""Integration tests for the diversified Best Opportunities orchestration
(Weekly Opportunity Discovery stage) - the actual fix for "all five top
opportunities are alternate disposal lines for the same player"."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import (
    Bookmaker,
    ExpectedLineup,
    Match,
    MatchStatus,
    Player,
    PlayerDisposalProjection,
    PlayerMatchStat,
    PlayerModelRun,
    PlayerPropMarket,
    Round,
    Season,
    Sport,
    Team,
)
from app.player_modelling.diversified_opportunities import VIEW_DISPOSALS, VIEW_OVERALL, load_diversified_opportunities
from app.player_modelling.market import PlayerMarket

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


def _add_player_with_lines(db, match, home, player_name, *, thresholds_and_prices):
    """Seeds one player with MULTIPLE alternate disposal-threshold quotes,
    matching the real Darcy Gardiner scenario the brief describes."""
    player = Player(sport_id=match.sport_id, display_name=player_name, source="afltables", source_player_id=player_name, current_team_id=home.id)
    db.add(player)
    db.flush()
    _ensure_promoted_disposal_model(db)
    db.add(
        PlayerDisposalProjection(
            match_id=match.id, player_id=player.id, team_id=home.id, model_name="disposals_ridge", model_version="v1",
            generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
            predicted_mean=28.0, distribution_method="nb", nb_alpha=3.0, confidence_tier="higher_confidence",
            warnings=[], input_features={"disposals_last5_avg": 28.0, "disposals_last10_avg": 27.0, "disposals_ewma": 28.0},
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
    for threshold, price in thresholds_and_prices:
        db.add(
            PlayerPropMarket(
                match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
                line_type="over_under", threshold=threshold, selection="over", price_decimal=price,
                recorded_at=NOW, source="the_odds_api",
            )
        )
    db.commit()
    return player


def test_alternate_lines_for_one_player_collapse_to_one_headline(db_session):
    match, home, away = _seed_match(db_session)
    # Six alternate disposal lines for the SAME player - the exact
    # scenario the brief flags as a usability problem when left raw.
    _add_player_with_lines(
        db_session, match, home, "Darcy Gardiner",
        thresholds_and_prices=[(10.5, 3.5), (11.5, 4.0), (12.5, 4.5), (13.5, 5.0), (14.5, 5.5), (15.5, 6.0)],
    )

    result = load_diversified_opportunities(db_session, view=VIEW_OVERALL, include_uncertain=True)
    headline_players = [o["player_id"] for o in result.opportunities]
    assert len(headline_players) == 1  # only one headline for this player, not six
    assert len(result.opportunities[0]["alternate_lines"]) == 5


def test_headline_has_family_label_and_alternate_line_shape(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_with_lines(db_session, match, home, "Darcy Gardiner", thresholds_and_prices=[(12.5, 4.5), (13.5, 5.0)])

    result = load_diversified_opportunities(db_session, view=VIEW_OVERALL, include_uncertain=True)
    entry = result.opportunities[0]
    assert entry["family_label"] == f"Darcy Gardiner / {home.name} v {away.name} / disposals"
    alt = entry["alternate_lines"][0]
    assert set(alt.keys()) == {"threshold", "line_type", "label", "model_probability", "best_price", "best_bookmaker", "difference_pp", "expected_value", "n_bookmakers"}


def test_two_players_produce_two_headlines_not_correlated_as_one(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_with_lines(db_session, match, home, "Darcy Gardiner", thresholds_and_prices=[(12.5, 4.5), (13.5, 5.0)])
    _add_player_with_lines(db_session, match, home, "Jack Crisp", thresholds_and_prices=[(20.5, 6.5)])

    result = load_diversified_opportunities(db_session, view=VIEW_OVERALL, include_uncertain=True)
    player_names = {o["player_name"] for o in result.opportunities}
    assert player_names == {"Darcy Gardiner", "Jack Crisp"}


def test_correlation_label_present_when_alternates_exist(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_with_lines(db_session, match, home, "Darcy Gardiner", thresholds_and_prices=[(12.5, 4.5), (13.5, 5.0)])

    result = load_diversified_opportunities(db_session, view=VIEW_OVERALL, include_uncertain=True)
    assert any("alternate line" in label for label in result.opportunities[0]["correlation_labels"])


def test_view_disposals_excludes_goal_markets(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_with_lines(db_session, match, home, "Darcy Gardiner", thresholds_and_prices=[(12.5, 4.5)])

    result = load_diversified_opportunities(db_session, view=VIEW_DISPOSALS, include_uncertain=True)
    assert all(o["market_type"] == "player_disposals" for o in result.opportunities)


def test_recent_form_block_present_for_player_opportunity(db_session):
    match, home, away = _seed_match(db_session)
    player = _add_player_with_lines(db_session, match, home, "Darcy Gardiner", thresholds_and_prices=[(12.5, 4.5)])

    # seed some real recent-form history
    for i, disposals in enumerate([25, 30, 28, 32, 29]):
        past_match = Match(
            sport_id=match.sport_id, season_id=match.season_id, round_id=match.round_id,
            home_team_id=home.id, away_team_id=away.id,
            scheduled_start=NOW - timedelta(days=(5 - i) * 7), status=MatchStatus.COMPLETED,
        )
        db_session.add(past_match)
        db_session.flush()
        db_session.add(PlayerMatchStat(
            player_id=player.id, match_id=past_match.id, team_id=home.id, opponent_team_id=away.id,
            source="afltables", recorded_at=NOW, disposals=disposals,
        ))
    db_session.commit()

    result = load_diversified_opportunities(db_session, view=VIEW_OVERALL, include_uncertain=True)
    recent_form = result.opportunities[0]["recent_form"]
    assert recent_form is not None
    assert recent_form["last5"] == [25, 30, 28, 32, 29]
    assert "12.5+" in recent_form["hit_rate_description"]
    assert recent_form["predicted_mean"] == 28.0


def test_reason_codes_present_on_headline(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_with_lines(db_session, match, home, "Darcy Gardiner", thresholds_and_prices=[(12.5, 4.5)])

    result = load_diversified_opportunities(db_session, view=VIEW_OVERALL, include_uncertain=True)
    assert isinstance(result.opportunities[0]["reason_codes"], list)
    assert isinstance(result.opportunities[0]["reason_labels"], list)


def test_one_per_match_filter_collapses_multiple_players_in_one_match(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_with_lines(db_session, match, home, "Darcy Gardiner", thresholds_and_prices=[(12.5, 4.5)])
    _add_player_with_lines(db_session, match, home, "Jack Crisp", thresholds_and_prices=[(20.5, 6.5)])

    result = load_diversified_opportunities(db_session, view=VIEW_OVERALL, include_uncertain=True, one_per_match=True)
    match_ids = {o["match_id"] for o in result.opportunities}
    assert len(result.opportunities) == 1
    assert len(match_ids) == 1
