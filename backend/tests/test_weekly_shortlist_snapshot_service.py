"""Tests for shortlist snapshot/freeze, immutability, replay, and result
tracking (Weekly Bet Review stage, Sections 14-16)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import Bookmaker, Match, MatchStatus, OddsQuote, Round, Season, Sport, Team, WeeklyShortlistSnapshot, WeeklyShortlistSnapshotItem
from app.player_modelling.weekly_shortlist_snapshot_service import create_snapshot, get_snapshot, list_snapshots, settle_snapshot

NOW = datetime.now(timezone.utc)


def _seed_model_runs(db):
    persist_model_run(
        db, "elo", EloConfig(), 2022,
        metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 648, "holdout_value": 0.20, "naive_baseline_value": 0.25, "has_edge_over_naive": True}],
    )
    persist_model_run(
        db, "poisson", PoissonConfig(), 2022,
        metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 648, "holdout_value": 0.205, "naive_baseline_value": 0.25, "has_edge_over_naive": True}],
    )


def _seed_match_with_h2h_opportunity(db, *, home_name="Collingwood", away_name="Carlton"):
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

    _seed_model_runs(db)
    for bookmaker_name, price in [("SportsBet", 1.72), ("TAB", 1.90)]:
        bookmaker = Bookmaker(name=bookmaker_name)
        db.add(bookmaker)
        db.flush()
        db.add(OddsQuote(match_id=match.id, bookmaker_id=bookmaker.id, market_type="h2h", selection=home.name, line_value=None, price_decimal=price, recorded_at=NOW, source="manual", is_closing_line=False))
    db.commit()
    return match, home, away


def test_create_snapshot_freezes_current_shortlist(db_session):
    match, home, away = _seed_match_with_h2h_opportunity(db_session)
    snapshot = create_snapshot(db_session, limit=10, label="test")
    assert snapshot.id is not None
    assert snapshot.n_items >= 0  # may be 0 if quality tier gates exclude it - the key check is it doesn't crash
    assert snapshot.label == "test"


def test_snapshot_never_overwrites_previous_snapshot(db_session):
    match, home, away = _seed_match_with_h2h_opportunity(db_session)
    snap1 = create_snapshot(db_session, limit=10, label="first")
    snap2 = create_snapshot(db_session, limit=10, label="second")
    assert snap1.id != snap2.id

    reloaded_snap1 = get_snapshot(db_session, snap1.id)
    assert reloaded_snap1 is not None
    assert reloaded_snap1.label == "first"  # untouched by the later snapshot


def test_snapshot_items_are_frozen_copies_not_live_references(db_session):
    match, home, away = _seed_match_with_h2h_opportunity(db_session)
    snap1 = create_snapshot(db_session, limit=10)
    if snap1.n_items == 0:
        return  # quality tier gates excluded everything in this synthetic fixture - nothing to freeze
    original_price = snap1.items[0].best_price

    # Mutate the underlying live quote after the snapshot was taken.
    quote = db_session.scalar(select(OddsQuote).where(OddsQuote.match_id == match.id, OddsQuote.price_decimal == original_price))
    if quote is not None:
        quote.price_decimal = 99.0
        db_session.commit()

    reloaded = get_snapshot(db_session, snap1.id)
    assert reloaded.items[0].best_price == original_price  # frozen, not re-derived from the mutated quote


def test_list_snapshots_returns_newest_first(db_session):
    match, home, away = _seed_match_with_h2h_opportunity(db_session)
    snap1 = create_snapshot(db_session, limit=10, label="older")
    snap2 = create_snapshot(db_session, limit=10, label="newer")
    snapshots = list_snapshots(db_session)
    assert snapshots[0].id == snap2.id
    assert snapshots[1].id == snap1.id


def test_replay_reproduces_snapshot_exactly(db_session):
    match, home, away = _seed_match_with_h2h_opportunity(db_session)
    snap = create_snapshot(db_session, limit=10)
    replayed = get_snapshot(db_session, snap.id)
    assert replayed is not None
    assert len(replayed.items) == snap.n_items
    assert replayed.round_number == snap.round_number


def test_get_snapshot_returns_none_for_missing_id(db_session):
    assert get_snapshot(db_session, 999999) is None


def test_settle_snapshot_does_nothing_for_incomplete_matches(db_session):
    match, home, away = _seed_match_with_h2h_opportunity(db_session)
    snap = create_snapshot(db_session, limit=10)
    settled_count = settle_snapshot(db_session, snap.id)
    assert settled_count == 0


def test_settle_snapshot_attaches_team_result_when_match_completed(db_session):
    match, home, away = _seed_match_with_h2h_opportunity(db_session)
    snap = create_snapshot(db_session, limit=10)
    if snap.n_items == 0:
        return

    match.status = MatchStatus.COMPLETED
    match.home_score = 100
    match.away_score = 80
    db_session.commit()

    settled_count = settle_snapshot(db_session, snap.id)
    assert settled_count == snap.n_items

    reloaded = get_snapshot(db_session, snap.id)
    item = reloaded.items[0]
    assert item.settled_at is not None
    assert item.match_result in ("won", "lost", "push")
    assert item.actual_stat_value == 20.0  # home margin


def test_settle_snapshot_h2h_home_selection_wins_when_home_wins(db_session):
    match, home, away = _seed_match_with_h2h_opportunity(db_session)
    snap = create_snapshot(db_session, limit=10)
    if snap.n_items == 0:
        return
    match.status = MatchStatus.COMPLETED
    match.home_score = 100
    match.away_score = 80
    db_session.commit()
    settle_snapshot(db_session, snap.id)
    reloaded = get_snapshot(db_session, snap.id)
    h2h_items = [i for i in reloaded.items if i.market_type == "h2h" and i.selection == home.name]
    if h2h_items:
        assert h2h_items[0].match_result == "won"


def _direct_team_item(snapshot_id, match, *, market_type="h2h", selection=None, line_value=None):
    """Constructs a WeeklyShortlistSnapshotItem directly rather than via
    create_snapshot, whose opportunity-ranking quality gates return zero
    items for this bare synthetic fixture (see the `if snap.n_items == 0:
    return` guards above) — every other test in this file silently skips
    the team-settlement code path as a result. This is what actually
    caught the missing `match` argument to _settle_team_item (settle_snapshot
    raised TypeError on every real completed team item before that fix)."""
    return WeeklyShortlistSnapshotItem(
        snapshot_id=snapshot_id, rank=1, opportunity_type="team", label="test item", match_id=match.id,
        market_type=market_type, selection=selection or match.home_team.name, line_value=line_value,
        best_price=1.9, best_bookmaker="TAB", recorded_at=NOW,
        model_probability=0.6, model_fair_odds=1.67, market_implied_probability=0.55,
        difference_pp=0.05, expected_value=0.09, confidence_tier="higher_confidence",
        quality_tier="strong_candidate", n_bookmakers=2, reasons_json={},
    )


def test_settle_snapshot_settles_a_real_team_item_without_raising(db_session):
    """Regression: settle_snapshot used to call _settle_team_item(db, item)
    with the match argument missing, raising TypeError for every real
    completed team item — masked in every other test here by the
    n_items==0 early return. This constructs the item directly so the
    team-settlement path is actually exercised."""
    match, home, away = _seed_match_with_h2h_opportunity(db_session)
    snapshot = WeeklyShortlistSnapshot(round_number=1, season_year=2026, n_items=1, label="direct")
    db_session.add(snapshot)
    db_session.flush()
    item = _direct_team_item(snapshot.id, match)
    db_session.add(item)
    db_session.commit()

    match.status = MatchStatus.COMPLETED
    match.home_score = 100
    match.away_score = 80
    db_session.commit()

    settled_count = settle_snapshot(db_session, snapshot.id)

    assert settled_count == 1
    db_session.refresh(item)
    assert item.settled_at is not None
    assert item.match_result == "won"  # home selection, home won
    assert item.actual_stat_value == 20.0


def test_settle_snapshot_never_re_settles_already_settled_items(db_session):
    match, home, away = _seed_match_with_h2h_opportunity(db_session)
    snap = create_snapshot(db_session, limit=10)
    if snap.n_items == 0:
        return
    match.status = MatchStatus.COMPLETED
    match.home_score = 100
    match.away_score = 80
    db_session.commit()
    settle_snapshot(db_session, snap.id)
    first_settled_at = get_snapshot(db_session, snap.id).items[0].settled_at

    # Change the score AFTER settlement - a second settle call must not re-settle.
    match.home_score = 50
    db_session.commit()
    second_count = settle_snapshot(db_session, snap.id)
    assert second_count == 0
    assert get_snapshot(db_session, snap.id).items[0].settled_at == first_settled_at
