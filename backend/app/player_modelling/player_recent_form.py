"""Player recent-form context (Weekly Opportunity Discovery stage,
Sections 13-16): the player's own actual recent results, shown ALONGSIDE
— never in place of — the model's probability (Section 13's explicit "do
not use recent results as proof that the bet will win"). Leakage-safe:
only matches strictly before the target match's kickoff are ever
included, mirroring the point-in-time discipline disposal_features.py/
goal_features.py already use for the model's own inputs.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, PlayerMatchStat

STAT_FIELD_BY_MARKET = {"player_disposals": "disposals", "player_goals": "goals"}


@dataclass(frozen=True)
class RecentFormResult:
    stat_field: str  # "disposals" | "goals"
    last5: list[int]  # chronological order, most recent last
    last10: list[int]
    last5_avg: float | None
    last10_avg: float | None


def _avg(values: list[int]) -> float | None:
    return sum(values) / len(values) if values else None


def load_recent_form(db: Session, player_id: int, before: datetime, stat_field: str, *, n: int = 10) -> RecentFormResult:
    rows = (
        db.execute(
            select(getattr(PlayerMatchStat, stat_field))
            .join(Match, Match.id == PlayerMatchStat.match_id)
            .where(
                PlayerMatchStat.player_id == player_id,
                Match.scheduled_start < before,
                getattr(PlayerMatchStat, stat_field).is_not(None),
            )
            .order_by(Match.scheduled_start.desc())
            .limit(n)
        )
        .scalars()
        .all()
    )
    values = list(reversed(rows))  # oldest-of-window first, most recent last
    last5 = values[-5:]
    return RecentFormResult(stat_field=stat_field, last5=last5, last10=values, last5_avg=_avg(last5), last10_avg=_avg(values))


def meets_threshold(value: int, threshold: float, line_type: str) -> bool:
    """over_under thresholds are conventionally X.5 (e.g. 27.5) so `value
    > threshold` and `value >= threshold` agree; multi_plus thresholds are
    whole numbers (e.g. 2+) where `>=` is the only correct reading."""
    if line_type == "multi_plus":
        return value >= threshold
    return value > threshold


def hit_rate_description(values: list[int], threshold: float, line_type: str) -> str:
    if not values:
        return "No recent history available."
    hits = sum(1 for v in values if meets_threshold(v, threshold, line_type))
    threshold_display = f"{threshold:g}"
    return f"{threshold_display}+ in {hits} of last {len(values)} games"


def hit_rate(values: list[int], threshold: float, line_type: str) -> float | None:
    if not values:
        return None
    return sum(1 for v in values if meets_threshold(v, threshold, line_type)) / len(values)


# A gap this large between the model's probability for this exact line
# and the player's simple recent hit rate for the same line is treated as
# a genuine disagreement worth flagging (Section 15) - not a claim about
# which one is "right".
_FORM_DISAGREEMENT_THRESHOLD_PP = 0.15


def form_disagreement_label(model_probability: float, recent_hit_rate: float | None) -> str | None:
    if recent_hit_rate is None:
        return None
    gap = recent_hit_rate - model_probability
    if gap >= _FORM_DISAGREEMENT_THRESHOLD_PP:
        return "Recent hot streak exceeds model expectation"
    if gap <= -_FORM_DISAGREEMENT_THRESHOLD_PP:
        return "Model probability substantially above recent hit rate"
    return None


# Section 16: if the model's raw projected mean sits at least this far
# below MULTIPLE of the player's own recent-form baselines, flag it as
# diagnostic-only - never used to adjust the probability itself.
_CONSERVATIVE_MODEL_GAP = 3.0
_CONSERVATIVE_MODEL_MIN_BASELINES_EXCEEDED = 2


def conservative_model_flag(predicted_mean: float, baselines: dict[str, float | None], *, gap: float = _CONSERVATIVE_MODEL_GAP) -> str | None:
    exceeded = [name for name, baseline in baselines.items() if baseline is not None and baseline - predicted_mean > gap]
    if len(exceeded) >= _CONSERVATIVE_MODEL_MIN_BASELINES_EXCEEDED:
        return "Model is conservative relative to recent form"
    return None
