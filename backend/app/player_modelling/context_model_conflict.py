"""Model-context conflict detection (Current Context + Team News
Intelligence stage, Sections 6, 13-14) — structured, named codes flagging
when current context MAY make a model output less trustworthy. Never
adjusts a probability or projection value (Section 15's explicit
instruction): this module only produces labels for the evidence/caution
system to display next to the unchanged model output.

MODEL_PREDATES_CONTEXT is derived differently per opportunity type,
because the two model families have genuinely different blind spots:

- Player disposal/goal projections are STORED rows with their own
  `generated_at` timestamp (see player_projection.py) — a real staleness
  check compares that timestamp against the newest relevant context
  item's own timestamp, the same "was this true when the number was
  computed" question live_staleness.py already asks for lineup/model-
  version changes.
- Team h2h/line/total predictions are computed LIVE from the current
  Elo/Poisson ratings on every request (see edges/calculator.py) — there
  is no stored generation time to compare against, and more importantly
  neither model has ANY feature for lineup composition or weather at all,
  at any point in time. So for team opportunities, the presence of a
  current lineup-affecting or severe-weather context item is itself
  sufficient: the model provably cannot see it, regardless of when it was
  computed.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ContextType, PlayerDisposalProjection, PlayerGoalProjection
from app.player_modelling.match_context_service import current_context_for_match
from app.player_modelling.weather_ingestion import SEVERE_RAIN_PROBABILITY_PCT, SEVERE_WIND_GUST_KPH, latest_weather_for_match

CTX_PLAYER_CONFIRMED_OUT = "PLAYER_CONFIRMED_OUT"
CTX_PLAYER_SUBSTITUTE = "PLAYER_SUBSTITUTE"
CTX_PLAYER_RETURNING_FROM_INJURY = "PLAYER_RETURNING_FROM_INJURY"
CTX_KEY_TEAM_OUT = "KEY_TEAM_OUT"
CTX_LINEUP_CHANGED = "LINEUP_CHANGED"
CTX_WEATHER_RAIN = "WEATHER_RAIN"
CTX_WEATHER_HIGH_WIND = "WEATHER_HIGH_WIND"
CTX_MODEL_PREDATES_CONTEXT = "MODEL_PREDATES_CONTEXT"

CONTEXT_REASON_LABELS: dict[str, str] = {
    CTX_PLAYER_CONFIRMED_OUT: "Player confirmed out",
    CTX_PLAYER_SUBSTITUTE: "Named as substitute",
    CTX_PLAYER_RETURNING_FROM_INJURY: "Player returning from injury/absence",
    CTX_KEY_TEAM_OUT: "A player relevant to this team is confirmed out",
    CTX_LINEUP_CHANGED: "Team lineup has changed recently",
    CTX_WEATHER_RAIN: "High rain probability forecast",
    CTX_WEATHER_HIGH_WIND: "High wind forecast",
    CTX_MODEL_PREDATES_CONTEXT: "Model may not fully reflect latest context",
}

_LINEUP_AFFECTING_TYPES = frozenset(
    {
        ContextType.CONFIRMED_IN.value,
        ContextType.CONFIRMED_OUT.value,
        ContextType.LATE_WITHDRAWAL.value,
        ContextType.NAMED_SUBSTITUTE.value,
        ContextType.EMERGENCY.value,
        ContextType.MAJOR_ROLE_CHANGE.value,
    }
)


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class ContextConflictResult:
    codes: list[str]
    labels: list[str]
    latest_context_at: datetime | None
    model_generated_at: datetime | None  # None for team markets - always computed live, no stored generation time


def detect_context_conflicts(db: Session, opportunity: dict) -> ContextConflictResult:
    match_id = opportunity["match_id"]
    team_id = opportunity.get("team_id")
    player_id = opportunity.get("player_id")

    all_current = current_context_for_match(db, match_id)
    codes: list[str] = []

    if player_id is not None:
        for item in all_current:
            if item.player_id != player_id:
                continue
            if item.context_type == ContextType.CONFIRMED_OUT.value:
                codes.append(CTX_PLAYER_CONFIRMED_OUT)
            elif item.context_type == ContextType.NAMED_SUBSTITUTE.value:
                codes.append(CTX_PLAYER_SUBSTITUTE)
            elif item.context_type == ContextType.RETURNING_PLAYER.value:
                codes.append(CTX_PLAYER_RETURNING_FROM_INJURY)

    # Any lineup-affecting item about this opportunity's own team (team-wide
    # or player-specific) — team markets care about ANY player on the team;
    # a player opportunity also picks up its own player's item here again,
    # which is fine (it's deduped below).
    team_lineup_items = [
        item
        for item in all_current
        if item.context_type in _LINEUP_AFFECTING_TYPES
        and ((team_id is not None and item.team_id == team_id) or (player_id is not None and item.player_id == player_id))
    ]
    if team_lineup_items:
        if any(item.context_type == ContextType.CONFIRMED_OUT.value for item in team_lineup_items):
            codes.append(CTX_KEY_TEAM_OUT)
        codes.append(CTX_LINEUP_CHANGED)

    weather_flags: list[str] = []
    weather = latest_weather_for_match(db, match_id)
    if weather is not None:
        if weather.rain_probability_pct is not None and weather.rain_probability_pct >= SEVERE_RAIN_PROBABILITY_PCT:
            weather_flags.append(CTX_WEATHER_RAIN)
        if weather.wind_gust_kph is not None and weather.wind_gust_kph >= SEVERE_WIND_GUST_KPH:
            weather_flags.append(CTX_WEATHER_HIGH_WIND)
    codes.extend(weather_flags)

    model_generated_at: datetime | None = None
    predates_context = False

    if player_id is not None:
        projection = None
        if opportunity.get("market_type") == "player_disposals":
            projection = db.scalar(
                select(PlayerDisposalProjection).where(PlayerDisposalProjection.match_id == match_id, PlayerDisposalProjection.player_id == player_id)
            )
        elif opportunity.get("market_type") == "player_goals":
            projection = db.scalar(
                select(PlayerGoalProjection).where(PlayerGoalProjection.match_id == match_id, PlayerGoalProjection.player_id == player_id)
            )
        if projection is not None:
            model_generated_at = projection.generated_at
            relevant = [i for i in all_current if i.player_id == player_id]
            for item in relevant:
                ts = _aware(item.source_timestamp or item.recorded_at)
                if ts > _aware(model_generated_at):
                    predates_context = True
                    break
    else:
        # Team markets: Elo/Poisson have no lineup or weather feature at
        # all, at any generation time - presence alone is sufficient.
        predates_context = bool(team_lineup_items) or bool(weather_flags)

    if predates_context:
        codes.append(CTX_MODEL_PREDATES_CONTEXT)

    seen: set[str] = set()
    unique_codes = [c for c in codes if not (c in seen or seen.add(c))]
    labels = [CONTEXT_REASON_LABELS[c] for c in unique_codes]

    relevant_for_timestamp = list(team_lineup_items)
    if player_id is not None:
        relevant_for_timestamp += [i for i in all_current if i.player_id == player_id]
    latest_context_at = None
    timestamps = [_aware(i.source_timestamp or i.recorded_at) for i in relevant_for_timestamp]
    if timestamps:
        latest_context_at = max(timestamps)
    if weather is not None:
        weather_ts = _aware(weather.fetched_at)
        latest_context_at = weather_ts if latest_context_at is None else max(latest_context_at, weather_ts)

    return ContextConflictResult(codes=unique_codes, labels=labels, latest_context_at=latest_context_at, model_generated_at=model_generated_at)


def context_conflict_as_dict(r: ContextConflictResult) -> dict:
    return {
        "codes": r.codes,
        "labels": r.labels,
        "latest_context_at": r.latest_context_at,
        "model_generated_at": r.model_generated_at,
    }
