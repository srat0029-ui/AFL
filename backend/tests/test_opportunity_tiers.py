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


def _add_player_opportunity(
    db, match, home, *, player_name="Nick Daicos", price=8.5, confirmed=True, recorded_at=NOW, games_of_history=40,
    multi_book=False, has_lineup=True, confirmed_out=False,
):
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
    if confirmed_out:
        db.add(ExpectedLineup(
            match_id=match.id, player_id=player.id, team_id=home.id, status="expected_out",
            selection_status="confirmed_out", is_confirmed=True, recorded_at=NOW, source="manual",
        ))
    elif has_lineup:
        db.add(ExpectedLineup(
            match_id=match.id, player_id=player.id, team_id=home.id, status="expected_in",
            selection_status="confirmed_selected" if confirmed else "uncertain", is_confirmed=confirmed,
            recorded_at=NOW, source="manual",
        ))
    # has_lineup=False, confirmed_out=False: deliberately NO ExpectedLineup
    # row at all - the "projection + market, no lineup row" case.
    for bookmaker_name in (["SportsBet", "TAB"] if multi_book else ["SportsBet"]):
        bookmaker = db.scalar(select(Bookmaker).where(Bookmaker.name == bookmaker_name))
        if bookmaker is None:
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


def test_player_with_projection_and_market_but_no_lineup_row_is_still_returned(db_session):
    """Core rule under audit: a match needs no ExpectedLineup row at all
    for its players to be eligible - projection + valid market is enough."""
    match, home, away = _seed_match(db_session)
    _add_player_opportunity(db_session, match, home, has_lineup=False)

    result = load_opportunity_tiers(db_session)

    assert result.n_hard_excluded == 0
    assert result.n_candidates == 1
    names = {o["player_name"] for o in result.best} | {o["player_name"] for o in result.worth_reviewing}
    assert "Nick Daicos" in names


def test_missing_lineup_downgrades_not_excludes(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_opportunity(db_session, match, home, has_lineup=False)

    result = load_opportunity_tiers(db_session)

    assert result.best == []  # no confirmed lineup -> can't reach Best
    assert len(result.worth_reviewing) == 1
    assert result.worth_reviewing[0]["is_confirmed"] is False


def test_confirmed_out_still_hard_excludes_even_with_no_other_lineup_rows(db_session):
    match, home, away = _seed_match(db_session)
    _add_player_opportunity(db_session, match, home, confirmed_out=True)

    result = load_opportunity_tiers(db_session)

    # confirmed_out is filtered at the source (load_normalized_prop_insights)
    # before it ever becomes a candidate opportunity - never in Best, Worth
    # Reviewing, or the valid-candidate count.
    assert result.best == []
    assert result.worth_reviewing == []
    assert result.n_candidates == 0
    assert all(o["player_name"] != "Nick Daicos" for o in result.all_available)


def test_multiple_matches_without_lineup_rows_all_appear_simultaneously(db_session):
    match1, home1, away1 = _seed_match(db_session, home_name="Collingwood", away_name="Brisbane Lions")
    match2, home2, away2 = _seed_match(db_session, home_name="St Kilda", away_name="Gold Coast")
    match3, home3, away3 = _seed_match(db_session, home_name="Carlton", away_name="Fremantle")
    _add_player_opportunity(db_session, match1, home1, player_name="Player One", has_lineup=True, confirmed=True, multi_book=True, price=20.0)
    _add_player_opportunity(db_session, match2, home2, player_name="Player Two", has_lineup=False)
    _add_player_opportunity(db_session, match3, home3, player_name="Player Three", has_lineup=False)

    result = load_opportunity_tiers(db_session)

    match_ids_shown = {o["match_id"] for o in result.best} | {o["match_id"] for o in result.worth_reviewing}
    assert match_ids_shown == {match1.id, match2.id, match3.id}


def test_no_upcoming_matches_returns_empty_tiers(db_session):
    result = load_opportunity_tiers(db_session)
    assert result.best == []
    assert result.worth_reviewing == []
    assert result.all_available == []
    assert result.fallback_message is None
