"""Correctness regression tests for PricingSnapshot's per-family identity
fix (app/pricing/snapshot_service.py, app/models/pricing_snapshot.py).

Context: a real production forensic review (LiveCycleRun id=4's audit)
found `snapshot_price()`'s existence check omitted `player_id` from its
player-market lookup, and the database's own single UniqueConstraint
omitted it too - and because PostgreSQL treats every NULL as distinct from
every other NULL, that constraint provided no real protection either,
since player rows always have `line_value=NULL`. A full production data
audit found the existing 828 player rows were, by luck of timing, already
completely correct (same-cycle batches were accidentally protected by
`autoflush=False`, since no player's existence-check could see another
player's same-batch pending insert) - but a player priced for the first
time in a LATER, separate transaction after another player already held a
COMMITTED row at the same threshold/model_version would have been
silently skipped. That is the exact risk this file exists to close off.

A second, related bug was found and fixed while writing these tests
(exactly the kind of thing this file's own database-level tests are for):
the new team-snapshot index initially used raw `line_value` in its key,
but h2h markets always have `line_value=NULL` too - reproducing the
identical NULL-distinct bug for h2h specifically. Fixed with PostgreSQL's
native `NULLS NOT DISTINCT` (see app/models/pricing_snapshot.py) rather
than a magic sentinel value, so NULL h2h rows correctly collide while real
line/total values are unaffected. `NULLS NOT DISTINCT` is PostgreSQL-only
(no SQLite equivalent exists in SQLAlchemy's dialect support), so the one
test that exercises it directly at the database level
(test_db_rejects_duplicate_team_snapshot_including_h2h_null_line_value) is
skipped on SQLite rather than weakened to fit that limitation - consistent
with this project's standing rule that PostgreSQL partial-index semantics
require real PostgreSQL integration coverage, not a SQLite substitute.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
import pytest

from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import (
    ExpectedLineup,
    Match,
    MatchStatus,
    ModelRun,
    Player,
    PlayerDisposalProjection,
    PlayerGoalProjection,
    PricingSnapshot,
    Round,
    Season,
    Sport,
    Team,
)
from app.player_modelling.market import PlayerMarket
from app.pricing.snapshot_service import snapshot_price, snapshot_round_pricing

NOW = datetime.now(timezone.utc)


def _seed_match(db, *, suffix="a"):
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
    existing_rounds = db.scalars(select(Round.round_number).where(Round.season_id == season.id)).all()
    round_ = Round(season_id=season.id, round_number=max(existing_rounds, default=0) + 1)
    home = Team(sport_id=sport.id, name=f"Home{suffix}", short_name=f"H{suffix}"[:3].upper())
    away = Team(sport_id=sport.id, name=f"Away{suffix}", short_name=f"A{suffix}"[:3].upper())
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW + timedelta(days=1), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    return match, home, away


def _seed_model_runs(db):
    if db.scalar(select(ModelRun).where(ModelRun.model_name == "elo")) is None:
        persist_model_run(db, "elo", EloConfig(), 2022, metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 648, "holdout_value": 0.20, "naive_baseline_value": 0.25, "has_edge_over_naive": True}])
    if db.scalar(select(ModelRun).where(ModelRun.model_name == "poisson")) is None:
        persist_model_run(db, "poisson", PoissonConfig(), 2022, metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 648, "holdout_value": 0.205, "naive_baseline_value": 0.25, "has_edge_over_naive": True}])


def _seed_player(db, match, team, *, suffix, mean=25.0):
    p = Player(sport_id=match.sport_id, display_name=f"Player{suffix}", source="afltables", source_player_id=f"p{suffix}", current_team_id=team.id)
    db.add(p)
    db.flush()
    db.add(PlayerDisposalProjection(
        match_id=match.id, player_id=p.id, team_id=team.id, model_name="disposals_ridge", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
        predicted_mean=mean, distribution_method="nb", nb_alpha=3.0, confidence_tier="higher_confidence",
        warnings=[], input_features={},
    ))
    db.add(PlayerGoalProjection(
        match_id=match.id, player_id=p.id, team_id=team.id, model_name="goals_hurdle", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
        predicted_mean=0.6, distribution_kind="hurdle", nb_alpha=None,
        p_score=0.5, mu_scored=1.2, alpha_scored=0.3, scoring_archetype="forward",
        confidence_tier="higher_confidence", warnings=[], input_features={},
    ))
    db.add(ExpectedLineup(
        match_id=match.id, player_id=p.id, team_id=team.id, status="expected_in",
        selection_status="confirmed_selected", is_confirmed=True, recorded_at=NOW, source="manual",
    ))
    db.commit()
    return p


def _snap_disposal_kwargs(match, player, threshold=20.5, model_version="mv1"):
    return dict(
        db=None, match_id=match.id, player_id=player.id, market_family="player_disposals", market_type=PlayerMarket.DISPOSALS.value,
        selection="over", line_type="over_under", threshold=threshold, line_value=None,
        model_name="disposals_ridge", model_version=model_version, generated_at=NOW, data_cutoff=NOW,
        lineup_status="expected_in", confidence_tier="higher_confidence", model_probability=0.5,
    )


def _snap(db, match, player, **overrides):
    kwargs = _snap_disposal_kwargs(match, player)
    kwargs.update(overrides)
    kwargs["db"] = db
    return snapshot_price(**kwargs)


# --- Part E: application-level identity tests -------------------------------


def test_player_a_snapshot_created(db_session):
    match, home, away = _seed_match(db_session, suffix="e1")
    player_a = _seed_player(db_session, match, home, suffix="A")
    snap = _snap(db_session, match, player_a)
    db_session.commit()
    assert snap is not None
    assert snap.player_id == player_a.id


def test_player_b_same_threshold_and_model_version_also_created(db_session):
    match, home, away = _seed_match(db_session, suffix="e2")
    player_a = _seed_player(db_session, match, home, suffix="A")
    player_b = _seed_player(db_session, match, away, suffix="B")
    snap_a = _snap(db_session, match, player_a)
    snap_b = _snap(db_session, match, player_b)
    db_session.commit()
    assert snap_a is not None
    assert snap_b is not None
    assert snap_a.id != snap_b.id
    rows = db_session.scalars(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id)).all()
    assert len(rows) == 2
    assert {r.player_id for r in rows} == {player_a.id, player_b.id}


def test_repeating_player_a_is_idempotently_skipped(db_session):
    match, home, away = _seed_match(db_session, suffix="e3")
    player_a = _seed_player(db_session, match, home, suffix="A")
    first = _snap(db_session, match, player_a)
    db_session.commit()
    second = _snap(db_session, match, player_a)
    assert first is not None
    assert second is None
    rows = db_session.scalars(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id)).all()
    assert len(rows) == 1


def test_repeat_behavior_correct_after_commit(db_session):
    match, home, away = _seed_match(db_session, suffix="e4")
    player_a = _seed_player(db_session, match, home, suffix="A")
    _snap(db_session, match, player_a)
    db_session.commit()
    # A fresh existence check, well after the original commit, must still find it.
    again = _snap(db_session, match, player_a)
    assert again is None
    assert db_session.scalar(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id)) is not None


def test_repeat_behavior_correct_in_a_new_session(db_session, monkeypatch):
    """Correctness must not depend on the same Session/connection - the
    ORIGINAL bug's `autoflush=False` masking only ever applied WITHIN one
    session's uncommitted batch; this proves the fixed identity check
    works from a completely fresh Session too, mirroring a new live-cycle
    process starting a brand-new SessionLocal()."""
    from sqlalchemy.orm import sessionmaker

    match, home, away = _seed_match(db_session, suffix="e5")
    player_a = _seed_player(db_session, match, home, suffix="A")
    _snap(db_session, match, player_a)
    db_session.commit()

    NewSession = sessionmaker(bind=db_session.get_bind())
    fresh_db = NewSession()
    try:
        again = _snap(fresh_db, match, player_a)
        assert again is None
    finally:
        fresh_db.close()


def test_different_thresholds_remain_distinct(db_session):
    match, home, away = _seed_match(db_session, suffix="e6")
    player_a = _seed_player(db_session, match, home, suffix="A")
    snap_20 = _snap(db_session, match, player_a, threshold=20.5)
    snap_25 = _snap(db_session, match, player_a, threshold=25.5)
    db_session.commit()
    assert snap_20 is not None and snap_25 is not None
    assert snap_20.id != snap_25.id


def test_goal_market_identity_also_respects_player_id(db_session):
    match, home, away = _seed_match(db_session, suffix="e7")
    player_a = _seed_player(db_session, match, home, suffix="A")
    player_b = _seed_player(db_session, match, away, suffix="B")
    goal_kwargs = dict(
        match_id=match.id, market_family="player_goals", market_type=PlayerMarket.GOALS.value,
        selection="over", line_type="over_under", threshold=1.5, line_value=None,
        model_name="goals_hurdle", model_version="gv1", generated_at=NOW, data_cutoff=NOW,
        lineup_status="expected_in", confidence_tier="higher_confidence", model_probability=0.4,
    )
    snap_a = snapshot_price(db_session, player_id=player_a.id, **goal_kwargs)
    snap_b = snapshot_price(db_session, player_id=player_b.id, **goal_kwargs)
    db_session.commit()
    assert snap_a is not None and snap_b is not None
    assert snap_a.id != snap_b.id


def test_team_identity_remains_idempotent(db_session):
    """Regression: unchanged team behavior, mirrors the existing
    test_snapshot_price_is_idempotent_and_never_overwritten but confirms
    it still holds after the identity refactor."""
    match, home, away = _seed_match(db_session, suffix="e8")
    kwargs = dict(
        match_id=match.id, player_id=None, market_family="team", market_type="h2h", selection=home.name,
        line_type=None, threshold=None, line_value=None, model_name="elo_poisson", model_version="tv1",
        generated_at=NOW, data_cutoff=NOW, lineup_status=None, confidence_tier="validated_edge_over_naive",
        model_probability=0.6,
    )
    first = snapshot_price(db_session, **kwargs)
    db_session.commit()
    second = snapshot_price(db_session, **{**kwargs, "model_probability": 0.99})
    assert first is not None
    assert second is None
    rows = db_session.scalars(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id)).all()
    assert len(rows) == 1
    assert rows[0].model_probability == 0.6


def test_team_and_player_rows_cannot_collide_logically(db_session):
    """A team row and a player row sharing every other field must coexist
    fine - they live under entirely different partial indexes."""
    match, home, away = _seed_match(db_session, suffix="e9")
    player_a = _seed_player(db_session, match, home, suffix="A")
    team_snap = snapshot_price(
        db_session, match_id=match.id, player_id=None, market_family="team", market_type="h2h", selection="over",
        line_type=None, threshold=None, line_value=None, model_name="elo_poisson", model_version="shared_v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status=None, confidence_tier="validated_edge_over_naive",
        model_probability=0.5,
    )
    player_snap = _snap(db_session, match, player_a, model_version="shared_v1", threshold=None, **{})
    db_session.commit()
    assert team_snap is not None
    assert player_snap is not None


def test_cross_cycle_new_player_at_already_committed_threshold_is_not_skipped(db_session):
    """THE regression test for the exact production risk identified in the
    forensic review: player A's snapshot is committed in one 'cycle'
    (transaction), then in a LATER, separate transaction/cycle, player B
    is priced for the first time at the identical threshold/model_version.
    Before the fix, player B would have been silently skipped because the
    existence check didn't include player_id. After the fix, player B's
    row is successfully persisted."""
    match, home, away = _seed_match(db_session, suffix="e10")
    player_a = _seed_player(db_session, match, home, suffix="A")

    # "Cycle 1": player A only, committed.
    snap_a = _snap(db_session, match, player_a, model_version="crosscycle_v1")
    db_session.commit()
    assert snap_a is not None
    assert db_session.scalar(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id)).id == snap_a.id

    # "Cycle 2": a brand new player, first appearance, SEPARATE transaction,
    # same threshold/model_version player A already has a COMMITTED row for.
    player_b = _seed_player(db_session, match, away, suffix="B")
    snap_b = _snap(db_session, match, player_b, model_version="crosscycle_v1")
    db_session.commit()

    assert snap_b is not None, "player B was incorrectly skipped - the cross-cycle identity bug has returned"
    rows = db_session.scalars(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id, PricingSnapshot.model_version == "crosscycle_v1")).all()
    assert len(rows) == 2
    assert {r.player_id for r in rows} == {player_a.id, player_b.id}


# --- Part F: database-level constraint tests (real PostgreSQL semantics) ---
# These use raw SQL inserts to bypass the application layer entirely and
# prove the DATABASE ITSELF enforces the corrected identity - real
# partial-unique-index behavior, not just application logic. Run against
# whatever backend db_session is wired to (SQLite locally, real PostgreSQL
# 16/18 in CI's curated integration suites via TEST_DATABASE_URL).


def _raw_insert_player_snapshot(db, match_id, player_id, threshold, model_version):
    db.execute(text("""
        INSERT INTO pricing_snapshots
        (match_id, player_id, market_family, market_type, selection, line_type, threshold, line_value,
         model_name, model_version, generated_at, data_cutoff, confidence_tier, model_probability, model_fair_odds, created_at, updated_at)
        VALUES (:m, :p, 'player_disposals', 'player_disposals', 'over', 'over_under', :t, NULL,
         'disposals_ridge', :mv, :n, :n, 'higher_confidence', 0.5, 2.0, :n, :n)
    """), {"m": match_id, "p": player_id, "t": threshold, "mv": model_version, "n": NOW})


def _raw_insert_team_snapshot(db, match_id, market_type, selection, line_value, model_version):
    db.execute(text("""
        INSERT INTO pricing_snapshots
        (match_id, player_id, market_family, market_type, selection, line_type, threshold, line_value,
         model_name, model_version, generated_at, data_cutoff, confidence_tier, model_probability, model_fair_odds, created_at, updated_at)
        VALUES (:m, NULL, 'team', :mt, :sel, NULL, NULL, :lv,
         'elo_poisson', :mv, :n, :n, 'validated_edge_over_naive', 0.6, 1.6, :n, :n)
    """), {"m": match_id, "mt": market_type, "sel": selection, "lv": line_value, "mv": model_version, "n": NOW})


def test_db_rejects_duplicate_player_snapshot(db_session):
    match, home, away = _seed_match(db_session, suffix="f1")
    player_a = _seed_player(db_session, match, home, suffix="A")
    _raw_insert_player_snapshot(db_session, match.id, player_a.id, 20.5, "dbv1")
    db_session.commit()
    try:
        _raw_insert_player_snapshot(db_session, match.id, player_a.id, 20.5, "dbv1")
        db_session.commit()
        raised = False
    except Exception:
        db_session.rollback()
        raised = True
    assert raised, "database did not reject a genuine duplicate player snapshot"


def test_db_accepts_different_player_at_otherwise_identical_key(db_session):
    match, home, away = _seed_match(db_session, suffix="f2")
    player_a = _seed_player(db_session, match, home, suffix="A")
    player_b = _seed_player(db_session, match, away, suffix="B")
    _raw_insert_player_snapshot(db_session, match.id, player_a.id, 20.5, "dbv2")
    _raw_insert_player_snapshot(db_session, match.id, player_b.id, 20.5, "dbv2")
    db_session.commit()
    rows = db_session.scalars(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id)).all()
    assert len(rows) == 2


def test_db_accepts_different_threshold_same_player(db_session):
    match, home, away = _seed_match(db_session, suffix="f3")
    player_a = _seed_player(db_session, match, home, suffix="A")
    _raw_insert_player_snapshot(db_session, match.id, player_a.id, 20.5, "dbv3")
    _raw_insert_player_snapshot(db_session, match.id, player_a.id, 25.5, "dbv3")
    db_session.commit()
    rows = db_session.scalars(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id)).all()
    assert len(rows) == 2


def test_db_rejects_duplicate_team_snapshot_including_h2h_null_line_value(db_session):
    """The exact case this project's own required testing caught during
    implementation: h2h always has line_value=NULL, which would break a
    naive raw-line_value unique index the same way player rows broke the
    original constraint. PostgreSQL's native NULLS NOT DISTINCT fixes it -
    this proves it at the database layer, not just in application logic.
    PostgreSQL-only: SQLAlchemy has no SQLite equivalent for NULLS NOT
    DISTINCT, so this index is a plain (weaker) unique index on SQLite and
    this specific duplicate would NOT be caught there - skip rather than
    weaken the assertion to fit that limitation."""
    if db_session.get_bind().dialect.name != "postgresql":
        pytest.skip("NULLS NOT DISTINCT is PostgreSQL-only; requires TEST_DATABASE_URL pointed at real Postgres")
    match, home, away = _seed_match(db_session, suffix="f4")
    _raw_insert_team_snapshot(db_session, match.id, "h2h", home.name, None, "dbteam1")
    db_session.commit()
    try:
        _raw_insert_team_snapshot(db_session, match.id, "h2h", home.name, None, "dbteam1")
        db_session.commit()
        raised = False
    except Exception:
        db_session.rollback()
        raised = True
    assert raised, "database did not reject a genuine duplicate H2H team snapshot (NULL line_value case)"


def test_db_accepts_different_line_value_team(db_session):
    match, home, away = _seed_match(db_session, suffix="f5")
    _raw_insert_team_snapshot(db_session, match.id, "line", home.name, -12.5, "dbteam2")
    _raw_insert_team_snapshot(db_session, match.id, "line", home.name, -20.5, "dbteam2")
    db_session.commit()
    rows = db_session.scalars(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id)).all()
    assert len(rows) == 2


def test_db_team_and_player_rows_do_not_collide_through_wrong_index(db_session):
    match, home, away = _seed_match(db_session, suffix="f6")
    player_a = _seed_player(db_session, match, home, suffix="A")
    _raw_insert_team_snapshot(db_session, match.id, "h2h", home.name, None, "sharedv")
    _raw_insert_player_snapshot(db_session, match.id, player_a.id, 20.5, "sharedv")
    db_session.commit()
    rows = db_session.scalars(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id)).all()
    assert len(rows) == 2


# --- Part H: snapshot_round_pricing regression (report vs. reality) --------


def test_snapshot_round_pricing_creates_n_players_times_thresholds(db_session):
    _seed_model_runs(db_session)
    match, home, away = _seed_match(db_session, suffix="h1")
    players = [_seed_player(db_session, match, home if i % 2 == 0 else away, suffix=f"H1_{i}") for i in range(4)]

    report = snapshot_round_pricing(db_session, [match.id])

    assert report.disposal_snapshots_created == len(players) * 5  # DEFAULT_DISPOSAL_THRESHOLDS has 5 entries
    assert report.goal_snapshots_created == len(players) * 4  # DEFAULT_GOAL_THRESHOLDS has 4 entries

    committed_disposal = db_session.scalar(select(PricingSnapshot).where(
        PricingSnapshot.match_id == match.id, PricingSnapshot.market_type == PlayerMarket.DISPOSALS.value,
    ).limit(1))
    assert committed_disposal is not None
    disposal_count = len(db_session.scalars(select(PricingSnapshot).where(
        PricingSnapshot.match_id == match.id, PricingSnapshot.market_type == PlayerMarket.DISPOSALS.value,
    )).all())
    goal_count = len(db_session.scalars(select(PricingSnapshot).where(
        PricingSnapshot.match_id == match.id, PricingSnapshot.market_type == PlayerMarket.GOALS.value,
    )).all())
    assert disposal_count == report.disposal_snapshots_created, "report counter diverged from committed rows"
    assert goal_count == report.goal_snapshots_created, "report counter diverged from committed rows"
    assert len({r.player_id for r in db_session.scalars(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id, PricingSnapshot.market_type == PlayerMarket.DISPOSALS.value)).all()}) == len(players)


def test_later_cycle_new_player_adds_exactly_its_own_snapshots(db_session):
    """The end-to-end version of the cross-cycle regression test, through
    the real snapshot_round_pricing entry point rather than snapshot_price
    directly."""
    _seed_model_runs(db_session)
    match, home, away = _seed_match(db_session, suffix="h2")
    first_wave = [_seed_player(db_session, match, home, suffix=f"H2first_{i}") for i in range(3)]

    report1 = snapshot_round_pricing(db_session, [match.id])
    assert report1.disposal_snapshots_created == 3 * 5
    assert report1.goal_snapshots_created == 3 * 4

    # A later cycle: one genuinely new player joins the same match.
    new_player = _seed_player(db_session, match, away, suffix="H2new")
    report2 = snapshot_round_pricing(db_session, [match.id])

    assert report2.disposal_snapshots_created == 5, "the new player did not get exactly its own 5 disposal snapshots"
    assert report2.goal_snapshots_created == 4, "the new player did not get exactly its own 4 goal snapshots"

    all_disposal_rows = db_session.scalars(select(PricingSnapshot).where(
        PricingSnapshot.match_id == match.id, PricingSnapshot.market_type == PlayerMarket.DISPOSALS.value,
    )).all()
    assert len(all_disposal_rows) == 4 * 5
    assert new_player.id in {r.player_id for r in all_disposal_rows}


def test_rerunning_unchanged_data_creates_no_duplicates(db_session):
    _seed_model_runs(db_session)
    match, home, away = _seed_match(db_session, suffix="h3")
    players = [_seed_player(db_session, match, home, suffix=f"H3_{i}") for i in range(2)]

    report1 = snapshot_round_pricing(db_session, [match.id])
    n_after_first = len(db_session.scalars(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id)).all())

    report2 = snapshot_round_pricing(db_session, [match.id])
    n_after_second = len(db_session.scalars(select(PricingSnapshot).where(PricingSnapshot.match_id == match.id)).all())

    assert report2.disposal_snapshots_created == 0
    assert report2.goal_snapshots_created == 0
    assert n_after_second == n_after_first
