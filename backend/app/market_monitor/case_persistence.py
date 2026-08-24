"""Case-level persistence + lifecycle (items 4 & 10) — reads/writes
AnomalyCaseRecord (app/models/anomaly_case_record.py; see that module's
docstring for why it's a separate table from the prior stage's
AnomalyAlertSnapshot). Every function here is a plain upsert/lookup, no
scoring logic — app/market_monitor/priority.py consumes n_snapshots to
compute the persistence score.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnomalyCaseRecord

LIFECYCLE_NEW = "new"
LIFECYCLE_PERSISTING = "persisting"
LIFECYCLE_RESOLVED = "resolved_naturally"

MANUAL_STATUSES = ("reviewed", "acknowledged", "dismissed")


def record_case_observation(db: Session, case_key: str, match_id: int, now: datetime | None = None) -> AnomalyCaseRecord:
    now = now or datetime.now(timezone.utc)
    record = db.scalar(select(AnomalyCaseRecord).where(AnomalyCaseRecord.case_key == case_key))
    if record is None:
        record = AnomalyCaseRecord(case_key=case_key, match_id=match_id, first_seen_at=now, last_seen_at=now, n_snapshots=1)
        db.add(record)
        db.flush()  # so a second call for the SAME key later in this same transaction finds it via SELECT, not a duplicate INSERT
        return record
    # A case that reappears after having been marked resolved is a fresh
    # situation, not a continuation of the old streak — restart the count
    # rather than silently inflating "persistence" across a gap where the
    # underlying issue was actually gone for a while.
    if record.resolved_at is not None:
        record.first_seen_at = now
        record.n_snapshots = 1
        record.resolved_at = None
    else:
        record.n_snapshots += 1
    record.last_seen_at = now
    return record


def resolve_stale_cases(db: Session, current_case_keys: set[str], now: datetime | None = None) -> int:
    """Any case_key with an unresolved record that did NOT appear in the
    current detection pass has naturally stopped firing — item 10's
    "resolved naturally" status, a system-computed fact, not a manual
    action."""
    now = now or datetime.now(timezone.utc)
    unresolved = db.scalars(select(AnomalyCaseRecord).where(AnomalyCaseRecord.resolved_at.is_(None))).all()
    n = 0
    for record in unresolved:
        if record.case_key not in current_case_keys:
            record.resolved_at = now
            n += 1
    return n


def lifecycle_status(record: AnomalyCaseRecord | None) -> str:
    if record is None:
        return LIFECYCLE_NEW
    if record.resolved_at is not None:
        return LIFECYCLE_RESOLVED
    return LIFECYCLE_PERSISTING if record.n_snapshots >= 2 else LIFECYCLE_NEW


def set_manual_status(db: Session, case_key: str, status: str | None) -> AnomalyCaseRecord | None:
    if status is not None and status not in MANUAL_STATUSES:
        raise ValueError(f"manual_status must be one of {MANUAL_STATUSES} or None, got {status!r}")
    record = db.scalar(select(AnomalyCaseRecord).where(AnomalyCaseRecord.case_key == case_key))
    if record is None:
        return None
    record.manual_status = status
    return record
