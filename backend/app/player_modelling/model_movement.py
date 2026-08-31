"""Model-side price/projection movement over time — reads
`ModelValueObservation` history (see that model's docstring for why it
exists) and reports previous/current/change for whichever identities have
at least two observations. Descriptive only, mirroring
`market_movement.py`'s own framing: a movement is a fact, not a signal
about which side is "right."

Materiality thresholds are centralized in `app.trading_monitor.thresholds`
(never redefined here) — a movement below the "notable" floor is still
returned (never hidden), just flagged `is_material=False`/`is_notable=False`
so callers decide what to surface, matching this project's "don't classify
ordinary conditions as failures just to make the dashboard look active"
principle applied to the opposite failure mode (don't hide real data to
look tidy either).
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ModelValueObservation,
    VALUE_PLAYER_DISPOSAL_PROBABILITY,
    VALUE_PLAYER_DISPOSAL_PROJECTED_MEAN,
    VALUE_PLAYER_GOAL_PROBABILITY,
    VALUE_PLAYER_GOAL_PROJECTED_MEAN,
    VALUE_TEAM_FAIR_ODDS,
    VALUE_TEAM_WIN_PROBABILITY,
)
from app.trading_monitor.thresholds import (
    DISPOSAL_PROJECTED_MEAN_MATERIAL,
    DISPOSAL_PROJECTED_MEAN_NOTABLE,
    GOAL_PROJECTED_MEAN_MATERIAL,
    GOAL_PROJECTED_MEAN_NOTABLE,
    PLAYER_PROBABILITY_MATERIAL_PP,
    PLAYER_PROBABILITY_NOTABLE_PP,
    TEAM_PROBABILITY_MATERIAL_PP,
    TEAM_PROBABILITY_NOTABLE_PP,
)

_ABSOLUTE_THRESHOLDS = {
    VALUE_TEAM_WIN_PROBABILITY: (TEAM_PROBABILITY_NOTABLE_PP, TEAM_PROBABILITY_MATERIAL_PP),
    VALUE_PLAYER_DISPOSAL_PROBABILITY: (PLAYER_PROBABILITY_NOTABLE_PP, PLAYER_PROBABILITY_MATERIAL_PP),
    VALUE_PLAYER_GOAL_PROBABILITY: (PLAYER_PROBABILITY_NOTABLE_PP, PLAYER_PROBABILITY_MATERIAL_PP),
    VALUE_PLAYER_DISPOSAL_PROJECTED_MEAN: (DISPOSAL_PROJECTED_MEAN_NOTABLE, DISPOSAL_PROJECTED_MEAN_MATERIAL),
    VALUE_PLAYER_GOAL_PROJECTED_MEAN: (GOAL_PROJECTED_MEAN_NOTABLE, GOAL_PROJECTED_MEAN_MATERIAL),
}


@dataclass(frozen=True)
class ModelMovement:
    match_id: int
    player_id: int | None
    value_type: str
    value_kind: str
    selection: str | None
    threshold: float | None
    previous_value: float
    current_value: float
    absolute_change: float
    relative_change: float | None  # None when previous_value == 0 (undefined)
    hours_between: float
    previous_recorded_at: datetime
    recorded_at: datetime
    model_name: str
    model_version: str
    lineup_status_changed: bool
    previous_lineup_status: str | None
    current_lineup_status: str | None
    is_notable: bool
    is_material: bool


def _classify(value_type: str, absolute_change: float, relative_change: float | None) -> tuple[bool, bool]:
    if value_type == VALUE_TEAM_FAIR_ODDS:
        # Deliberately never independently material: fair odds is a
        # monotonic transform of team win probability, which already has
        # its own (better-behaved, probability-space) materiality check
        # above. Fair odds movements are still computed and returned - for
        # DISPLAY alongside the probability movement - just never flagged
        # as their own separate signal, which would double-count the same
        # underlying fact and risk a threshold that misjudges short vs.
        # long prices (a fixed relative-odds cutoff doesn't correspond
        # cleanly to a fixed probability-space cutoff).
        return False, False
    notable, material = _ABSOLUTE_THRESHOLDS.get(value_type, (None, None))
    if notable is None:
        return False, False
    magnitude = abs(absolute_change)
    return magnitude >= notable, magnitude >= material


def _build_movement(previous: ModelValueObservation, current: ModelValueObservation) -> ModelMovement:
    absolute_change = current.value - previous.value
    relative_change = (absolute_change / previous.value) if previous.value != 0 else None
    hours_between = (current.recorded_at - previous.recorded_at).total_seconds() / 3600.0
    is_notable, is_material = _classify(current.value_type, absolute_change, relative_change)
    lineup_changed = previous.lineup_status != current.lineup_status
    return ModelMovement(
        match_id=current.match_id, player_id=current.player_id, value_type=current.value_type, value_kind=current.value_kind,
        selection=current.selection, threshold=current.threshold, previous_value=previous.value, current_value=current.value,
        absolute_change=absolute_change, relative_change=relative_change, hours_between=hours_between,
        previous_recorded_at=previous.recorded_at, recorded_at=current.recorded_at, model_name=current.model_name,
        model_version=current.model_version, lineup_status_changed=lineup_changed, previous_lineup_status=previous.lineup_status,
        current_lineup_status=current.lineup_status, is_notable=is_notable, is_material=is_material,
    )


def recent_model_movements(db: Session, match_ids: list[int]) -> list[ModelMovement]:
    """One movement per identity that has at least two observations,
    comparing the two most recent. Sorted by materiality then magnitude so
    the largest genuine moves surface first."""
    if not match_ids:
        return []
    rows = db.scalars(
        select(ModelValueObservation).where(ModelValueObservation.match_id.in_(match_ids)).order_by(ModelValueObservation.recorded_at)
    ).all()

    groups: dict[tuple, list[ModelValueObservation]] = {}
    for r in rows:
        key = (r.match_id, r.player_id, r.value_type, r.selection, r.threshold)
        groups.setdefault(key, []).append(r)

    movements = [_build_movement(obs[-2], obs[-1]) for obs in groups.values() if len(obs) >= 2]
    movements.sort(key=lambda m: (not m.is_material, not m.is_notable, -abs(m.absolute_change)))
    return movements
