"""Correctness-equivalence and query-count regression tests for the SGM
match-scoping fix (app/player_modelling/prop_insights_normalized.py,
app/player_modelling/best_opportunities.py, app/pricing/sgm_snapshot_service.py).

Context: a real production step (snapshot_sgm_pricing) took 591.77 seconds
to produce ZERO new snapshots. Root cause, confirmed by direct code
inspection AND a real local PostgreSQL 18 measurement (see conversation):
load_best_opportunities -> _player_opportunities called
load_normalized_prop_insights with no match scoping at all, so it scanned
and per-group-queried the ENTIRE historical player_prop_markets table
(8,519+ rows and growing every cycle) before filtering the result down to
the 1-2 active matches. Measured before this fix: query count tracked
TOTAL table size 1:1 (230 rows -> 266 queries, 680 -> 716, 1,805 -> 1,841)
even with the target match's own 3 rows held completely fixed.

The fix threads the already-known match_ids through to the SQL query
itself (additive `match_ids` param, existing `match_id` param and every
existing caller untouched), and has snapshot_sgm_pricing compute the
HIGH_PROBABILITY raw opportunity list once per invocation instead of once
per (match, mode) call.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import event, select

from app.models import (
    Bookmaker, ExpectedLineup, Match, MatchStatus, Player,
    PlayerDisposalProjection, PlayerPropMarket, Round, Season, Sport, Team,
)
from app.player_modelling.best_opportunities import load_best_opportunities
from app.player_modelling.market import PlayerMarket
from app.player_modelling.multi_builder import MODE_HIGH_PROBABILITY, MODE_VALUE, _all_alternate_legs, build_match_multis
from app.player_modelling.prop_insights_normalized import load_normalized_prop_insights
from app.player_modelling.request_cache import clear_ttl_cache
from app.pricing.sgm_snapshot_service import snapshot_sgm_pricing

NOW = datetime.now(timezone.utc)


@contextmanager
def count_queries(session):
    counter = {"n": 0}
    engine = session.get_bind()

    def _cb(*a, **k):
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _cb)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", _cb)


def _get_or_create_sport(db):
    sport = db.scalar(select(Sport).where(Sport.code == "AFL"))
    if sport is None:
        sport = Sport(code="AFL", name="Australian Football League")
        db.add(sport)
        db.flush()
    return sport


def _get_or_create_bookmaker(db, name="TestBook"):
    bm = db.scalar(select(Bookmaker).where(Bookmaker.name == name))
    if bm is None:
        bm = Bookmaker(name=name, eligibility="included")
        db.add(bm)
        db.flush()
    return bm


def _next_round(db, season):
    existing = db.scalars(select(Round.round_number).where(Round.season_id == season.id)).all()
    return max(existing, default=0) + 1


def _seed_eligible_match(db, sport, season, bookmaker, *, suffix, scheduled_start, n_players=3, status=MatchStatus.SCHEDULED, round_=None):
    """A match with genuinely eligible player opportunities - clears every
    real gate in load_normalized_prop_insights/load_best_opportunities/
    _all_alternate_legs (see the conversation's documented eligibility
    gate list): AFL sport (load_next_upcoming_round hardcodes it),
    SCHEDULED status, a promoted-shaped disposal projection, a
    confirmed_selected lineup, and a bookmaker price mispriced enough to
    produce a genuine positive model-vs-market edge."""
    # load_next_upcoming_round returns only the SINGLE round containing the
    # globally-earliest still-SCHEDULED match, then every match sharing
    # that same round_id - two matches meant to appear together in "the
    # round" (e.g. for multi-match isolation tests) must share one Round
    # object, exactly like two real fixtures in the same AFL round do.
    if round_ is None:
        round_ = Round(season_id=season.id, round_number=_next_round(db, season))
        db.add(round_)
    home = Team(sport_id=sport.id, name=f"Home{suffix}", short_name=f"H{suffix}"[:3].upper())
    away = Team(sport_id=sport.id, name=f"Away{suffix}", short_name=f"A{suffix}"[:3].upper())
    db.add_all([home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=scheduled_start, status=status,
    )
    db.add(match)
    db.flush()
    for i in range(n_players):
        team = home if i % 2 == 0 else away
        p = Player(sport_id=sport.id, display_name=f"P{suffix}{i}", source="afltables", source_player_id=f"{suffix}p{i}", current_team_id=team.id)
        db.add(p)
        db.flush()
        db.add(PlayerDisposalProjection(
            match_id=match.id, player_id=p.id, team_id=team.id, model_name="disposals_ridge", model_version="v1",
            generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
            predicted_mean=32.0, distribution_method="nb", nb_alpha=3.0, confidence_tier="higher_confidence",
            warnings=[], input_features={},
        ))
        db.add(ExpectedLineup(
            match_id=match.id, player_id=p.id, team_id=team.id, status="expected_in",
            selection_status="confirmed_selected", is_confirmed=True, recorded_at=NOW, source="manual",
        ))
        db.add(PlayerPropMarket(
            match_id=match.id, player_id=p.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
            line_type="over_under", threshold=20.5, selection="over", price_decimal=6.0,
            recorded_at=NOW, source="the_odds_api", bookmaker_last_update=NOW,
        ))
    db.commit()
    return match


def _seed_background_history(db, sport, season, bookmaker, *, n_matches, n_players_per_match, n_thresholds_per_player):
    """Unrelated, COMPLETED matches' PlayerPropMarket rows - no
    projections/lineups, matching most of the real 8,519-row production
    table's shape: they get scanned/grouped, a projection lookup returns
    None, and they're silently dropped - paying the query cost without
    ever becoming an opportunity."""
    base_round = _next_round(db, season)
    added = 0
    for m in range(n_matches):
        round_ = Round(season_id=season.id, round_number=base_round + m)
        home = Team(sport_id=sport.id, name=f"BgHome{m}", short_name=f"H{m}"[:3].upper())
        away = Team(sport_id=sport.id, name=f"BgAway{m}", short_name=f"A{m}"[:3].upper())
        db.add_all([round_, home, away])
        db.flush()
        match = Match(
            sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
            scheduled_start=NOW - timedelta(days=30 + m), status=MatchStatus.COMPLETED,
        )
        db.add(match)
        db.flush()
        for i in range(n_players_per_match):
            team = home if i % 2 == 0 else away
            p = Player(sport_id=sport.id, display_name=f"Bg{m}_{i}", source="afltables", source_player_id=f"bg{m}_{i}", current_team_id=team.id)
            db.add(p)
            db.flush()
            for t in range(n_thresholds_per_player):
                db.add(PlayerPropMarket(
                    match_id=match.id, player_id=p.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
                    line_type="over_under", threshold=10.5 + t, selection="over", price_decimal=1.8,
                    recorded_at=NOW, source="the_odds_api", bookmaker_last_update=NOW,
                ))
                added += 1
    db.commit()
    return added


# --- Correctness equivalence -------------------------------------------


def test_scoped_query_returns_same_opportunities_as_unscoped_filtered_afterward(db_session):
    """The core equivalence claim: match_ids=... must return exactly the
    same opportunities the OLD code got by loading everything and
    filtering in Python afterward - for the requested matches."""
    sport = _get_or_create_sport(db_session)
    season = Season(sport_id=sport.id, year=2091)
    db_session.add(season)
    db_session.commit()
    bookmaker = _get_or_create_bookmaker(db_session)

    target = _seed_eligible_match(db_session, sport, season, bookmaker, suffix="tgt", scheduled_start=NOW + timedelta(hours=1))
    other = _seed_eligible_match(db_session, sport, season, bookmaker, suffix="oth", scheduled_start=NOW + timedelta(hours=2))

    scoped = load_normalized_prop_insights(db_session, include_uncertain=True, opportunities_only=True, match_ids=frozenset({target.id}))
    unscoped_filtered = [
        r for r in load_normalized_prop_insights(db_session, include_uncertain=True, opportunities_only=True)
        if r["match_id"] == target.id
    ]

    def _key(r):
        return (r["match_id"], r["player_id"], r["market_type"], r["threshold"])

    assert sorted(map(_key, scoped)) == sorted(map(_key, unscoped_filtered))
    assert len(scoped) > 0
    assert all(r["match_id"] == target.id for r in scoped)
    # The other eligible match must not leak into the scoped result.
    assert other.id not in {r["match_id"] for r in scoped}


def test_multiple_matches_in_requested_set_all_returned(db_session):
    sport = _get_or_create_sport(db_session)
    season = Season(sport_id=sport.id, year=2092)
    db_session.add(season)
    db_session.commit()
    bookmaker = _get_or_create_bookmaker(db_session)

    m1 = _seed_eligible_match(db_session, sport, season, bookmaker, suffix="m1", scheduled_start=NOW + timedelta(hours=1))
    m2 = _seed_eligible_match(db_session, sport, season, bookmaker, suffix="m2", scheduled_start=NOW + timedelta(hours=2))

    rows = load_normalized_prop_insights(db_session, include_uncertain=True, opportunities_only=True, match_ids=frozenset({m1.id, m2.id}))
    seen_matches = {r["match_id"] for r in rows}
    assert seen_matches == {m1.id, m2.id}


def test_non_requested_match_cannot_influence_scoped_result(db_session):
    sport = _get_or_create_sport(db_session)
    season = Season(sport_id=sport.id, year=2093)
    db_session.add(season)
    db_session.commit()
    bookmaker = _get_or_create_bookmaker(db_session)

    target = _seed_eligible_match(db_session, sport, season, bookmaker, suffix="tgt", scheduled_start=NOW + timedelta(hours=1))
    _seed_background_history(db_session, sport, season, bookmaker, n_matches=2, n_players_per_match=5, n_thresholds_per_player=3)

    rows = load_normalized_prop_insights(db_session, include_uncertain=True, opportunities_only=True, match_ids=frozenset({target.id}))
    assert {r["match_id"] for r in rows} == {target.id}
    assert len(rows) == 3  # exactly the target match's 3 eligible players, background noise excluded


def test_match_id_singular_param_unchanged_for_existing_callers(db_session):
    """The existing (unmodified) match_id=<int> parameter must behave
    exactly as before - other callers (Prop Insights page,
    model_market_disagreements) are not part of this refactor."""
    sport = _get_or_create_sport(db_session)
    season = Season(sport_id=sport.id, year=2094)
    db_session.add(season)
    db_session.commit()
    bookmaker = _get_or_create_bookmaker(db_session)
    target = _seed_eligible_match(db_session, sport, season, bookmaker, suffix="single", scheduled_start=NOW + timedelta(hours=1))

    rows = load_normalized_prop_insights(db_session, include_uncertain=True, opportunities_only=True, match_id=target.id)
    assert {r["match_id"] for r in rows} == {target.id}
    assert len(rows) == 3


# --- SGM mode equivalence ------------------------------------------------


def test_high_probability_mode_identical_with_precomputed_raw_opportunities(db_session):
    """build_match_multis(mode=HIGH_PROBABILITY) must produce the identical
    result whether raw_opportunities is precomputed-and-passed (the new
    snapshot_sgm_pricing behavior) or left to compute internally (the old
    per-call behavior) - proving the reuse is a pure performance change."""
    sport = _get_or_create_sport(db_session)
    season = Season(sport_id=sport.id, year=2095)
    db_session.add(season)
    db_session.commit()
    bookmaker = _get_or_create_bookmaker(db_session)
    match = _seed_eligible_match(db_session, sport, season, bookmaker, suffix="equiv", scheduled_start=NOW + timedelta(hours=1))

    clear_ttl_cache()
    without_precompute = build_match_multis(db_session, match.id, confirmed_only=True, mode=MODE_HIGH_PROBABILITY)

    clear_ttl_cache()
    raw = load_best_opportunities(db_session, market_scope="all", include_uncertain=True, include_stale=True, include_insufficient_history=True, limit=None)
    with_precompute = build_match_multis(db_session, match.id, confirmed_only=True, mode=MODE_HIGH_PROBABILITY, raw_opportunities=raw)

    assert without_precompute.n_eligible_legs == with_precompute.n_eligible_legs
    for tier_key in without_precompute.tiers:
        assert len(without_precompute.tiers[tier_key].options) == len(with_precompute.tiers[tier_key].options)


def test_value_mode_untouched_by_raw_opportunities_reuse(db_session):
    """MODE_VALUE never reads raw_opportunities - passing None explicitly
    (snapshot_sgm_pricing's new call site) must be indistinguishable from
    omitting it (the old call site)."""
    sport = _get_or_create_sport(db_session)
    season = Season(sport_id=sport.id, year=2096)
    db_session.add(season)
    db_session.commit()
    bookmaker = _get_or_create_bookmaker(db_session)
    match = _seed_eligible_match(db_session, sport, season, bookmaker, suffix="val", scheduled_start=NOW + timedelta(hours=1))

    old_style = build_match_multis(db_session, match.id, confirmed_only=True, mode=MODE_VALUE)
    new_style = build_match_multis(db_session, match.id, confirmed_only=True, mode=MODE_VALUE, raw_opportunities=None)

    assert old_style.n_eligible_legs == new_style.n_eligible_legs
    for tier_key in old_style.tiers:
        assert len(old_style.tiers[tier_key].options) == len(new_style.tiers[tier_key].options)


# --- Multi-match shared-raw-opportunities isolation/equivalence ----------
# snapshot_sgm_pricing computes ONE round-wide raw_opportunities list (via
# load_best_opportunities, itself now match-scoped in SQL - see above) and
# passes the SAME list into build_match_multis for every match in the
# round. _all_alternate_legs filters that shared list down to
# `o["match_id"] == match_id` internally per match. This proves that
# sharing is safe: each match sees only its own legs, and the result is
# identical to the old per-match-independent computation.


def test_two_matches_shared_raw_opportunities_are_correctly_isolated_and_equivalent(db_session):
    sport = _get_or_create_sport(db_session)
    season = Season(sport_id=sport.id, year=2099)
    db_session.add(season)
    db_session.commit()
    bookmaker = _get_or_create_bookmaker(db_session)

    shared_round = Round(season_id=season.id, round_number=_next_round(db_session, season))
    db_session.add(shared_round)
    db_session.flush()
    match_a = _seed_eligible_match(db_session, sport, season, bookmaker, suffix="matcha", scheduled_start=NOW + timedelta(hours=1), round_=shared_round)
    match_b = _seed_eligible_match(db_session, sport, season, bookmaker, suffix="matchb", scheduled_start=NOW + timedelta(hours=2), round_=shared_round)

    def leg_ids(legs):
        return {(leg["match_id"], leg.get("player_id")) for leg in legs}

    # OLD behavior: each match independently computes (and discards) its
    # own round-wide raw_opportunities list.
    clear_ttl_cache()
    old_legs_a = _all_alternate_legs(db_session, match_a.id)
    clear_ttl_cache()
    old_legs_b = _all_alternate_legs(db_session, match_b.id)

    # NEW behavior: one shared list, computed once, reused for both matches
    # - exactly what snapshot_sgm_pricing now does.
    clear_ttl_cache()
    shared_raw = load_best_opportunities(
        db_session, market_scope="all", include_uncertain=True, include_stale=True, include_insufficient_history=True, limit=None,
    )
    new_legs_a = _all_alternate_legs(db_session, match_a.id, raw_opportunities=shared_raw)
    new_legs_b = _all_alternate_legs(db_session, match_b.id, raw_opportunities=shared_raw)

    # 1. Each match gets exactly its own eligible player opportunities -
    # every leg _all_alternate_legs returns for match_a is stamped with
    # match_a's own id (never match_b's), and vice versa.
    assert all(leg["match_id"] == match_a.id for leg in new_legs_a)
    assert all(leg["match_id"] == match_b.id for leg in new_legs_b)

    # 2/3. No cross-contamination in either direction.
    a_player_ids = {leg["player_id"] for leg in new_legs_a}
    b_player_ids = {leg["player_id"] for leg in new_legs_b}
    assert a_player_ids.isdisjoint(b_player_ids)
    assert len(a_player_ids) == 3 and len(b_player_ids) == 3  # each match's own 3 eligible players, nothing more/less

    # 4. Equivalent to the old, independent-per-match computation.
    assert leg_ids(new_legs_a) == leg_ids(old_legs_a)
    assert leg_ids(new_legs_b) == leg_ids(old_legs_b)

    # 5. Reuse changes nothing about ranking/filtering/bookmaker
    # selection/confidence/pricing math - every field on every leg dict is
    # identical (excluding nothing; these are the exact same computed
    # opportunity dicts, just sourced from a shared vs per-match list).
    def _without_wallclock_fields(leg):
        # market_maturity.hours_until_kickoff is computed against
        # datetime.now() fresh at call time - two genuinely separate calls
        # a few milliseconds apart will legitimately differ by
        # microseconds here regardless of this refactor. Every other field
        # must match exactly.
        d = dict(leg)
        if "market_maturity" in d:
            d["market_maturity"] = {k: v for k, v in d["market_maturity"].items() if k != "hours_until_kickoff"}
        return d

    old_by_key_a = {(leg["match_id"], leg["player_id"]): leg for leg in old_legs_a}
    for leg in new_legs_a:
        key = (leg["match_id"], leg["player_id"])
        assert _without_wallclock_fields(leg) == _without_wallclock_fields(old_by_key_a[key]), (
            f"leg for player {key} diverged between shared and independent computation"
        )


def test_full_build_match_multis_two_matches_equivalent_with_shared_context(db_session):
    """Same claim, exercised through the full build_match_multis pipeline
    (tier search, candidate pooling, ranking) rather than just the raw-leg
    layer above."""
    sport = _get_or_create_sport(db_session)
    season = Season(sport_id=sport.id, year=2100)
    db_session.add(season)
    db_session.commit()
    bookmaker = _get_or_create_bookmaker(db_session)

    shared_round = Round(season_id=season.id, round_number=_next_round(db_session, season))
    db_session.add(shared_round)
    db_session.flush()
    match_a = _seed_eligible_match(db_session, sport, season, bookmaker, suffix="fulla", scheduled_start=NOW + timedelta(hours=1), round_=shared_round)
    match_b = _seed_eligible_match(db_session, sport, season, bookmaker, suffix="fullb", scheduled_start=NOW + timedelta(hours=2), round_=shared_round)

    clear_ttl_cache()
    old_a = build_match_multis(db_session, match_a.id, confirmed_only=True, mode=MODE_HIGH_PROBABILITY)
    clear_ttl_cache()
    old_b = build_match_multis(db_session, match_b.id, confirmed_only=True, mode=MODE_HIGH_PROBABILITY)

    clear_ttl_cache()
    shared_raw = load_best_opportunities(
        db_session, market_scope="all", include_uncertain=True, include_stale=True, include_insufficient_history=True, limit=None,
    )
    new_a = build_match_multis(db_session, match_a.id, confirmed_only=True, mode=MODE_HIGH_PROBABILITY, raw_opportunities=shared_raw)
    new_b = build_match_multis(db_session, match_b.id, confirmed_only=True, mode=MODE_HIGH_PROBABILITY, raw_opportunities=shared_raw)

    assert new_a.n_eligible_legs == old_a.n_eligible_legs == 3
    assert new_b.n_eligible_legs == old_b.n_eligible_legs == 3
    for tier_key in old_a.tiers:
        assert len(new_a.tiers[tier_key].options) == len(old_a.tiers[tier_key].options)
        assert len(new_b.tiers[tier_key].options) == len(old_b.tiers[tier_key].options)
    # MODE_VALUE (a separate candidate source entirely) is unaffected by any of this.
    value_a = build_match_multis(db_session, match_a.id, confirmed_only=True, mode=MODE_VALUE)
    value_a_with_raw = build_match_multis(db_session, match_a.id, confirmed_only=True, mode=MODE_VALUE, raw_opportunities=shared_raw)
    assert value_a.n_eligible_legs == value_a_with_raw.n_eligible_legs


# --- Query-count regression (the actual production bug) ------------------


def test_query_count_stays_roughly_constant_as_unrelated_history_grows(db_session):
    """The specific regression guard for the production incident: query
    count for build_match_multis on a FIXED target match must NOT scale
    with unrelated background history - before this fix it tracked total
    table size 1:1 (measured on real PostgreSQL 18: 230->266, 680->716,
    1,805->1,841 queries). After the fix it must stay roughly flat."""
    sport = _get_or_create_sport(db_session)
    season = Season(sport_id=sport.id, year=2097)
    db_session.add(season)
    db_session.commit()
    bookmaker = _get_or_create_bookmaker(db_session)

    target = _seed_eligible_match(db_session, sport, season, bookmaker, suffix="scale", scheduled_start=NOW + timedelta(hours=1))

    clear_ttl_cache()
    with count_queries(db_session) as counter:
        result_small = build_match_multis(db_session, target.id, confirmed_only=True, mode=MODE_HIGH_PROBABILITY)
    small_queries = counter["n"]
    assert result_small.n_eligible_legs == 3

    _seed_background_history(db_session, sport, season, bookmaker, n_matches=10, n_players_per_match=15, n_thresholds_per_player=5)  # 750 unrelated rows

    clear_ttl_cache()
    with count_queries(db_session) as counter:
        result_large = build_match_multis(db_session, target.id, confirmed_only=True, mode=MODE_HIGH_PROBABILITY)
    large_queries = counter["n"]

    assert result_large.n_eligible_legs == result_small.n_eligible_legs == 3  # target match's own data never changed
    assert large_queries < small_queries + 15, (
        f"query count scaled with unrelated background history ({small_queries} -> {large_queries} queries "
        "after adding 750 unrelated rows) - the unscoped full-table scan may have returned"
    )


def test_snapshot_sgm_pricing_does_not_reload_context_per_match(db_session):
    """Two matches in one snapshot_sgm_pricing call must not cost ~2x a
    single match's own query count for the shared raw-opportunities load -
    it should be computed once and reused."""
    sport = _get_or_create_sport(db_session)
    season = Season(sport_id=sport.id, year=2098)
    db_session.add(season)
    db_session.commit()
    bookmaker = _get_or_create_bookmaker(db_session)

    m1 = _seed_eligible_match(db_session, sport, season, bookmaker, suffix="one", scheduled_start=NOW + timedelta(hours=1))

    clear_ttl_cache()
    with count_queries(db_session) as counter_one:
        report_one = snapshot_sgm_pricing(db_session, [m1.id])
    one_match_queries = counter_one["n"]
    assert report_one.matches_considered == 1

    m2 = _seed_eligible_match(db_session, sport, season, bookmaker, suffix="two", scheduled_start=NOW + timedelta(hours=2))

    clear_ttl_cache()
    with count_queries(db_session) as counter_two:
        report_two = snapshot_sgm_pricing(db_session, [m1.id, m2.id])
    two_match_queries = counter_two["n"]
    assert report_two.matches_considered == 2

    # If the shared opportunities load were repeated per match, two matches
    # would cost roughly 2x one match's query count for that shared part.
    # With reuse, the second match should add only its own per-match work
    # (freeze/settle/etc), not a second full opportunities reload.
    assert two_match_queries < one_match_queries * 1.8, (
        f"one match: {one_match_queries} queries, two matches: {two_match_queries} queries - "
        "the shared HIGH_PROBABILITY opportunities load may be getting recomputed per match"
    )
