"""Performance/correctness regression tests for the create_observations
batching fast path (app/player_modelling/prop_observation.py).

Context: a real production Live Cycle run recorded `create_observations` at
390.31 seconds while creating ZERO new observations - it was re-scanning
every historical PlayerPropMarket row for the match (thousands, since the
table is append-only) and issuing 2-4 per-quote SELECTs
(complementary-price lookup, disposal/goal projection lookup, lineup
lookup, existing-observation idempotency check) for each one. The fix
preloads all four as match-scoped maps ONCE (build_observation_match_context)
and resolves every quote purely in memory, mirroring the same pattern
already proven for prop-odds ingestion (see test_prop_odds_performance.py).

test_query_count_is_not_proportional_to_quote_count is the specific
regression guard: it fails loudly if the per-quote query pattern quietly
returns, by asserting a much larger batch doesn't produce proportionally
more queries.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import event, select

from app.models import (
    Bookmaker,
    ExpectedLineup,
    Match,
    MatchStatus,
    Player,
    PlayerDisposalProjection,
    PlayerGoalProjection,
    PlayerPropMarket,
    PropMarketObservation,
    Round,
    Season,
    Sport,
    Team,
)
from app.player_modelling.prop_observation import (
    ObservationCreationReport,
    create_observation_for_quote,
    create_observations_for_match,
)

NOW = datetime.now(timezone.utc)


@contextmanager
def count_queries(session):
    counter = {"n": 0}
    engine = session.get_bind()

    def _before_cursor_execute(*args, **kwargs):
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", _before_cursor_execute)


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
    existing_round_numbers = db.scalars(select(Round.round_number).where(Round.season_id == season.id)).all()
    round_ = Round(season_id=season.id, round_number=max(existing_round_numbers, default=0) + 1)
    home = Team(sport_id=sport.id, name=f"Home{suffix}", short_name=f"H{suffix}"[:3].upper())
    away = Team(sport_id=sport.id, name=f"Away{suffix}", short_name=f"A{suffix}"[:3].upper())
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW + timedelta(days=2), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.flush()
    bookmakers = [Bookmaker(name=f"Book{suffix}{i}") for i in range(4)]
    db.add_all(bookmakers)
    db.flush()
    db.commit()
    return match, home, away, bookmakers


def _seed_player_with_disposal_projection(db, match, team, *, suffix, predicted_mean=28.0, lineup_status="confirmed_selected", is_confirmed=True, skip_lineup=False):
    player = Player(sport_id=match.sport_id, display_name=f"Player{suffix}", source="afltables", source_player_id=f"p{suffix}", current_team_id=team.id)
    db.add(player)
    db.flush()
    db.add(PlayerDisposalProjection(
        match_id=match.id, player_id=player.id, team_id=team.id,
        model_name="disposals_nb", model_version="v1", generated_at=NOW, data_cutoff=NOW,
        lineup_status_at_generation="uncertain", games_of_history=20,
        predicted_mean=predicted_mean, distribution_method="nb", nb_alpha=8.0, confidence_tier="moderate_confidence",
    ))
    if not skip_lineup:
        db.add(ExpectedLineup(
            match_id=match.id, player_id=player.id, team_id=team.id, status="expected_in",
            selection_status=lineup_status, is_confirmed=is_confirmed, recorded_at=NOW, source="manual",
        ))
    db.commit()
    return player


def _seed_player_with_goal_projection(db, match, team, *, suffix, p_score=0.6, mu_scored=1.3):
    player = Player(sport_id=match.sport_id, display_name=f"Goaler{suffix}", source="afltables", source_player_id=f"g{suffix}", current_team_id=team.id)
    db.add(player)
    db.flush()
    db.add(PlayerGoalProjection(
        match_id=match.id, player_id=player.id, team_id=team.id,
        model_name="goals_hurdle", model_version="v1", generated_at=NOW, data_cutoff=NOW,
        lineup_status_at_generation="uncertain", games_of_history=20,
        predicted_mean=p_score * mu_scored, distribution_kind="hurdle", nb_alpha=None,
        p_score=p_score, mu_scored=mu_scored, alpha_scored=0.3, scoring_archetype="forward",
        confidence_tier="higher_confidence",
    ))
    db.add(ExpectedLineup(
        match_id=match.id, player_id=player.id, team_id=team.id, status="expected_in",
        selection_status="confirmed_selected", is_confirmed=True, recorded_at=NOW, source="manual",
    ))
    db.commit()
    return player


def _quote(match, player, bookmaker, *, threshold=21.5, selection="over", price=1.9, market_type="player_disposals", last_update=None):
    q = PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=market_type,
        line_type="over_under", threshold=threshold, selection=selection, price_decimal=price,
        recorded_at=NOW, source="the_odds_api", bookmaker_last_update=last_update or NOW,
    )
    return q


def _generate_disposal_quotes(match, players, bookmakers, n_thresholds):
    quotes = []
    for player in players:
        for bm in bookmakers:
            for t in range(n_thresholds):
                quotes.append(_quote(match, player, bm, threshold=10.5 + t, selection="over"))
    return quotes


# --- G: query-count regression guard ---------------------------------------


def test_query_count_is_not_proportional_to_quote_count(db_session):
    match, home, away, bookmakers = _seed_match(db_session, suffix="s")
    players = [_seed_player_with_disposal_projection(db_session, match, home if i % 2 == 0 else away, suffix=f"s{i}") for i in range(6)]
    small_quotes = _generate_disposal_quotes(match, players, bookmakers[:2], n_thresholds=1)  # 12 quotes
    db_session.add_all(small_quotes)
    db_session.commit()

    with count_queries(db_session) as counter:
        report = create_observations_for_match(db_session, match.id)
    small_queries = counter["n"]
    assert report.observations_created == len(small_quotes)

    match2, home2, away2, bookmakers2 = _seed_match(db_session, suffix="l")
    players2 = [_seed_player_with_disposal_projection(db_session, match2, home2 if i % 2 == 0 else away2, suffix=f"l{i}") for i in range(6)]
    large_quotes = _generate_disposal_quotes(match2, players2, bookmakers2, n_thresholds=10)  # 6*4*10 = 240 quotes
    db_session.add_all(large_quotes)
    db_session.commit()

    with count_queries(db_session) as counter2:
        report2 = create_observations_for_match(db_session, match2.id)
    large_queries = counter2["n"]
    assert report2.observations_created == len(large_quotes)

    # 20x the quotes must NOT mean anywhere close to 20x the queries.
    assert large_queries < small_queries + 30, (
        f"query count scaled with quote volume (small={small_queries} for {len(small_quotes)} quotes, "
        f"large={large_queries} for {len(large_quotes)} quotes) - the per-quote N+1 pattern may have returned"
    )
    assert large_queries < 60


def test_realistic_scale_batch_stays_well_under_query_ceiling(db_session):
    """~2,000+ quotes for one match, same order of magnitude as the real
    production match (3,958+ accumulated historical rows)."""
    match, home, away, bookmakers = _seed_match(db_session, suffix="r")
    players = [_seed_player_with_disposal_projection(db_session, match, home if i % 2 == 0 else away, suffix=f"r{i}") for i in range(12)]
    quotes = _generate_disposal_quotes(match, players, bookmakers, n_thresholds=42)  # 12*4*42 = 2016
    db_session.add_all(quotes)
    db_session.commit()

    with count_queries(db_session) as counter:
        report = create_observations_for_match(db_session, match.id)

    assert report.observations_created == len(quotes)
    assert counter["n"] < 60, f"{counter['n']} queries for {len(quotes)} quotes - expected a few dozen, not thousands"


def test_second_identical_call_creates_zero_additional_rows_and_stays_batched(db_session):
    match, home, away, bookmakers = _seed_match(db_session, suffix="i")
    players = [_seed_player_with_disposal_projection(db_session, match, home if i % 2 == 0 else away, suffix=f"i{i}") for i in range(6)]
    quotes = _generate_disposal_quotes(match, players, bookmakers[:2], n_thresholds=8)  # 96 quotes
    db_session.add_all(quotes)
    db_session.commit()

    report1 = create_observations_for_match(db_session, match.id)
    n_after_first = db_session.query(PropMarketObservation).count()
    assert report1.observations_created == len(quotes)
    assert n_after_first == len(quotes)

    with count_queries(db_session) as counter:
        report2 = create_observations_for_match(db_session, match.id)

    assert report2.observations_created == 0
    assert report2.observations_unchanged == len(quotes)
    assert db_session.query(PropMarketObservation).count() == n_after_first
    assert counter["n"] < 60  # the re-check pass must also stay batched


# --- G: equivalence between the still-existing per-quote path and the new
# batched path - the actual "matches pre-refactor behaviour" proof, using
# the UNCHANGED create_observation_for_quote as the live reference
# implementation rather than a frozen snapshot. ------------------------------


def _build_equivalence_scenario(db, suffix):
    """One match with: a disposal player with a genuine complementary
    (over/under) pair on one bookmaker, a goal player, an uncertain-lineup
    player (confidence downgrade path), and a confirmed-out player (must be
    skipped by both paths identically)."""
    match, home, away, bookmakers = _seed_match(db, suffix=suffix)
    bm = bookmakers[0]

    disposal_player = _seed_player_with_disposal_projection(db, match, home, suffix=f"{suffix}-disp")
    goal_player = _seed_player_with_goal_projection(db, match, away, suffix=f"{suffix}-goal")
    uncertain_player = _seed_player_with_disposal_projection(
        db, match, home, suffix=f"{suffix}-unc", lineup_status="uncertain", is_confirmed=False,
    )
    out_player = _seed_player_with_disposal_projection(
        db, match, away, suffix=f"{suffix}-out", lineup_status="confirmed_out", is_confirmed=False,
    )

    same_snapshot = NOW
    quotes = [
        _quote(match, disposal_player, bm, threshold=21.5, selection="over", price=1.85, last_update=same_snapshot),
        _quote(match, disposal_player, bm, threshold=21.5, selection="under", price=2.05, last_update=same_snapshot),  # complementary pair
        _quote(match, goal_player, bm, threshold=0.5, selection="over", price=2.4, market_type="player_goals", last_update=same_snapshot),
        _quote(match, uncertain_player, bm, threshold=15.5, selection="over", price=1.95, last_update=same_snapshot),
        _quote(match, out_player, bm, threshold=18.5, selection="over", price=1.9, last_update=same_snapshot),
    ]
    db.add_all(quotes)
    db.commit()
    return match, quotes


def _snapshot_fields(obs):
    """Deliberately excludes identity columns (quote_id/match_id/player_id/
    bookmaker_id) - the legacy and batched scenarios are two separately
    seeded matches with independently auto-incremented ids, so those are
    expected to differ; what must match is every DERIVED value the
    pricing/comparison math produced."""
    if obs is None:
        return None
    return {
        col: getattr(obs, col)
        for col in (
            "market_type", "line_type", "threshold",
            "source", "offered_odds", "raw_implied_probability", "devigged_probability", "overround_removed",
            "model_probability", "model_fair_odds", "predicted_mean", "model_name", "model_version",
            "confidence_tier", "selection_status_at_observation", "is_confirmed_at_observation",
            "difference_pp", "expected_value",
        )
    }


def test_batched_path_matches_legacy_per_quote_path_field_for_field(db_session):
    legacy_match, legacy_quotes = _build_equivalence_scenario(db_session, "legacy")
    legacy_report = ObservationCreationReport()
    for q in legacy_quotes:
        create_observation_for_quote(db_session, q, legacy_report)
    db_session.commit()
    legacy_by_quote_id = {
        q.id: db_session.scalar(select(PropMarketObservation).where(PropMarketObservation.quote_id == q.id))
        for q in legacy_quotes
    }

    batched_match, batched_quotes = _build_equivalence_scenario(db_session, "batched")
    batched_report = create_observations_for_match(db_session, batched_match.id)
    batched_by_index = {
        i: db_session.scalar(select(PropMarketObservation).where(PropMarketObservation.quote_id == q.id))
        for i, q in enumerate(batched_quotes)
    }
    legacy_by_index = {i: legacy_by_quote_id[q.id] for i, q in enumerate(legacy_quotes)}

    # Same aggregate outcome (one player has no projection-eligible
    # market skip path exercised here - all 5 quotes are either created or
    # skipped identically).
    assert legacy_report.observations_created == batched_report.observations_created
    assert legacy_report.skipped_confirmed_out == batched_report.skipped_confirmed_out == 1

    for i in legacy_by_index:
        legacy_fields = _snapshot_fields(legacy_by_index[i])
        batched_fields = _snapshot_fields(batched_by_index[i])
        assert legacy_fields == batched_fields, f"quote index {i} diverged between legacy and batched paths"

    # The complementary pair specifically: devig must have actually
    # triggered (proves the in-memory complementary-price index found the
    # sibling quote, not just skipped devigging silently).
    disposal_over_obs = legacy_by_index[0]
    assert disposal_over_obs.devigged_probability is not None
    assert disposal_over_obs.overround_removed is True

    # Goal-market association unchanged.
    goal_obs = legacy_by_index[2]
    assert goal_obs.market_type == "player_goals"

    # Uncertain-lineup confidence downgrade unchanged (moderate -> lower).
    uncertain_obs = legacy_by_index[3]
    assert uncertain_obs.confidence_tier == "lower_confidence"

    # Confirmed-out player produced no observation via either path.
    assert legacy_by_index[4] is None
    assert batched_by_index[4] is None
