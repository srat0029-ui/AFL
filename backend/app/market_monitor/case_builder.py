"""Groups raw Alerts (app.market_monitor.detector, UNCHANGED — no new
anomaly types, no detection-logic changes) into `AnomalyCase`s: item 2's
deduplication layer. Detection stays exactly as validated last stage; this
module only reorganises its OUTPUT so a trading desk sees "one market
situation, N supporting alerts" instead of N unrelated rows.

Grouping key is deliberately the MARKET identity, not the alert type: every
alert type that can fire about the exact same (match, player/team, market,
selection, threshold, line) is describing the same underlying situation
(item 2's own example — divergence + dispersion + outlier on one
threshold), so they collapse into one case with the others attached as
"supporting evidence." A per-bookmaker movement alert on that same market
still belongs to the same case — its bookmaker just joins the case's
bookmaker list.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from app.market_monitor.types import Alert, SEVERITY_CRITICAL, SEVERITY_WARNING

CaseKey = tuple

# Deterministic primary-alert choice (item 12/13: "deterministic
# prioritisation") - ties within the same severity are broken by this fixed
# type order, most-diagnostic-first, not insertion order.
_TYPE_PRIORITY = (
    "MODEL_VS_MARKET_DIVERGENCE",
    "TEAM_MARKET_INTERNAL_INCONSISTENCY",
    "BOOKMAKER_VS_CONSENSUS_OUTLIER",
    "NON_MONOTONIC_PLAYER_PRICE_CURVE",
    "ADJACENT_THRESHOLD_JUMP",
    "LARGE_MARKET_DISPERSION",
    "STALE_AFTER_LINEUP_CHANGE",
    "STALE_AFTER_CONTEXT_CHANGE",
    "SHARP_MARKET_MOVE_MODEL_STABLE",
    "BOOKMAKER_MOVED_VS_STABLE_CONSENSUS",
    "CONSENSUS_MOVED_VS_STALE_BOOKMAKER",
)
_SEVERITY_RANK = {SEVERITY_CRITICAL: 0, SEVERITY_WARNING: 1, "info": 2}


def case_key(alert: Alert) -> CaseKey:
    return (alert.match_id, alert.player_id, alert.team_id, alert.market_type, alert.selection, alert.threshold, alert.line_value)


def case_key_str(key: CaseKey) -> str:
    return ":".join("" if v is None else str(v) for v in key)


@dataclass
class AnomalyCase:
    key: CaseKey
    case_id: str
    match_id: int
    home_team: str
    away_team: str
    player_id: int | None
    player_name: str | None
    team_id: int | None
    market_type: str
    selection: str | None
    threshold: float | None
    line_value: float | None

    primary_alert: Alert
    supporting_alert_types: list[str]
    alerts: list[Alert]
    bookmakers: list[str]

    first_detected: datetime
    latest_detected: datetime


def _select_primary(alerts: list[Alert]) -> Alert:
    def sort_key(a: Alert) -> tuple:
        type_rank = _TYPE_PRIORITY.index(a.alert_type) if a.alert_type in _TYPE_PRIORITY else len(_TYPE_PRIORITY)
        return (_SEVERITY_RANK.get(a.severity, 9), type_rank)

    return sorted(alerts, key=sort_key)[0]


def build_cases(alerts: list[Alert]) -> list[AnomalyCase]:
    grouped: dict[CaseKey, list[Alert]] = defaultdict(list)
    for a in alerts:
        grouped[case_key(a)].append(a)

    cases = []
    for key, group in grouped.items():
        primary = _select_primary(group)
        supporting_types = sorted({a.alert_type for a in group} - {primary.alert_type})
        bookmakers = sorted({b.bookmaker_name for a in group for b in a.bookmaker_prices})
        cases.append(
            AnomalyCase(
                key=key, case_id=case_key_str(key), match_id=primary.match_id, home_team=primary.home_team,
                away_team=primary.away_team, player_id=primary.player_id, player_name=primary.player_name,
                team_id=primary.team_id, market_type=primary.market_type, selection=primary.selection,
                threshold=primary.threshold, line_value=primary.line_value, primary_alert=primary,
                supporting_alert_types=supporting_types, alerts=group, bookmakers=bookmakers,
                first_detected=min(a.generated_at for a in group), latest_detected=max(a.generated_at for a in group),
            )
        )
    return cases
