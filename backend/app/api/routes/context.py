"""Current Context + Team News Intelligence stage API — structured
context items, weather, the match context panel (Section 4), the
weather-model diagnostic (Section 9), and the Current Round Context
dashboard (Section 17). Manual entry is the primary write path (see
match_context_service.py's module docstring); this router is deliberately
thin — every real computation lives in player_modelling/.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    ContextConflictRead,
    MatchContextApplyResult,
    MatchContextItemCreate,
    MatchContextItemRead,
    MatchContextPanelRead,
    RoundContextDashboardRead,
    RoundContextMatchRead,
    WeatherDiagnosticRead,
    WeatherRefreshResult,
    WeatherSnapshotRead,
)
from app.database import get_db
from app.edges.calculator import ModelsUnavailableError, build_model_context, compute_match_predictions
from app.models import ContextType, ExpectedLineup, Match, Player, PlayerDisposalProjection, PlayerGoalProjection, SelectionStatus
from app.player_modelling.context_model_conflict import context_conflict_as_dict, detect_context_conflicts
from app.player_modelling.live_change_detection import detect_matches_needing_regeneration
from app.player_modelling.match_context_service import (
    add_context_item,
    context_freshness,
    context_item_as_dict,
    current_context_for_match,
    list_context_for_match,
)
from app.player_modelling.request_cache import clear_ttl_cache
from app.player_modelling.team_selection_ingestion import SelectionEntry, derive_announcement_state, ingest_team_selections
from app.player_modelling.upcoming_features import load_next_upcoming_round
from app.player_modelling.weather_diagnostic import weather_diagnostic_as_dict, weather_model_diagnostic
from app.player_modelling.weather_ingestion import latest_weather_for_match, refresh_weather_for_matches

router = APIRouter(prefix="/api/afl", tags=["context"])

# Section 7: which context types the manual-entry apply_to_lineup shortcut
# is allowed to also write into ExpectedLineup, and what SelectionStatus it
# maps to — a note-only type (injury, limited-game-time-concern,
# major-role-change) never silently changes lineup status.
_LINEUP_APPLY_MAP = {
    ContextType.CONFIRMED_IN.value: SelectionStatus.CONFIRMED_SELECTED.value,
    ContextType.CONFIRMED_OUT.value: SelectionStatus.CONFIRMED_OUT.value,
    ContextType.LATE_WITHDRAWAL.value: SelectionStatus.CONFIRMED_OUT.value,
    ContextType.NAMED_SUBSTITUTE.value: SelectionStatus.SUBSTITUTE.value,
    ContextType.EMERGENCY.value: SelectionStatus.EMERGENCY.value,
}


def _weather_read(w) -> WeatherSnapshotRead | None:
    if w is None:
        return None
    return WeatherSnapshotRead(
        match_id=w.match_id, venue_id=w.venue_id, fetched_at=w.fetched_at, forecast_for=w.forecast_for,
        temperature_c=w.temperature_c, rain_probability_pct=w.rain_probability_pct, expected_rainfall_mm=w.expected_rainfall_mm,
        wind_speed_kph=w.wind_speed_kph, wind_gust_kph=w.wind_gust_kph, severe_weather_warning=w.severe_weather_warning,
        severe_weather_note=w.severe_weather_note, source=w.source,
    )


@router.get("/matches/{match_id}/context", response_model=list[MatchContextItemRead])
def get_match_context_history(match_id: int, db: Session = Depends(get_db)) -> list[MatchContextItemRead]:
    """Full history (Section 12: "preserve history") — newest first, with
    is_current marking which item is the authoritative one per subject."""
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")
    all_items = list_context_for_match(db, match_id)
    current_ids = {c.id for c in current_context_for_match(db, match_id)}
    now = datetime.now(timezone.utc)
    return [context_item_as_dict(i, is_current=i.id in current_ids, now=now) for i in all_items]


@router.get("/matches/{match_id}/context/current", response_model=list[MatchContextItemRead])
def get_match_context_current(match_id: int, db: Session = Depends(get_db)) -> list[MatchContextItemRead]:
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")
    now = datetime.now(timezone.utc)
    return [context_item_as_dict(i, is_current=True, now=now) for i in current_context_for_match(db, match_id)]


@router.post("/matches/{match_id}/context", response_model=MatchContextApplyResult, status_code=201)
def create_match_context_item(match_id: int, payload: MatchContextItemCreate, db: Session = Depends(get_db)) -> MatchContextApplyResult:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    if payload.player_id is not None and db.get(Player, payload.player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    item = add_context_item(
        db, match_id=match_id, context_type=payload.context_type.value, source=payload.source, summary=payload.summary,
        confidence=payload.confidence.value, team_id=payload.team_id, player_id=payload.player_id,
        source_timestamp=payload.source_timestamp, source_reference=payload.source_reference,
    )

    lineup_updated = False
    lineup_note: str | None = None
    if payload.apply_to_lineup:
        selection_status = _LINEUP_APPLY_MAP.get(payload.context_type.value)
        if payload.player_id is None:
            lineup_note = "apply_to_lineup requires player_id (context type has no lineup effect otherwise)."
        elif selection_status is None:
            lineup_note = f"Context type {payload.context_type.value!r} has no lineup-status mapping — lineup left unchanged."
        else:
            player = db.get(Player, payload.player_id)
            team_id = payload.team_id or player.current_team_id
            if team_id is None:
                lineup_note = "Could not determine team_id for this player — lineup left unchanged."
            else:
                report = ingest_team_selections(
                    db, match_id=match_id,
                    entries=[SelectionEntry(team_id=team_id, selection_status=selection_status, player_id=payload.player_id, source_reference=payload.source_reference)],
                    source=payload.source, source_timestamp=payload.source_timestamp,
                )
                lineup_updated = report.has_changes
                if lineup_updated:
                    clear_ttl_cache()
                if report.skipped_manual_override:
                    lineup_note = "Existing lineup entry is a manual override — not changed. Use the lineup editor to override it directly."
                elif lineup_updated:
                    lineup_note = f"ExpectedLineup updated to {selection_status!r}."

    return MatchContextApplyResult(item=context_item_as_dict(item), lineup_updated=lineup_updated, lineup_apply_note=lineup_note)


@router.get("/matches/{match_id}/context-panel", response_model=MatchContextPanelRead)
def get_match_context_panel(match_id: int, db: Session = Depends(get_db)) -> MatchContextPanelRead:
    """Section 4: compact per-match panel — confirmed changes, outs, ins,
    substitute, injuries, weather, last updated."""
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")
    current = current_context_for_match(db, match_id)
    weather = latest_weather_for_match(db, match_id)
    now = datetime.now(timezone.utc)
    timestamps = [c.source_timestamp or c.recorded_at for c in current]
    if weather is not None:
        timestamps.append(weather.fetched_at)
    aware = [t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc) for t in timestamps]
    last_updated = max(aware) if aware else None
    return MatchContextPanelRead(
        match_id=match_id, current_context=[context_item_as_dict(c, now=now) for c in current],
        weather=_weather_read(weather), last_updated=last_updated,
    )


@router.get("/matches/{match_id}/context-conflict", response_model=list[ContextConflictRead])
def get_match_context_conflicts(match_id: int, db: Session = Depends(get_db)) -> list[ContextConflictRead]:
    """One conflict result per (team, player) combination that has any
    current-context item on this match — a lighter-weight way to check
    Section 6 flags outside the full Weekly Review opportunity payload."""
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")
    current = current_context_for_match(db, match_id)
    subjects: dict[tuple, dict] = {}
    for item in current:
        key = (item.team_id, item.player_id)
        subjects.setdefault(key, {"match_id": match_id, "team_id": item.team_id, "player_id": item.player_id, "market_type": None})
    results = []
    for opp in subjects.values():
        conflict = detect_context_conflicts(db, opp)
        if conflict.codes:
            results.append(context_conflict_as_dict(conflict))
    return results


@router.get("/matches/{match_id}/weather", response_model=WeatherSnapshotRead | None)
def get_match_weather(match_id: int, db: Session = Depends(get_db)) -> WeatherSnapshotRead | None:
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")
    return _weather_read(latest_weather_for_match(db, match_id))


@router.post("/weather/refresh", response_model=WeatherRefreshResult)
def refresh_weather(db: Session = Depends(get_db)) -> WeatherRefreshResult:
    upcoming = load_next_upcoming_round(db)
    report = refresh_weather_for_matches(db, upcoming)
    return WeatherRefreshResult(
        matches_considered=report.matches_considered, snapshots_created=report.snapshots_created,
        skipped_no_venue=report.skipped_no_venue, skipped_no_coordinates=report.skipped_no_coordinates,
        skipped_too_far_out=report.skipped_too_far_out, errors=report.errors,
    )


@router.get("/matches/{match_id}/weather-diagnostic", response_model=WeatherDiagnosticRead)
def get_weather_diagnostic(match_id: int, db: Session = Depends(get_db)) -> WeatherDiagnosticRead:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    expected_total = None
    try:
        context = build_model_context(db)
        expected_total = compute_match_predictions(match, context).poisson_expected_total_points
    except ModelsUnavailableError:
        pass
    diagnostic = weather_model_diagnostic(db, match_id, expected_total_points=expected_total)
    return weather_diagnostic_as_dict(diagnostic)


@router.get("/context-dashboard", response_model=RoundContextDashboardRead)
def get_context_dashboard(db: Session = Depends(get_db)) -> RoundContextDashboardRead:
    """Section 17: pre-bet checklist for the current round — every
    upcoming match's confirmed-lineup state, major outs/late changes,
    weather, and how many player projections are currently stale."""
    upcoming = load_next_upcoming_round(db)
    if not upcoming:
        return RoundContextDashboardRead(round_number=None, season_year=None, matches=[])

    match_ids = [m.match_id for m in upcoming]
    try:
        stale_match_ids = detect_matches_needing_regeneration(db, upcoming)
    except ModelsUnavailableError:
        stale_match_ids = set()

    stale_counts: dict[int, int] = {}
    for match_id in match_ids:
        n_disposal = len(db.scalars(select(PlayerDisposalProjection.id).where(PlayerDisposalProjection.match_id == match_id)).all())
        n_goal = len(db.scalars(select(PlayerGoalProjection.id).where(PlayerGoalProjection.match_id == match_id)).all())
        stale_counts[match_id] = (n_disposal + n_goal) if match_id in stale_match_ids else 0

    matches: list[RoundContextMatchRead] = []
    for m in upcoming:
        match = db.get(Match, m.match_id)
        current = current_context_for_match(db, m.match_id)
        n_confirmed_in = sum(1 for c in current if c.context_type == ContextType.CONFIRMED_IN.value)
        n_confirmed_out = sum(1 for c in current if c.context_type == ContextType.CONFIRMED_OUT.value)
        n_substitutes = sum(1 for c in current if c.context_type == ContextType.NAMED_SUBSTITUTE.value)
        n_other = sum(
            1 for c in current if c.context_type not in (ContextType.CONFIRMED_IN.value, ContextType.CONFIRMED_OUT.value, ContextType.NAMED_SUBSTITUTE.value)
        )
        lineup_rows = db.scalars(select(ExpectedLineup.selection_status).where(ExpectedLineup.match_id == m.match_id)).all()
        announcement_state = derive_announcement_state(list(lineup_rows))
        weather = latest_weather_for_match(db, m.match_id)

        matches.append(
            RoundContextMatchRead(
                match_id=m.match_id, round_number=m.round_number, season_year=m.season_year, scheduled_start=match.scheduled_start,
                home_team_name=match.home_team.name, away_team_name=match.away_team.name, lineup_announcement_state=announcement_state,
                n_confirmed_in=n_confirmed_in, n_confirmed_out=n_confirmed_out, n_substitutes=n_substitutes, n_other_context_items=n_other,
                weather=_weather_read(weather), n_stale_projections=stale_counts.get(m.match_id, 0),
            )
        )

    return RoundContextDashboardRead(round_number=upcoming[0].round_number, season_year=upcoming[0].season_year, matches=matches)
