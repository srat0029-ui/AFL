"""Weekly Bet Review + Decision Support stage — the Weekly Review page's
full data (GET /api/afl/weekly-review) and the Shortlist snapshot/history/
result-tracking endpoints. Everything here is read-heavy aggregation over
already-existing ranking/gating modules (best_opportunities.py,
final_shortlist.py, etc.) plus this stage's own context modules — nothing
here computes a new probability or retunes anything.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.schemas import (
    CreateSnapshotRequest,
    SettleSnapshotResult,
    ShortlistRoundSummaryRead,
    ShortlistSnapshotRead,
    ShortlistSnapshotSummaryRead,
    WeeklyReviewOpportunityRead,
    WeeklyReviewPageRead,
    WeeklySummaryRead,
)
from app.database import get_db
from app.player_modelling.round_summary import build_round_summary, round_summary_as_dict
from app.player_modelling.weekly_review import build_weekly_review_page
from app.player_modelling.weekly_shortlist_snapshot_service import create_snapshot, get_snapshot, list_snapshots, settle_snapshot

router = APIRouter(prefix="/api/afl/weekly-review", tags=["weekly-review"])


@router.get("", response_model=WeeklyReviewPageRead)
def get_weekly_review_page(
    shortlist_limit: int = Query(default=10, description="Final Weekly Shortlist maximum (never padded)"),
    comparison_limit: int = Query(default=10, description="Maximum rows for each of the Strongest Player/Team/Waiting-on-confirmation sections"),
    db: Session = Depends(get_db),
) -> WeeklyReviewPageRead:
    """Weekly Bet Review + Decision Support stage, Section 1: the single
    page hierarchy — Final Weekly Shortlist, Strongest Player Opportunities,
    Strongest Team Opportunities, Model vs Market Disagreements (count
    only — see GET /api/afl/model-vs-market-disagreements for the full
    list), Markets Waiting on Team Confirmation, Market Coverage."""
    page = build_weekly_review_page(db, shortlist_limit=shortlist_limit, comparison_limit=comparison_limit)
    return WeeklyReviewPageRead(
        final_shortlist=[WeeklyReviewOpportunityRead(**o) for o in page.final_shortlist],
        strongest_player_opportunities=[WeeklyReviewOpportunityRead(**o) for o in page.strongest_player_opportunities],
        strongest_team_opportunities=[WeeklyReviewOpportunityRead(**o) for o in page.strongest_team_opportunities],
        model_vs_market_disagreements_count=page.model_vs_market_disagreements_count,
        markets_waiting_on_team_confirmation=[WeeklyReviewOpportunityRead(**o) for o in page.markets_waiting_on_team_confirmation],
        bookmaker_coverage=page.bookmaker_coverage,
        weekly_summary=WeeklySummaryRead(**page.weekly_summary),
        any_confirmed_player_lineups=page.any_confirmed_player_lineups,
    )


@router.get("/shortlist-snapshots", response_model=list[ShortlistSnapshotSummaryRead])
def get_shortlist_snapshots(limit: int = Query(default=50), db: Session = Depends(get_db)) -> list[ShortlistSnapshotSummaryRead]:
    """Section 15's snapshot history view — newest first."""
    snapshots = list_snapshots(db, limit=limit)
    return [
        ShortlistSnapshotSummaryRead(id=s.id, created_at=s.created_at, round_number=s.round_number, season_year=s.season_year, n_items=s.n_items, label=s.label)
        for s in snapshots
    ]


@router.post("/shortlist-snapshots", response_model=ShortlistSnapshotRead, status_code=201)
def post_shortlist_snapshot(payload: CreateSnapshotRequest, db: Session = Depends(get_db)) -> ShortlistSnapshotRead:
    """Section 14 — freezes the CURRENT Final Weekly Shortlist. Always
    creates a brand-new row; never overwrites or updates an existing
    snapshot."""
    snapshot = create_snapshot(db, limit=payload.limit, include_unconfirmed_players=payload.include_unconfirmed_players, label=payload.label)
    return ShortlistSnapshotRead.model_validate(snapshot)


@router.get("/shortlist-snapshots/{snapshot_id}", response_model=ShortlistSnapshotRead)
def get_shortlist_snapshot(snapshot_id: int, db: Session = Depends(get_db)) -> ShortlistSnapshotRead:
    """Section 15 — replay: reproduces the shortlist exactly as it was
    frozen, from the snapshot's own stored fields, never recomputed from
    current live state."""
    snapshot = get_snapshot(db, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return ShortlistSnapshotRead.model_validate(snapshot)


@router.post("/shortlist-snapshots/{snapshot_id}/settle", response_model=SettleSnapshotResult)
def post_settle_snapshot(snapshot_id: int, db: Session = Depends(get_db)) -> SettleSnapshotResult:
    """Section 16 — attaches actual results to this snapshot's items
    whose matches have completed. Descriptive tracking only; never
    retunes or re-ranks anything."""
    if get_snapshot(db, snapshot_id) is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    settled_count = settle_snapshot(db, snapshot_id)
    return SettleSnapshotResult(snapshot_id=snapshot_id, settled_count=settled_count)


@router.get("/shortlist-snapshots/{snapshot_id}/round-summary", response_model=ShortlistRoundSummaryRead)
def get_round_summary(snapshot_id: int, db: Session = Depends(get_db)) -> ShortlistRoundSummaryRead:
    """Section 17 — post-round summary against a frozen snapshot. Call
    POST .../settle first to attach results for any newly-completed
    matches; this endpoint only reads whatever is already settled."""
    snapshot = get_snapshot(db, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    summary = build_round_summary(snapshot)
    return ShortlistRoundSummaryRead(**round_summary_as_dict(summary))
