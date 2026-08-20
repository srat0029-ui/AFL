"""Live player-projection API — Section 21 of the live-projection brief.
Read endpoints serve whatever `python -m app.player_modelling.cli
project-upcoming` last persisted (see live_report_query.py); nothing here
recomputes a projection on request. Write endpoints (expected lineup,
manual prop entry) are plain upsert/CRUD, mirroring app/api/routes/odds.py's
conventions exactly.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    BestOpportunityRead,
    BookmakerCoverageRead,
    BulkApplyRequest,
    BulkApplyResult,
    BulkRemoveRequest,
    BulkRemoveResult,
    DiversifiedOpportunitiesResponseRead,
    DiversifiedOpportunityRead,
    DisposalProjectionRead,
    EliteDisposalBucketRead,
    ExcludedOpportunityRead,
    ExpectedLineupCreate,
    ExpectedLineupRead,
    FinalShortlistOpportunityRead,
    FinalShortlistResponseRead,
    GoalProjectionRead,
    LineupSummaryRead,
    MatchProjectionsRead,
    ModelMarketDisagreementRead,
    MatchMultiTiersRead,
    NormalizedPropInsightRead,
    RoundMultiSummaryRead,
    RoundMultiSummaryRowRead,
    OpportunityTiersResponseRead,
    PlayerProjectionRead,
    PlayerPropMarketCreate,
    PlayerPropMarketRead,
    PropInsightRead,
    RosterSuggestionRead,
    ThresholdProbabilityRead,
    WeeklySummaryRead,
)
from app.database import get_db
from app.models import Bookmaker, ExpectedLineup, Match, Player, PlayerPropMarket, SelectionStatus, Team, derive_coarse_status
from app.player_modelling.best_opportunities import load_best_opportunities
from app.player_modelling.diversified_opportunities import load_diversified_opportunities
from app.player_modelling.elite_disposal_diagnostic import bucket_diagnostic_as_dict, load_elite_disposal_diagnostic
from app.player_modelling.final_shortlist import DEFAULT_SHORTLIST_LIMIT, load_final_shortlist
from app.player_modelling.multi_builder import TIER_ORDER as MULTI_TIER_ORDER, build_match_multis, match_multi_tiers_as_dict
from app.player_modelling.opportunity_tiers import (
    DEFAULT_ALL_AVAILABLE_LIMIT,
    DEFAULT_BEST_LIMIT,
    DEFAULT_WORTH_REVIEWING_LIMIT,
    load_opportunity_tiers,
)
from app.player_modelling.live_report_query import (
    ProjectionFilters,
    load_lineup_summary,
    load_match_projections,
    load_player_projection,
    load_prop_insights,
    load_upcoming_disposal_projections,
    load_upcoming_goal_projections,
)
from app.player_modelling.match_context_service import context_item_as_dict, current_context_for_match
from app.player_modelling.model_market_disagreements import DEFAULT_DISAGREEMENT_THRESHOLD_PP, load_model_market_disagreements
from app.player_modelling.request_cache import clear_ttl_cache
from app.player_modelling.prop_insights_normalized import load_normalized_prop_insights
from app.player_modelling.team_selection_ingestion import (
    SelectionEntry,
    ingest_team_selections,
    suggest_roster_from_recent_match,
)
from app.player_modelling.uncertainty_flags import high_tog_volatility
from app.player_modelling.upcoming_features import load_next_upcoming_round
from app.player_modelling.weekly_summary import bookmaker_coverage_summary, compute_weekly_summary

router = APIRouter(prefix="/api/afl", tags=["player-projections"])


def _thresholds_read(thresholds: dict) -> dict[str, ThresholdProbabilityRead]:
    return {k: ThresholdProbabilityRead(**v) for k, v in thresholds.items()}


def _disposal_read(view: dict) -> DisposalProjectionRead:
    return DisposalProjectionRead(**{**view, "thresholds": _thresholds_read(view["thresholds"])})


def _goal_read(view: dict) -> GoalProjectionRead:
    return GoalProjectionRead(**{**view, "thresholds": _thresholds_read(view["thresholds"])})


@router.get("/player-projections/upcoming")
def get_upcoming_projections(
    market: str | None = Query(default=None, description="'player_disposals' | 'player_goals' | omit for both"),
    round_number: int | None = Query(default=None, alias="round"),
    season: int | None = None,
    team_id: int | None = None,
    match_id: int | None = None,
    confidence: str | None = None,
    min_probability: float | None = None,
    threshold: float | None = None,
    lineup_filter: str | None = Query(
        default=None, description="'confirmed_only' | 'confirmed_plus_expected' | 'include_uncertain' (default) — confirmed_out is always excluded"
    ),
    db: Session = Depends(get_db),
) -> dict:
    filters = ProjectionFilters(
        round_number=round_number, season_year=season, team_id=team_id, match_id=match_id,
        confidence=confidence, min_probability=min_probability, probability_threshold=threshold,
        lineup_filter=lineup_filter,
    )
    result: dict = {}
    if market in (None, "player_disposals"):
        result["disposals"] = [_disposal_read(v) for v in load_upcoming_disposal_projections(db, filters)]
    if market in (None, "player_goals"):
        result["goals"] = [_goal_read(v) for v in load_upcoming_goal_projections(db, filters)]
    return result


@router.get("/matches/{match_id}/player-projections", response_model=MatchProjectionsRead)
def get_match_projections(match_id: int, db: Session = Depends(get_db)) -> MatchProjectionsRead:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    view = load_match_projections(db, match_id)
    return MatchProjectionsRead(
        match_id=match_id,
        disposals=[_disposal_read(v) for v in view["disposals"]],
        goals=[_goal_read(v) for v in view["goals"]],
    )


@router.get("/players/{player_id}/projection", response_model=PlayerProjectionRead)
def get_player_projection(player_id: int, db: Session = Depends(get_db)) -> PlayerProjectionRead:
    player = db.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    view = load_player_projection(db, player_id)
    if view is None:
        return PlayerProjectionRead(disposals=None, goals=None)

    match_id = (view["disposals"] or view["goals"])["match_id"]
    lineup = db.scalar(select(ExpectedLineup).where(ExpectedLineup.match_id == match_id, ExpectedLineup.player_id == player_id))
    current_context = [context_item_as_dict(c) for c in current_context_for_match(db, match_id) if c.player_id == player_id]
    tog_volatile = None
    match = db.get(Match, match_id)
    if match is not None:
        tog_volatile = high_tog_volatility(db, player_id, match.scheduled_start)

    return PlayerProjectionRead(
        disposals=_disposal_read(view["disposals"]) if view["disposals"] else None,
        goals=_goal_read(view["goals"]) if view["goals"] else None,
        current_context=current_context,
        tog_volatile=tog_volatile,
        substitute_risk=lineup.substitute_risk if lineup is not None else None,
        returning_from_injury=lineup.returning_from_injury if lineup is not None else None,
        role_note=lineup.role_note if lineup is not None else None,
    )


# --- Expected lineup (Sections 1-2) ----------------------------------------


_DEFAULT_SELECTION_STATUS_BY_COARSE_STATUS = {
    "expected_in": SelectionStatus.CONFIRMED_SELECTED.value,
    "expected_out": SelectionStatus.CONFIRMED_OUT.value,
    "uncertain": SelectionStatus.UNCERTAIN.value,
}


def _lineup_read(lineup: ExpectedLineup) -> ExpectedLineupRead:
    return ExpectedLineupRead(
        id=lineup.id, match_id=lineup.match_id, player_id=lineup.player_id, player_name=lineup.player.display_name,
        team_id=lineup.team_id, status=lineup.status, selection_status=lineup.selection_status, is_confirmed=lineup.is_confirmed,
        recorded_at=lineup.recorded_at, source=lineup.source, source_timestamp=lineup.source_timestamp,
        source_reference=lineup.source_reference, is_manual_override=lineup.is_manual_override,
        note=lineup.note, substitute_risk=lineup.substitute_risk, returning_from_injury=lineup.returning_from_injury,
        role_note=lineup.role_note, expected_tog_adjustment=lineup.expected_tog_adjustment,
    )


@router.get("/matches/{match_id}/lineup", response_model=list[ExpectedLineupRead])
def list_expected_lineup(match_id: int, db: Session = Depends(get_db)) -> list[ExpectedLineupRead]:
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")
    rows = db.scalars(select(ExpectedLineup).where(ExpectedLineup.match_id == match_id)).all()
    return [_lineup_read(r) for r in rows]


@router.get("/matches/{match_id}/lineup/summary", response_model=LineupSummaryRead)
def get_lineup_summary(match_id: int, db: Session = Depends(get_db)) -> LineupSummaryRead:
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")
    return LineupSummaryRead(**load_lineup_summary(db, match_id))


@router.get("/matches/{match_id}/lineup/suggested-roster", response_model=list[RosterSuggestionRead])
def get_suggested_roster(match_id: int, team_id: int, db: Session = Depends(get_db)) -> list[RosterSuggestionRead]:
    """Section 13's manual-fallback starting point: this team's most recent
    completed-match roster, for a human to quickly review and bulk-apply
    from — never applied automatically, always PLACEHOLDER until a human
    confirms it via bulk-apply."""
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    if team_id not in (match.home_team_id, match.away_team_id):
        raise HTTPException(status_code=400, detail="team_id must be one of the two teams in this match")
    suggestions = suggest_roster_from_recent_match(db, team_id)
    return [RosterSuggestionRead(player_id=s.player_id, display_name=s.display_name, last_match_id=s.last_match_id, last_played_at=s.last_played_at) for s in suggestions]


@router.post("/matches/{match_id}/lineup/bulk-apply", response_model=BulkApplyResult)
def bulk_apply_lineup(match_id: int, payload: BulkApplyRequest, db: Session = Depends(get_db)) -> BulkApplyResult:
    """Section 13's efficient bulk workflow: apply a selection_status to
    many players in one call (e.g. "confirm this whole suggested roster as
    placeholder" or "mark these 4 players confirmed_out"). Never itself
    marks rows as manual_override — see team_selection_ingestion.py's
    module docstring for why that's reserved for the single-player PUT
    endpoint below."""
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")

    entries = [
        SelectionEntry(team_id=e.team_id, selection_status=e.selection_status.value, player_id=e.player_id, note=e.note)
        for e in payload.entries
    ]
    report = ingest_team_selections(
        db, match_id, entries, source=payload.source, source_timestamp=None, allow_override_manual=payload.allow_override_manual
    )
    clear_ttl_cache()
    return BulkApplyResult(
        created=report.created, updated=report.updated, status_changed=report.status_changed,
        skipped_manual_override=report.skipped_manual_override, unresolved=report.unresolved, ambiguous=report.ambiguous,
    )


@router.post("/matches/{match_id}/lineup/bulk-remove", response_model=BulkRemoveResult)
def bulk_remove_lineup(match_id: int, payload: BulkRemoveRequest, db: Session = Depends(get_db)) -> BulkRemoveResult:
    """Section 7 of the live-operations stage brief: efficiently removing
    players who were on a previous/suggested roster but are omitted from
    the new one — the bulk counterpart to the single-player DELETE below.
    Removes unconditionally (including manual-override rows) since this is
    itself an explicit human bulk action, same trust level as the single
    DELETE endpoint."""
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")

    removed: list[int] = []
    not_found: list[int] = []
    for player_id in payload.player_ids:
        lineup = db.scalar(select(ExpectedLineup).where(ExpectedLineup.match_id == match_id, ExpectedLineup.player_id == player_id))
        if lineup is None:
            not_found.append(player_id)
            continue
        db.delete(lineup)
        removed.append(player_id)
    db.commit()
    return BulkRemoveResult(removed=removed, not_found=not_found)


@router.put("/matches/{match_id}/lineup/{player_id}", response_model=ExpectedLineupRead)
def set_expected_lineup(match_id: int, player_id: int, payload: ExpectedLineupCreate, db: Session = Depends(get_db)) -> ExpectedLineupRead:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    if db.get(Player, player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")
    if db.get(Team, payload.team_id) is None:
        raise HTTPException(status_code=404, detail="Team not found")
    if payload.team_id not in (match.home_team_id, match.away_team_id):
        raise HTTPException(status_code=400, detail="team_id must be one of the two teams in this match")

    lineup = db.scalar(select(ExpectedLineup).where(ExpectedLineup.match_id == match_id, ExpectedLineup.player_id == player_id))
    if lineup is None:
        lineup = ExpectedLineup(match_id=match_id, player_id=player_id)
        db.add(lineup)

    selection_status = payload.selection_status.value if payload.selection_status else _DEFAULT_SELECTION_STATUS_BY_COARSE_STATUS[payload.status.value]

    lineup.team_id = payload.team_id
    lineup.status = payload.status.value
    lineup.selection_status = selection_status
    lineup.is_confirmed = selection_status == SelectionStatus.CONFIRMED_SELECTED.value
    lineup.recorded_at = datetime.now(timezone.utc)
    lineup.source = "manual"
    lineup.source_timestamp = None
    lineup.source_reference = None
    # A direct single-player edit through this endpoint IS the "manual
    # override" moment (Section 3): a later bulk/automated ingestion write
    # must not silently clobber it (see team_selection_ingestion.py).
    lineup.is_manual_override = True
    lineup.note = payload.note
    lineup.substitute_risk = payload.substitute_risk
    lineup.returning_from_injury = payload.returning_from_injury
    lineup.role_note = payload.role_note
    lineup.expected_tog_adjustment = payload.expected_tog_adjustment
    db.commit()
    db.refresh(lineup)
    clear_ttl_cache()
    return _lineup_read(lineup)


@router.delete("/matches/{match_id}/lineup/{player_id}", status_code=204)
def delete_expected_lineup(match_id: int, player_id: int, db: Session = Depends(get_db)) -> None:
    lineup = db.scalar(select(ExpectedLineup).where(ExpectedLineup.match_id == match_id, ExpectedLineup.player_id == player_id))
    if lineup is None:
        raise HTTPException(status_code=404, detail="Expected lineup record not found")
    db.delete(lineup)
    db.commit()


# --- Manual player prop entry (Sections 11-15) ------------------------------


def _get_or_create_bookmaker(db: Session, name: str) -> Bookmaker:
    bookmaker = db.scalar(select(Bookmaker).where(Bookmaker.name == name))
    if bookmaker is None:
        bookmaker = Bookmaker(name=name)
        db.add(bookmaker)
        db.flush()
    return bookmaker


def _prop_read(q: PlayerPropMarket) -> PlayerPropMarketRead:
    return PlayerPropMarketRead(
        id=q.id, match_id=q.match_id, player_id=q.player_id, player_name=q.player.display_name,
        bookmaker_name=q.bookmaker.name, market_type=q.market_type, line_type=q.line_type,
        threshold=q.threshold, price_decimal=q.price_decimal, recorded_at=q.recorded_at, source=q.source,
    )


@router.get("/matches/{match_id}/player-props", response_model=list[PlayerPropMarketRead])
def list_player_props(match_id: int, db: Session = Depends(get_db)) -> list[PlayerPropMarketRead]:
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")
    rows = db.scalars(
        select(PlayerPropMarket).where(PlayerPropMarket.match_id == match_id).order_by(PlayerPropMarket.recorded_at.desc())
    ).all()
    return [_prop_read(r) for r in rows]


@router.post("/matches/{match_id}/player-props", response_model=PlayerPropMarketRead, status_code=201)
def create_player_prop(match_id: int, payload: PlayerPropMarketCreate, db: Session = Depends(get_db)) -> PlayerPropMarketRead:
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")
    if db.get(Player, payload.player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    bookmaker = _get_or_create_bookmaker(db, payload.bookmaker_name)
    quote = PlayerPropMarket(
        match_id=match_id, player_id=payload.player_id, bookmaker_id=bookmaker.id,
        market_type=payload.market_type, line_type=payload.line_type, threshold=payload.threshold,
        price_decimal=payload.price_decimal, recorded_at=payload.recorded_at or datetime.now(timezone.utc), source="manual",
    )
    db.add(quote)
    db.commit()
    db.refresh(quote)
    return _prop_read(quote)


@router.delete("/player-props/{prop_id}", status_code=204)
def delete_player_prop(prop_id: int, db: Session = Depends(get_db)) -> None:
    quote = db.get(PlayerPropMarket, prop_id)
    if quote is None:
        raise HTTPException(status_code=404, detail="Player prop quote not found")
    db.delete(quote)
    db.commit()


@router.get("/prop-insights", response_model=list[PropInsightRead])
def get_prop_insights(
    market: str | None = None,
    confidence: str | None = None,
    include_uncertain: bool = Query(default=True, description="False hides props for players whose participation isn't confirmed/expected"),
    db: Session = Depends(get_db),
) -> list[PropInsightRead]:
    rows = load_prop_insights(db, market=market, min_confidence=confidence, include_uncertain=include_uncertain)
    return [PropInsightRead(**r) for r in rows]


@router.get("/prop-insights/normalized", response_model=list[NormalizedPropInsightRead])
def get_normalized_prop_insights(
    market: str | None = None,
    confidence: str | None = None,
    include_uncertain: bool = Query(default=True, description="False hides props for players whose participation isn't confirmed/expected"),
    opportunities_only: bool = Query(default=False, description="True hides markets where the model doesn't favour the best available price at all"),
    match_id: int | None = None,
    db: Session = Depends(get_db),
) -> list[NormalizedPropInsightRead]:
    """Best-price, multi-bookmaker Prop Insights (Sections 12-23 of the
    automated-odds stage) — one row per normalized market (player + market
    type + threshold), comparing every bookmaker currently offering it.
    Powers the redesigned Prop Insights page's tabs; each tab is just a
    different combination of these same query params."""
    rows = load_normalized_prop_insights(
        db,
        market=market,
        min_confidence=confidence,
        include_uncertain=include_uncertain,
        opportunities_only=opportunities_only,
        match_id=match_id,
    )
    return [NormalizedPropInsightRead(**r) for r in rows]


@router.get("/best-opportunities", response_model=list[BestOpportunityRead])
def get_best_opportunities(
    market_scope: str = Query(default="all", description="'all' | 'player' | 'team'"),
    include_uncertain: bool = Query(default=False, description="True includes players whose participation isn't confirmed/expected"),
    include_stale: bool = Query(default=False, description="True includes markets whose best price is stale"),
    include_insufficient_history: bool = Query(default=False, description="True includes markets with insufficient model history/confidence"),
    limit: int | None = Query(default=None, description="Top N by opportunity score; omit for all that pass the quality gates"),
    db: Session = Depends(get_db),
) -> list[BestOpportunityRead]:
    """The single round-wide ranked list Sections 6-11 and 17-18 ask for:
    'What are the strongest model-vs-market opportunities available across
    this AFL round right now, and which bookmaker currently has the best
    price?' Merges player markets (prop_insights_normalized.py) and team
    markets (edges/calculator.py) into one list, ranked by the same
    transparent opportunity score, with quality gates on by default and
    individually overridable."""
    rows = load_best_opportunities(
        db,
        market_scope=market_scope,
        include_uncertain=include_uncertain,
        include_stale=include_stale,
        include_insufficient_history=include_insufficient_history,
        limit=limit,
    )
    return [BestOpportunityRead(**r) for r in rows]


@router.get("/best-opportunities/diversified", response_model=DiversifiedOpportunitiesResponseRead)
def get_diversified_opportunities(
    view: str = Query(default="overall", description="'overall' | 'disposals' | 'goals'"),
    market_scope: str = Query(default="all", description="'all' | 'player' | 'team' — only applies to view='overall'"),
    include_uncertain: bool = Query(default=False, description="True includes players whose participation isn't confirmed/expected"),
    include_stale: bool = Query(default=False, description="True includes markets whose best price is stale"),
    include_insufficient_history: bool = Query(default=False, description="True includes markets with insufficient model history/confidence"),
    one_per_match: bool = Query(default=False, description="Off by default — collapses each match to its single best family before diversifying"),
    one_per_player: bool = Query(default=False, description="Off by default — collapses each player to their single best family before diversifying"),
    limit: int | None = Query(default=None, description="Top N diversified headlines; omit for all that pass the quality gates"),
    db: Session = Depends(get_db),
) -> DiversifiedOpportunitiesResponseRead:
    """Weekly Opportunity Discovery + Diversification stage: the curated
    'Best Opportunities This Round' experience (Sections 1-7) — groups
    correlated alternate lines (e.g. every disposal threshold for one
    player in one match) into one opportunity_family, picks the single
    most useful representative line to headline, and applies the
    diversification selection rules (max 2 per player, soft per-match
    cap) so a single hot player's alternate lines can never fill the
    entire list. The raw, ungrouped ranking (GET /best-opportunities)
    is unaffected and remains fully accessible — nothing here hides or
    deletes an alternate market, it's attached under `alternate_lines`."""
    result = load_diversified_opportunities(
        db,
        view=view,
        market_scope=market_scope,
        include_uncertain=include_uncertain,
        include_stale=include_stale,
        include_insufficient_history=include_insufficient_history,
        one_per_match=one_per_match,
        one_per_player=one_per_player,
        limit=limit,
    )

    upcoming = load_next_upcoming_round(db)
    round_number = upcoming[0].round_number if upcoming else None
    summary = compute_weekly_summary(round_number, result.all_passing)
    coverage = bookmaker_coverage_summary(db)

    return DiversifiedOpportunitiesResponseRead(
        opportunities=[DiversifiedOpportunityRead(**o) for o in result.opportunities],
        summary=WeeklySummaryRead(**summary),
        bookmaker_coverage=[BookmakerCoverageRead(**c) for c in coverage],
    )


@router.get("/best-opportunities/tiers", response_model=OpportunityTiersResponseRead)
def get_opportunity_tiers(
    market_scope: str = Query(default="all", description="'all' | 'player' | 'team'"),
    best_limit: int | None = Query(default=DEFAULT_BEST_LIMIT),
    worth_reviewing_limit: int | None = Query(default=DEFAULT_WORTH_REVIEWING_LIMIT),
    all_available_limit: int | None = Query(default=DEFAULT_ALL_AVAILABLE_LIMIT),
    db: Session = Depends(get_db),
) -> OpportunityTiersResponseRead:
    """Product-quality stage: the practical DEFAULT Prop Insights view —
    three visible tiers (Best Opportunities / Worth Reviewing / All
    Available) computed from the SAME opportunity_score and quality_tier
    as every other view here, just bucketed differently, so a market never
    disappears just for having one soft caveat (see opportunity_tiers.py).
    If `best` comes back empty but `worth_reviewing` doesn't,
    `fallback_message` is set — the frontend shows Worth Reviewing in
    Best's place with that message rather than an empty screen."""
    result = load_opportunity_tiers(
        db, market_scope=market_scope, best_limit=best_limit,
        worth_reviewing_limit=worth_reviewing_limit, all_available_limit=all_available_limit,
    )
    return OpportunityTiersResponseRead(
        best=[DiversifiedOpportunityRead(**o) for o in result.best],
        worth_reviewing=[DiversifiedOpportunityRead(**o) for o in result.worth_reviewing],
        all_available=[BestOpportunityRead(**o) for o in result.all_available],
        exclusion_breakdown=result.exclusion_breakdown,
        n_candidates=result.n_candidates,
        n_hard_excluded=result.n_hard_excluded,
        fallback_message=result.fallback_message,
    )


@router.get("/matches/{match_id}/multi-builder", response_model=MatchMultiTiersRead)
def get_match_multi_builder(match_id: int, confirmed_only: bool = Query(default=True), db: Session = Depends(get_db)) -> MatchMultiTiersRead:
    """Product feature stage: up to 3 model-informed multi combinations per
    tier (Conservative/Balanced/Higher Return/Longer Shot), built entirely
    from already-computed opportunities and already-existing hard/soft
    quality gates — see multi_builder.py for what "indicative combined
    odds" does and does not claim. `confirmed_only` (default True) excludes
    any leg whose player selection isn't confirmed; team legs are always
    eligible regardless (Section: "unconfirmed players may appear only in
    a clearly labelled provisional multi")."""
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")
    result = build_match_multis(db, match_id, confirmed_only=confirmed_only)
    return MatchMultiTiersRead(**match_multi_tiers_as_dict(result))


@router.get("/multi-builder/round-summary", response_model=RoundMultiSummaryRead)
def get_round_multi_summary(confirmed_only: bool = Query(default=True), db: Session = Depends(get_db)) -> RoundMultiSummaryRead:
    """Compact round-wide view for the Multis page — one row per upcoming
    match, so a user can see which matches currently support a multi
    without opening every Match Centre."""
    upcoming = load_next_upcoming_round(db)
    rows = []
    for m in upcoming:
        match = db.get(Match, m.match_id)
        result = build_match_multis(db, m.match_id, confirmed_only=confirmed_only)
        tiers_available = [t for t in MULTI_TIER_ORDER if result.tiers.get(t)]
        rows.append(RoundMultiSummaryRowRead(
            match_id=m.match_id, home_team_name=match.home_team.name, away_team_name=match.away_team.name,
            scheduled_start=match.scheduled_start, n_eligible_legs=result.n_eligible_legs,
            n_bookmakers_available=len(result.bookmakers_available), tiers_available=tiers_available,
        ))
    return RoundMultiSummaryRead(matches=rows)


@router.get("/best-opportunities/final-shortlist", response_model=FinalShortlistResponseRead)
def get_final_shortlist(
    limit: int | None = Query(default=DEFAULT_SHORTLIST_LIMIT, description="Maximum shortlist size (5/10/20) — never padded to reach it"),
    include_unconfirmed_players: bool = Query(default=False, description="True includes player opportunities whose selection isn't confirmed"),
    db: Session = Depends(get_db),
) -> FinalShortlistResponseRead:
    """Market Integrity + Final Weekly Picks stage, Sections 7-11, 22: the
    Final Weekly Shortlist — more selective than Best Opportunities
    (GET /best-opportunities/diversified). Only quality-tier
    strong_candidate/worth_reviewing opportunities can headline, player
    opportunities require a confirmed selection by default, and at most
    one representative survives per strongly-correlated team-market group
    (e.g. a team's H2H and line collapse to one). Never manufactures a
    Top N: if fewer opportunities genuinely qualify, fewer are returned."""
    result = load_final_shortlist(db, limit=limit, include_unconfirmed_players=include_unconfirmed_players)
    return FinalShortlistResponseRead(
        opportunities=[FinalShortlistOpportunityRead(**o) for o in result.opportunities],
        excluded=[ExcludedOpportunityRead(label=e.label, opportunity_type=e.opportunity_type, reason=e.reason) for e in result.excluded],
        empty_state_reason=result.empty_state_reason,
        any_confirmed_player_lineups=result.any_confirmed_player_lineups,
    )


@router.get("/model-vs-market-disagreements", response_model=list[ModelMarketDisagreementRead])
def get_model_market_disagreements(
    threshold_pp: float = Query(default=DEFAULT_DISAGREEMENT_THRESHOLD_PP, description="Minimum |model - market| gap (as a probability, e.g. 0.15 = 15pp) to flag"),
    limit: int | None = Query(default=20, description="Maximum rows to return, largest gap first"),
    db: Session = Depends(get_db),
) -> list[ModelMarketDisagreementRead]:
    """Section 18 — 'Model vs Market Disagreements'. NOT an opportunity
    list and never called 'Best Bets': surfaces the largest model/market
    probability gaps in EITHER direction, including cases (like the
    brief's own Nick Daicos example) where the MARKET is far more
    confident than the model — invisible everywhere else in this app,
    since Best Opportunities and the Final Shortlist only ever show
    markets where the model exceeds the market."""
    rows = load_model_market_disagreements(db, threshold_pp=threshold_pp, limit=limit)
    return [ModelMarketDisagreementRead(**r) for r in rows]


@router.get("/elite-disposal-diagnostic", response_model=list[EliteDisposalBucketRead] | None)
def get_elite_disposal_diagnostic(
    current_only: bool = Query(default=True, description="Restrict the displayed player lists to currently active/relevant players (default) rather than every historical player"),
    min_n_predictions: int = Query(default=20, ge=0, description="Minimum historical predictions a player needs to be listed (5/10/20/50); 0 means All"),
    db: Session = Depends(get_db),
) -> list[EliteDisposalBucketRead] | None:
    """Section 19 — a read-only RESEARCH diagnostic over the promoted
    disposal model's historical (2016-2025 backtest) evaluation
    predictions, bucketed by each player's own historical average actual
    disposals (ground truth, not reputation). Reports whether the model
    is systematically biased for high-volume players. Returns null if no
    promoted disposal model / evaluation predictions exist. This endpoint
    never changes the promoted model — it is diagnostic only, and Section
    19 explicitly forbids retuning based on current-round observations.

    `current_only` (default True) only affects which players are LISTED —
    bucket-level aggregate metrics (n_players, n_predictions, avg_actual,
    avg_predicted, bias, mae) always reflect the full historical evaluation
    population regardless of this flag (see current_players.py)."""
    buckets = load_elite_disposal_diagnostic(db, current_only=current_only, min_n_predictions=min_n_predictions or None)
    if buckets is None:
        return None
    return [EliteDisposalBucketRead(**bucket_diagnostic_as_dict(b)) for b in buckets]
