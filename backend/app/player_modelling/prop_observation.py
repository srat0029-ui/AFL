"""Creates PropMarketObservation rows — one frozen snapshot per (real
quote, model state) pairing (Sections 1-4 of the market-logging stage
brief). Reuses the exact same model-probability/comparison math
app/player_modelling/prop_insights_normalized.py uses (disposal/goal
distributions, compare_model_to_market, confidence downgrade) so an
observation's numbers always match what Prop Insights actually showed a
user at the time — never a separately-computed, potentially-diverging copy.

Idempotency (Section 4): a quote row is itself already a unique historical
snapshot (PlayerPropMarket is append-only — a new row only appears when the
bookmaker's price genuinely changed, see prop_odds_ingestion.py). So the
one remaining thing that can change WITHOUT a new quote row appearing is
the model's own belief (a later refresh-live regenerates the projection in
place). An observation is therefore keyed on (quote_id, model_version,
data_cutoff): re-processing the same quote against an unchanged projection
is a no-op; re-processing it after the projection has genuinely moved
(different model_version OR different data_cutoff) creates a new
observation for the SAME quote, capturing "the model changed its mind
about a price that hadn't moved" - itself useful evidence.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ExpectedLineup,
    PlayerDisposalProjection,
    PlayerGoalProjection,
    PlayerPropMarket,
    PropMarketObservation,
)
from app.player_modelling.live_report_query import (
    EXPECTED_IN_SELECTION_STATUSES,
    current_lineup_for,
    disposal_distribution_for,
    downgrade_confidence,
    goal_distribution_for,
    price_line,
)
from app.player_modelling.market import PlayerMarket
from app.player_modelling.prop_math import compare_model_to_market
from app.player_modelling.prop_insights_normalized import COMPLEMENTARY_SELECTIONS, PRIMARY_SELECTIONS


@dataclass
class ObservationCreationReport:
    quotes_considered: int = 0
    observations_created: int = 0
    observations_unchanged: int = 0
    skipped_manual_source: int = 0
    skipped_complementary_side: int = 0
    skipped_no_projection: int = 0
    skipped_confirmed_out: int = 0
    skipped_unsupported_market: int = 0


def _same_instant(a: datetime, b: datetime) -> bool:
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return a == b


def _find_paired_complementary_price(db: Session, quote: PlayerPropMarket) -> float | None:
    """Same bookmaker, same snapshot (bookmaker_last_update), opposite side
    - the same "only devig within one bookmaker's own paired snapshot" rule
    prop_insights_normalized.py uses, just keyed to one specific historical
    quote instead of "the current latest"."""
    paired = db.scalar(
        select(PlayerPropMarket).where(
            PlayerPropMarket.match_id == quote.match_id,
            PlayerPropMarket.player_id == quote.player_id,
            PlayerPropMarket.bookmaker_id == quote.bookmaker_id,
            PlayerPropMarket.market_type == quote.market_type,
            PlayerPropMarket.line_type == quote.line_type,
            PlayerPropMarket.threshold == quote.threshold,
            PlayerPropMarket.selection.in_(COMPLEMENTARY_SELECTIONS),
            PlayerPropMarket.bookmaker_last_update == quote.bookmaker_last_update,
        )
    )
    return paired.price_decimal if paired is not None else None


def create_observation_for_quote(db: Session, quote: PlayerPropMarket, report: ObservationCreationReport) -> PropMarketObservation | None:
    report.quotes_considered += 1

    if quote.source == "manual":
        report.skipped_manual_source += 1
        return None
    if quote.selection not in PRIMARY_SELECTIONS:
        report.skipped_complementary_side += 1
        return None

    if quote.market_type == PlayerMarket.DISPOSALS.value:
        proj = db.scalar(
            select(PlayerDisposalProjection).where(
                PlayerDisposalProjection.match_id == quote.match_id, PlayerDisposalProjection.player_id == quote.player_id
            )
        )
        dist = disposal_distribution_for(proj) if proj else None
    elif quote.market_type == PlayerMarket.GOALS.value:
        proj = db.scalar(
            select(PlayerGoalProjection).where(
                PlayerGoalProjection.match_id == quote.match_id, PlayerGoalProjection.player_id == quote.player_id
            )
        )
        dist = goal_distribution_for(proj) if proj else None
    else:
        report.skipped_unsupported_market += 1
        return None

    if proj is None:
        report.skipped_no_projection += 1
        return None

    current_lineup = current_lineup_for(db, quote.player_id, quote.match_id)
    selection_status = current_lineup.selection_status if current_lineup else "uncertain"
    is_confirmed = current_lineup.is_confirmed if current_lineup else False
    if selection_status == "confirmed_out":
        report.skipped_confirmed_out += 1
        return None

    is_uncertain_participation = not is_confirmed and selection_status not in EXPECTED_IN_SELECTION_STATUSES
    confidence_tier = downgrade_confidence(proj.confidence_tier) if is_uncertain_participation else proj.confidence_tier

    existing = db.scalar(
        select(PropMarketObservation)
        .where(PropMarketObservation.quote_id == quote.id)
        .order_by(PropMarketObservation.created_at.desc())
        .limit(1)
    )
    if existing is not None and existing.model_version == proj.model_version and _same_instant(existing.data_cutoff, proj.data_cutoff):
        report.observations_unchanged += 1
        return existing

    model_probability = price_line(dist, quote.threshold, quote.line_type)
    opposite_odds = _find_paired_complementary_price(db, quote)
    comparison = compare_model_to_market(model_probability, quote.price_decimal, opposite_side_odds=opposite_odds)

    observation = PropMarketObservation(
        quote_id=quote.id,
        match_id=quote.match_id,
        player_id=quote.player_id,
        bookmaker_id=quote.bookmaker_id,
        market_type=quote.market_type,
        line_type=quote.line_type,
        threshold=quote.threshold,
        source=quote.source,
        offered_odds=quote.price_decimal,
        observed_at=quote.recorded_at,
        bookmaker_last_update=quote.bookmaker_last_update,
        raw_implied_probability=comparison.raw_implied_probability,
        devigged_probability=comparison.devigged_probability,
        overround_removed=comparison.overround_removed,
        model_probability=comparison.model_probability,
        model_fair_odds=comparison.model_fair_odds,
        predicted_mean=proj.predicted_mean,
        model_name=proj.model_name,
        model_version=proj.model_version,
        data_cutoff=proj.data_cutoff,
        confidence_tier=confidence_tier,
        selection_status_at_observation=selection_status,
        is_confirmed_at_observation=is_confirmed,
        difference_pp=comparison.difference_pp,
        expected_value=comparison.expected_value,
    )
    db.add(observation)
    db.flush()
    report.observations_created += 1
    return observation


@dataclass
class ObservationMatchContext:
    """Match-scoped preload of everything create_observation_for_quote
    would otherwise fetch per-quote - built in a small, fixed number of
    queries regardless of how many quotes the match has (see
    build_observation_match_context), exactly mirroring the
    MatchResolutionContext pattern already proven for prop-odds ingestion
    (app/player_modelling/prop_player_resolution.py)."""

    disposal_projection_by_player: dict[int, PlayerDisposalProjection] = field(default_factory=dict)
    goal_projection_by_player: dict[int, PlayerGoalProjection] = field(default_factory=dict)
    lineup_by_player: dict[int, ExpectedLineup] = field(default_factory=dict)
    # Only the two fields the idempotency check actually needs, keyed by
    # quote_id - a plain dict (not the ORM row) so it can hold either an
    # already-persisted observation's values or a not-yet-inserted one
    # prepared earlier in the SAME batch, identically to how
    # prop_odds_ingestion.py's existing_by_key doubles as both.
    latest_observation_by_quote_id: dict[int, dict] = field(default_factory=dict)
    # Keyed by (player_id, bookmaker_id, market_type, line_type, threshold,
    # bookmaker_last_update) from every COMPLEMENTARY-side quote already
    # loaded for this match - replaces _find_paired_complementary_price's
    # per-quote query. If more than one complementary-side row ever shares
    # an identical key (bookmaker_last_update should already make this a
    # genuine data anomaly, not a normal case), the last one encountered
    # wins - the original per-quote db.scalar() call had no explicit
    # ordering either, so this doesn't weaken an existing guarantee.
    complementary_price_by_key: dict[tuple, float] = field(default_factory=dict)


def build_observation_match_context(db: Session, match_id: int, quotes: list[PlayerPropMarket]) -> ObservationMatchContext:
    """Four fixed queries (disposal projections, goal projections, expected
    lineups, existing observations) regardless of quote count - `quotes`
    must already be every PlayerPropMarket row for this match (the caller
    already loaded it to iterate over), so the complementary-price index
    costs no extra query at all."""
    disposal_projection_by_player = {
        p.player_id: p
        for p in db.scalars(select(PlayerDisposalProjection).where(PlayerDisposalProjection.match_id == match_id)).all()
    }
    goal_projection_by_player = {
        p.player_id: p
        for p in db.scalars(select(PlayerGoalProjection).where(PlayerGoalProjection.match_id == match_id)).all()
    }
    lineup_by_player = {
        lu.player_id: lu for lu in db.scalars(select(ExpectedLineup).where(ExpectedLineup.match_id == match_id)).all()
    }

    quote_ids = [q.id for q in quotes]
    latest_observation_by_quote_id: dict[int, dict] = {}
    if quote_ids:
        for obs in db.scalars(
            select(PropMarketObservation)
            .where(PropMarketObservation.quote_id.in_(quote_ids))
            .order_by(PropMarketObservation.created_at.asc())
        ).all():
            # Ascending order + last-write-wins reproduces the original
            # per-quote query's "ORDER BY created_at DESC LIMIT 1".
            latest_observation_by_quote_id[obs.quote_id] = {
                "model_version": obs.model_version, "data_cutoff": obs.data_cutoff,
            }

    complementary_price_by_key: dict[tuple, float] = {}
    for q in quotes:
        if q.selection in COMPLEMENTARY_SELECTIONS:
            key = (q.player_id, q.bookmaker_id, q.market_type, q.line_type, q.threshold, q.bookmaker_last_update)
            complementary_price_by_key[key] = q.price_decimal

    return ObservationMatchContext(
        disposal_projection_by_player=disposal_projection_by_player,
        goal_projection_by_player=goal_projection_by_player,
        lineup_by_player=lineup_by_player,
        latest_observation_by_quote_id=latest_observation_by_quote_id,
        complementary_price_by_key=complementary_price_by_key,
    )


def _prepare_observation(
    context: ObservationMatchContext, quote: PlayerPropMarket, report: ObservationCreationReport
) -> dict | None:
    """In-memory reimplementation of create_observation_for_quote's exact
    logic - identical skip/eligibility rules, identical idempotency rule,
    identical model-probability/comparison math - purely against
    `context`'s preloaded maps instead of per-quote queries. Returns an
    insert-ready dict (never adds/flushes) so the caller can bulk-insert
    every match's new rows in one statement."""
    report.quotes_considered += 1

    if quote.source == "manual":
        report.skipped_manual_source += 1
        return None
    if quote.selection not in PRIMARY_SELECTIONS:
        report.skipped_complementary_side += 1
        return None

    if quote.market_type == PlayerMarket.DISPOSALS.value:
        proj = context.disposal_projection_by_player.get(quote.player_id)
        dist = disposal_distribution_for(proj) if proj else None
    elif quote.market_type == PlayerMarket.GOALS.value:
        proj = context.goal_projection_by_player.get(quote.player_id)
        dist = goal_distribution_for(proj) if proj else None
    else:
        report.skipped_unsupported_market += 1
        return None

    if proj is None:
        report.skipped_no_projection += 1
        return None

    current_lineup = context.lineup_by_player.get(quote.player_id)
    selection_status = current_lineup.selection_status if current_lineup else "uncertain"
    is_confirmed = current_lineup.is_confirmed if current_lineup else False
    if selection_status == "confirmed_out":
        report.skipped_confirmed_out += 1
        return None

    is_uncertain_participation = not is_confirmed and selection_status not in EXPECTED_IN_SELECTION_STATUSES
    confidence_tier = downgrade_confidence(proj.confidence_tier) if is_uncertain_participation else proj.confidence_tier

    existing = context.latest_observation_by_quote_id.get(quote.id)
    if existing is not None and existing["model_version"] == proj.model_version and _same_instant(existing["data_cutoff"], proj.data_cutoff):
        report.observations_unchanged += 1
        return None

    model_probability = price_line(dist, quote.threshold, quote.line_type)
    key = (quote.player_id, quote.bookmaker_id, quote.market_type, quote.line_type, quote.threshold, quote.bookmaker_last_update)
    opposite_odds = context.complementary_price_by_key.get(key)
    comparison = compare_model_to_market(model_probability, quote.price_decimal, opposite_side_odds=opposite_odds)

    report.observations_created += 1
    return dict(
        quote_id=quote.id,
        match_id=quote.match_id,
        player_id=quote.player_id,
        bookmaker_id=quote.bookmaker_id,
        market_type=quote.market_type,
        line_type=quote.line_type,
        threshold=quote.threshold,
        source=quote.source,
        offered_odds=quote.price_decimal,
        observed_at=quote.recorded_at,
        bookmaker_last_update=quote.bookmaker_last_update,
        raw_implied_probability=comparison.raw_implied_probability,
        devigged_probability=comparison.devigged_probability,
        overround_removed=comparison.overround_removed,
        model_probability=comparison.model_probability,
        model_fair_odds=comparison.model_fair_odds,
        predicted_mean=proj.predicted_mean,
        model_name=proj.model_name,
        model_version=proj.model_version,
        data_cutoff=proj.data_cutoff,
        confidence_tier=confidence_tier,
        selection_status_at_observation=selection_status,
        is_confirmed_at_observation=is_confirmed,
        difference_pp=comparison.difference_pp,
        expected_value=comparison.expected_value,
    )


def create_observations_for_match(db: Session, match_id: int) -> ObservationCreationReport:
    report = ObservationCreationReport()
    quotes = db.scalars(select(PlayerPropMarket).where(PlayerPropMarket.match_id == match_id)).all()
    if not quotes:
        db.commit()
        return report

    context = build_observation_match_context(db, match_id, quotes)
    new_rows: list[dict] = []
    for quote in quotes:
        row = _prepare_observation(context, quote, report)
        if row is not None:
            new_rows.append(row)
    if new_rows:
        db.execute(PropMarketObservation.__table__.insert(), new_rows)
    db.commit()
    return report


def create_observations_for_quotes(db: Session, quotes: list[PlayerPropMarket]) -> ObservationCreationReport:
    """Used right after a refresh-prop-odds run to only process the quotes
    that run actually touched, rather than re-scanning every historical
    quote for the match every time (still cheap either way thanks to the
    idempotency check, but this is the natural call site - see
    app/player_modelling/prop_odds_ingestion.py)."""
    report = ObservationCreationReport()
    for quote in quotes:
        create_observation_for_quote(db, quote, report)
    db.commit()
    return report
