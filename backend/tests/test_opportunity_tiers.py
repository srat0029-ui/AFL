"""Tests for the three-tier Prop Insights UX (product-quality stage):
Best Opportunities / Worth Reviewing / All Available, and the exclusion
breakdown. Root-cause fix under test: an unconfirmed player must be
DOWNGRADED to Worth Reviewing, never made invisible, while genuine hard
exclusions (stale odds, insufficient history, confirmed out, integrity
failure) still work exactly as before."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Bookmaker, ExpectedLineup, Match, MatchStatus, Player, PlayerDisposalProjection, PlayerModelRun, PlayerPropMarket, Round, Season, Sport, Team
from app.player_modelling.market import PlayerMarket
from app.player_modelling.opportunity_tiers import FALLBACK_MESSAGE, load_opportunity_tiers

NOW = datetime.now(timezone.utc)


def _seed_match(db, *, home_name="Collingwood", away_name="Carlton"):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
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


def _add_player_opportunity(db, match, home, *, player_name="Nick Daicos", price=8.5, confirmed=True, recorded_at=NOW, games_of_history=40, multi_book=False):
    player = Player(sport_id=match.sport_id, display_name=player_name, source="afltables", source_player_id=player_name, current_team_id=home.id)
    db.add(player)
    db.flush()
    _ensure_promoted_disposal_model(db)
    db.add(PlayerDisposalProjection(
        match_id=match.id, player_id=player.id, team_id=home.id, model_name="disposals_ridge", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=games_of_history,
        predicted_mean=28.0, distribution_method="nb", nb_alpha=3.0,
        confidence_tier="higher_confidence" if games_of_history >= 10 else "insufficient_history",
        warnings=[], input_features={},
    ))
    db.add(ExpectedLineup(
        match_id=match.id, player_id=player.id, team_id=home.id, status="expected_in",
        selection_status="confirmed_selected" if confirmed else "uncertain", is_confirmed=confirmed,
        recorded_at=NOW, source="manual",
    ))
    for bookmaker_name in (["SportsBet", "TAB"] if multi_book else ["SportsBet"]):
        bookmaker = Bookmaker(name=bookmaker_name)
        db.add(bookmaker)
        db.flush()
        db.add(PlayerPropMarket(
            match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
            line_type="over_under", threshold=27.5, selection="over", price_decimal=price,
            recorded_at=recorded_at, source="the_odds_api",
        ))
    db.commit()
    return player


def test_unconfirmed_player_appears_in_worth_reviewing_not_hidden(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_opportunity(db_session, match, home, confirmed=False)

    result = load_opportunity_tiers(db_session)

    assert result.best == []
    assert len(result.worth_reviewing) == 1
    assert result.worth_reviewing[0]["player_name"] == "Nick Daicos"
    assert result.worth_reviewing[0]["is_confirmed"] is False
    assert result.n_hard_excluded == 0
    assert result.n_candidates == 1


def test_confirmed_strong_player_appears_in_best(db_session):
    match, home, away = _seed_match(db_session)
    # A long price against a genuinely likely outcome (predicted_mean=28 vs
    # a 27.5 threshold) produces a large model-market difference (>=10pp) -
    # needed to actually reach the "meaningful difference" bar for
    # strong_candidate, not just "worth reviewing".
    _add_player_opportunity(db_session, match, home, confirmed=True, multi_book=True, price=20.0)

    result = load_opportunity_tiers(db_session)

    assert len(result.best) == 1
    assert result.worth_reviewing == []
    assert result.fallback_message is None


def test_fallback_message_set_when_best_empty_but_worth_reviewing_has_entries(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_opportunity(db_session, match, home, confirmed=False)

    result = load_opportunity_tiers(db_session)

    assert result.best == []
    assert result.worth_reviewing != []
    assert result.fallback_message == FALLBACK_MESSAGE


def test_stale_odds_hard_excluded_not_in_any_tier(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_opportunity(db_session, match, home, confirmed=True, recorded_at=NOW - timedelta(days=5))

    result = load_opportunity_tiers(db_session)

    assert result.best == []
    assert result.worth_reviewing == []
    assert result.n_hard_excluded == 1
    assert result.exclusion_breakdown["stale_odds"] == 1


def test_insufficient_history_hard_excluded(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_opportunity(db_session, match, home, confirmed=True, games_of_history=1)

    result = load_opportunity_tiers(db_session)

    assert result.n_hard_excluded == 1
    assert result.exclusion_breakdown["insufficient_history"] == 1


def test_all_available_includes_worth_reviewing_entries(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_opportunity(db_session, match, home, confirmed=False)

    result = load_opportunity_tiers(db_session)

    assert len(result.all_available) == 1
    assert result.all_available[0]["player_name"] == "Nick Daicos"


def test_no_upcoming_matches_returns_empty_tiers(db_session):
    result = load_opportunity_tiers(db_session)
    assert result.best == []
    assert result.worth_reviewing == []
    assert result.all_available == []
    assert result.fallback_message is None
