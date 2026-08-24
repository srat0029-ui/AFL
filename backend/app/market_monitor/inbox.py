"""Trader Inbox orchestration (item 9's data source): raw Alerts -> cases
(case_builder.py, UNCHANGED detection) -> priority score (priority.py) ->
lifecycle status (case_persistence.py), in one call. This is the only
place all four modules meet — the API/UI/verification script all call
this, never the lower-level pieces directly, so there's exactly one
definition of "how a raw detection becomes a ranked case."
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnomalyCaseRecord, Match
from app.market_monitor.common import aware
from app.market_monitor.case_builder import AnomalyCase, build_cases
from app.market_monitor.case_persistence import lifecycle_status, record_case_observation, resolve_stale_cases
from app.market_monitor.detector import detect_match_anomalies
from app.market_monitor.priority import PriorityBreakdown, compute_priority


@dataclass(frozen=True)
class RankedCase:
    case: AnomalyCase
    priority: PriorityBreakdown
    lifecycle: str
    manual_status: str | None


def build_trader_inbox(
    db: Session, match_ids: list[int], *, now: datetime | None = None, track_persistence: bool = True, full_scan: bool = False,
) -> list[RankedCase]:
    """full_scan=True means match_ids covers EVERY currently-active match
    (not a single match_id lookup) — only then is it safe to mark
    previously-tracked cases outside this result set as "resolved
    naturally" (see resolve_stale_cases). A narrow single-match call must
    never resolve unrelated cases from matches it didn't even look at."""
    now = now or datetime.now(timezone.utc)

    raw_alerts = []
    for mid in match_ids:
        raw_alerts += detect_match_anomalies(db, mid)
    cases = build_cases(raw_alerts)

    kickoff_by_match: dict[int, datetime] = {}
    if match_ids:
        for m in db.scalars(select(Match).where(Match.id.in_(match_ids))).all():
            kickoff_by_match[m.id] = aware(m.scheduled_start)

    existing_records: dict[str, AnomalyCaseRecord] = {}
    if track_persistence:
        for c in cases:
            record = record_case_observation(db, c.case_id, c.match_id, now=now)
            existing_records[c.case_id] = record
        if full_scan:
            resolve_stale_cases(db, {c.case_id for c in cases}, now=now)
        db.commit()

    ranked = []
    for c in cases:
        record = existing_records.get(c.case_id)
        n_snapshots = record.n_snapshots if record is not None else 1
        priority = compute_priority(c, kickoff=kickoff_by_match.get(c.match_id), n_snapshots=n_snapshots, now=now)
        lifecycle = lifecycle_status(record)
        ranked.append(RankedCase(case=c, priority=priority, lifecycle=lifecycle, manual_status=record.manual_status if record else None))

    ranked.sort(key=lambda r: (-r.priority.total_score, r.case.case_id))
    return ranked
