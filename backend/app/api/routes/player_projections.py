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
    BulkApplyRequest,
    BulkApplyResult,
    DisposalProjectionRead,
    ExpectedLineupCreate,
    ExpectedLineupRead,
    GoalProjectionRead,
    LineupSummaryRead,
    MatchProjectionsRead,
    NormalizedPropInsightRead,
    PlayerProjectionRead,
    PlayerPropMarketCreate,
    PlayerPropMarketRead,
    PropInsightRead,
    RosterSuggestionRead,
    ThresholdProbabilityRead,
)
from app.database import get_db
from app.models import Bookmaker, ExpectedLineup, Match, Player, PlayerPropMarket, SelectionStatus, Team, derive_coarse_status
from app.player_modelling.live_report_query import (
    ProjectionFilters,
    load_lineup_summary,
    load_match_projections,
    load_player_projection,
    load_prop_insights,
    load_upcoming_disposal_projections,
    load_upcoming_goal_projections,
)
from app.player_modelling.prop_insights_normalized import load_normalized_prop_insights
from app.player_modelling.team_selection_ingestion import (
    SelectionEntry,
    ingest_team_selections,
    suggest_roster_from_recent_match,
)

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
    return PlayerProjectionRead(
        disposals=_disposal_read(view["disposals"]) if view["disposals"] else None,
        goals=_goal_read(view["goals"]) if view["goals"] else None,
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
    return BulkApplyResult(
        created=report.created, updated=report.updated, status_changed=report.status_changed,
        skipped_manual_override=report.skipped_manual_override, unresolved=report.unresolved, ambiguous=report.ambiguous,
    )


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
