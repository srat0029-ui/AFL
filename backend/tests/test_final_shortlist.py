"""Tests for the Final Weekly Shortlist (Market Integrity stage, Sections
7-11, 22) - the more selective view than Best Opportunities: confirmed
-lineup gating, strong-correlation collapsing (the brief's own Collingwood
H2H + Collingwood +24.5 example), and never manufacturing a padded Top N."""

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
from app.player_modelling.final_shortlist import load_final_shortlist
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
        ],
    )


def _add_team_quotes(db, match, selection, *, market_type="h2h", line_value=None, prices):
    for bookmaker_name, price in prices:
        bookmaker = db.scalar(select(Bookmaker).where(Bookmaker.name == bookmaker_name))
        if bookmaker is None:
            bookmaker = Bookmaker(name=bookmaker_name)
            db.add(bookmaker)
            db.flush()
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


def _add_player_opportunity(db, match, home, *, player_name="Nick Daicos", price=8.5, confirmed=True):
    player = Player(sport_id=match.sport_id, display_name=player_name, source="afltables", source_player_id=player_name, current_team_id=home.id)
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
            match_id=match.id, player_id=player.id, team_id=home.id, status="expected_in",
            selection_status="confirmed_selected" if confirmed else "uncertain", is_confirmed=confirmed,
            recorded_at=NOW, source="manual",
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
            line_type="over_under", threshold=27.5, selection="over", price_decimal=price,
            recorded_at=NOW, source="the_odds_api",
        )
    )
    db.commit()
    return player


def test_unconfirmed_player_excluded_by_default(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_opportunity(db_session, match, home, confirmed=False)

    result = load_final_shortlist(db_session, limit=10)
    assert result.opportunities == []
    assert any("not yet confirmed" in e.reason for e in result.excluded)


def test_unconfirmed_player_included_with_toggle(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_opportunity(db_session, match, home, confirmed=False)

    result = load_final_shortlist(db_session, limit=10, include_unconfirmed_players=True)
    assert len(result.opportunities) == 1


def test_confirmed_player_headlines(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_opportunity(db_session, match, home, confirmed=True)

    result = load_final_shortlist(db_session, limit=10)
    assert len(result.opportunities) == 1
    assert result.opportunities[0]["is_confirmed"] is True


def test_h2h_and_line_same_team_collapse_to_one_shortlist_entry(db_session):
    # The brief's own real example: Collingwood H2H + Collingwood +24.5
    # must not both headline as if they were distinct opinions.
    _seed_model_runs(db_session)
    match, home, away = _seed_match(db_session)
    _add_team_quotes(db_session, match, home.name, market_type="h2h", prices=[("SportsBet", 4.20), ("TAB", 4.10)])
    _add_team_quotes(db_session, match, home.name, market_type="line", line_value=24.5, prices=[("SportsBet", 1.90), ("TAB", 1.88)])

    result = load_final_shortlist(db_session, limit=10)
    team_labels = [o["label"] for o in result.opportunities if o["opportunity_type"] == "team"]
    assert len(team_labels) <= 1, f"expected at most one collapsed team entry, got {team_labels}"


def test_shortlist_never_backfills_below_limit(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_opportunity(db_session, match, home, confirmed=True)

    result = load_final_shortlist(db_session, limit=20)
    # Only one genuine opportunity exists - the shortlist must not pad to 20.
    assert len(result.opportunities) == 1


def test_empty_state_reason_set_when_no_opportunities(db_session):
    match, home, away = _seed_match(db_session)
    result = load_final_shortlist(db_session, limit=10)
    assert result.opportunities == []
    assert result.empty_state_reason is not None


def test_team_market_flagged_teams_not_confirmed_when_no_lineups_exist(db_session):
    _seed_model_runs(db_session)
    match, home, away = _seed_match(db_session)
    _add_team_quotes(db_session, match, home.name, market_type="h2h", prices=[("SportsBet", 4.20), ("TAB", 4.10)])

    result = load_final_shortlist(db_session, limit=10)
    assert result.any_confirmed_player_lineups is False
    team_entries = [o for o in result.opportunities if o["opportunity_type"] == "team"]
    if team_entries:
        assert any("Teams not confirmed yet" in c for c in team_entries[0]["caveats"])
