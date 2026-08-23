"""Shortlist snapshot/freeze + history/replay + result tracking (Weekly
Bet Review + Decision Support stage, Sections 14-16). Freezing NEVER
overwrites an existing snapshot — every call to create_snapshot inserts a
brand-new WeeklyShortlistSnapshot row; nothing here ever updates or
deletes one. Settlement is the one exception, and even then only three
result-tracking columns per item are ever written, exactly once, well
after the rest of the row was frozen.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    GoalModelRun,
    Match,
    MatchStatus,
    PlayerMatchStat,
    PlayerModelRun,
    WeeklyShortlistSnapshot,
    WeeklyShortlistSnapshotItem,
)
from app.player_modelling.final_shortlist import DEFAULT_SHORTLIST_LIMIT, load_final_shortlist
from app.player_modelling.prop_settlement import compute_team_market_result
from app.player_modelling.upcoming_features import load_next_upcoming_round


def _model_name_version(opportunity: dict) -> tuple[str | None, str | None]:
    if opportunity["market_type"] == "h2h":
        return "elo", None
    if opportunity["market_type"] in ("line", "total"):
        return "poisson", None
    return None, None  # filled in per-row from the promoted player model below


def create_snapshot(db: Session, *, limit: int | None = DEFAULT_SHORTLIST_LIMIT, include_unconfirmed_players: bool = False, label: str | None = None) -> WeeklyShortlistSnapshot:
    result = load_final_shortlist(db, limit=limit, include_unconfirmed_players=include_unconfirmed_players)

    upcoming = load_next_upcoming_round(db)
    round_number = upcoming[0].round_number if upcoming else None
    season_year = upcoming[0].season_year if upcoming else None

    disposal_version = None
    goal_version = None
    disposal_run = db.scalar(select(PlayerModelRun).where(PlayerModelRun.is_promoted.is_(True)))
    if disposal_run is not None:
        disposal_version = f"{disposal_run.model_name}@{disposal_run.run_at.isoformat()}"
    goal_run = db.scalar(select(GoalModelRun).where(GoalModelRun.is_promoted.is_(True)))
    if goal_run is not None:
        goal_version = f"{goal_run.model_name}@{goal_run.run_at.isoformat()}"

    snapshot = WeeklyShortlistSnapshot(
        round_number=round_number, season_year=season_year, limit_requested=limit,
        include_unconfirmed_players=include_unconfirmed_players, n_items=len(result.opportunities), label=label,
    )
    db.add(snapshot)
    db.flush()

    for rank, o in enumerate(result.opportunities, start=1):
        model_name, model_version = _model_name_version(o)
        if o["market_type"] == "player_disposals":
            model_name, model_version = "disposals", disposal_version
        elif o["market_type"] == "player_goals":
            model_name, model_version = "goals", goal_version

        best_entry = next((b for b in o["bookmakers"] if b["bookmaker_name"] == o["best_bookmaker"]), None)
        recorded_at = best_entry["recorded_at"] if best_entry else datetime.now(timezone.utc)

        db.add(
            WeeklyShortlistSnapshotItem(
                snapshot_id=snapshot.id, rank=rank,
                opportunity_type=o["opportunity_type"], label=o["label"], match_id=o["match_id"], market_type=o["market_type"],
                player_id=o.get("player_id"), selection=o.get("selection"), threshold=o.get("threshold"), line_value=o.get("line_value"),
                line_type=o.get("line_type"),
                best_price=o["best_price"], best_bookmaker=o["best_bookmaker"], recorded_at=recorded_at,
                model_probability=o["model_probability"], model_fair_odds=o["model_fair_odds"],
                market_implied_probability=o["market_implied_probability"], devigged_probability=o.get("devigged_probability"),
                overround_removed=bool(o.get("overround_removed")), difference_pp=o["difference_pp"], expected_value=o["expected_value"],
                confidence_tier=o["confidence_tier"], quality_tier=o["quality_tier"]["tier"],
                market_maturity_tier=o["market_maturity"]["tier"] if o.get("market_maturity") else None,
                is_confirmed=o.get("is_confirmed"), model_name=model_name, model_version=model_version, n_bookmakers=o["n_bookmakers"],
                reasons_json={
                    "why_it_ranks_here": o.get("why_it_ranks_here", []),
                    "caveats": o.get("caveats", []),
                    "correlation_labels": o.get("correlation_labels", []),
                },
            )
        )

    db.commit()
    db.refresh(snapshot)
    return snapshot


def list_snapshots(db: Session, *, limit: int | None = 50) -> list[WeeklyShortlistSnapshot]:
    stmt = select(WeeklyShortlistSnapshot).order_by(WeeklyShortlistSnapshot.created_at.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt).all())


def get_snapshot(db: Session, snapshot_id: int) -> WeeklyShortlistSnapshot | None:
    return db.get(WeeklyShortlistSnapshot, snapshot_id)


def _settle_player_item(db: Session, item: WeeklyShortlistSnapshotItem) -> None:
    stat_field = "disposals" if item.market_type == "player_disposals" else "goals"
    stat = db.scalar(
        select(getattr(PlayerMatchStat, stat_field)).where(PlayerMatchStat.match_id == item.match_id, PlayerMatchStat.player_id == item.player_id)
    )
    if stat is None:
        return
    cleared = stat >= item.threshold if item.line_type == "multi_plus" else stat > item.threshold
    item.actual_stat_value = float(stat)
    item.match_result = "won" if cleared else "lost"
    item.settled_at = datetime.now(timezone.utc)


def _settle_team_item(db: Session, item: WeeklyShortlistSnapshotItem, match: Match) -> None:
    result = compute_team_market_result(match, item.market_type, item.selection, item.line_value)
    if result is None:
        return
    item.actual_stat_value, item.match_result = result
    item.settled_at = datetime.now(timezone.utc)


def settle_snapshot(db: Session, snapshot_id: int) -> int:
    """Attaches actual results to every UNSETTLED item in this snapshot
    whose match has completed. Descriptive tracking only (Section 16) -
    never used to retune anything. Returns the number of items settled in
    this call."""
    snapshot = db.get(WeeklyShortlistSnapshot, snapshot_id)
    if snapshot is None:
        return 0
    settled_count = 0
    for item in snapshot.items:
        if item.settled_at is not None:
            continue
        match = db.get(Match, item.match_id)
        if match is None or match.status != MatchStatus.COMPLETED:
            continue
        if item.opportunity_type == "player":
            _settle_player_item(db, item)
        else:
            _settle_team_item(db, item, match)
        if item.settled_at is not None:
            settled_count += 1
    db.commit()
    return settled_count
